"""Đọc concert và hạng vé trong một Unit of Work ngắn."""
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone

from app.services.concert_data import ConcertData, TicketTypeData
from app.services.errors import NotFound
from app.services.unit_of_work import ConcertRecord, Concerts, UnitOfWork


class ConcertService:
    def __init__(self, uow_factory: Callable[[], UnitOfWork], *,
                 protect: Callable[[], AbstractContextManager[None]]):
        self.uow_factory = uow_factory
        self.protect = protect

    def list_concerts(self, on_sale: bool = False) -> list[ConcertData]:
        with self.protect(), self.uow_factory() as uow:
            if on_sale:
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                concerts = uow.concerts.list_on_sale(now)
            else:
                concerts = uow.concerts.list_all()
            return [ConcertData.from_concert(concert) for concert in concerts]

    @staticmethod
    def _find_concert(concerts: Concerts, concert_id: int) -> ConcertRecord:
        concert = concerts.get_by_id(concert_id)
        if concert is None:
            raise NotFound(f"concert {concert_id}")
        return concert

    def get_concert(self, concert_id: int) -> ConcertData:
        with self.protect(), self.uow_factory() as uow:
            return ConcertData.from_concert(self._find_concert(uow.concerts, concert_id))

    def list_ticket_types(self, concert_id: int) -> list[TicketTypeData]:
        with self.protect(), self.uow_factory() as uow:
            self._find_concert(uow.concerts, concert_id)
            return [TicketTypeData.from_ticket_type(ticket_type)
                    for ticket_type in uow.ticket_types.list_by_concert(concert_id)]
