"""Đọc concert và hạng vé trong một Unit of Work ngắn, qua cache Cache-Aside (nếu có).

Dữ liệu trả về là snapshot bất biến (ConcertData/TicketTypeData) nên dùng chung được giữa
các request và luồng. Cache chỉ phục vụ hiển thị: `remaining` có thể cũ tối đa TTL giây,
còn đặt mua (`OrderService.place_in`) luôn kiểm tra và trừ kho trực tiếp trong DB.
"""
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone

from app.services.concert_data import ConcertData, TicketTypeData
from app.services.errors import NotFound
from app.services.read_cache import ReadThroughCache
from app.services.unit_of_work import ConcertRecord, Concerts, UnitOfWork


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ConcertService:
    def __init__(self, uow_factory: Callable[[], UnitOfWork], *,
                 protect: Callable[[], AbstractContextManager[None]],
                 cache: ReadThroughCache | None = None, clock=_utcnow):
        self.uow_factory = uow_factory
        self.protect = protect
        # Không truyền cache (vd. trong test) thì mỗi lần đọc đều vào DB như trước.
        self.cache = cache or ReadThroughCache(ttl_seconds=0)
        self.clock = clock

    def list_concerts(self, on_sale: bool = False) -> list[ConcertData]:
        concerts = self.cache.get_or_load(("concerts",), self._load_concerts)
        if on_sale:
            # Lọc trên bản cache theo giờ hiện tại, để concert vừa mở bán hiện ra đúng lúc
            # mà không cần một khóa cache riêng phụ thuộc thời gian.
            now = self.clock()
            return [concert for concert in concerts if concert.sale_open_time <= now]
        return list(concerts)

    def get_concert(self, concert_id: int) -> ConcertData:
        return self.cache.get_or_load(("concert", concert_id), lambda: self._load_concert(concert_id))

    def list_ticket_types(self, concert_id: int) -> list[TicketTypeData]:
        return list(self.cache.get_or_load(("ticket_types", concert_id),
                                           lambda: self._load_ticket_types(concert_id)))

    # --- Đọc DB khi cache trống/hết hạn -------------------------------------

    @staticmethod
    def _find_concert(concerts: Concerts, concert_id: int) -> ConcertRecord:
        concert = concerts.get_by_id(concert_id)
        if concert is None:
            raise NotFound(f"concert {concert_id}")
        return concert

    def _load_concerts(self) -> tuple[ConcertData, ...]:
        with self.protect(), self.uow_factory() as uow:
            return tuple(ConcertData.from_concert(concert) for concert in uow.concerts.list_all())

    def _load_concert(self, concert_id: int) -> ConcertData:
        with self.protect(), self.uow_factory() as uow:
            return ConcertData.from_concert(self._find_concert(uow.concerts, concert_id))

    def _load_ticket_types(self, concert_id: int) -> tuple[TicketTypeData, ...]:
        with self.protect(), self.uow_factory() as uow:
            self._find_concert(uow.concerts, concert_id)
            return tuple(TicketTypeData.from_ticket_type(ticket_type)
                         for ticket_type in uow.ticket_types.list_by_concert(concert_id))
