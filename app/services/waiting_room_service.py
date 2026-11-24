"""Virtual Waiting Room cho đợt mở bán: điều tiết SỐ NGƯỜI được vào luồng mua vé.

Trong WAITING_ROOM_MINUTES phút đầu sau giờ mở bán của mỗi concert, muốn đặt mua thì phải
có thẻ vào cửa (pass). Người dùng vào hàng chờ (được phép từ WAITING_ROOM_LEAD_MINUTES phút
trước giờ mở bán), hỏi vị trí định kỳ, và nhận pass khi tới lượt.

Thứ tự: hoàn toàn theo thứ tự vào hàng (FIFO, theo `id` tăng dần).
Tốc độ cho vào: lúc mở bán cho ngay `initial_batch` người, sau đó thêm `admit_per_minute`
người mỗi phút. Người ở vị trí k (đếm từ 0) được vào khi số suất đã mở > k. Vì số suất chỉ
phụ thuộc thời gian, không cần tiến trình nền nào "gọi tên", và mọi tiến trình API tính
ra cùng một kết quả.

Pass là chuỗi ký (JWT HS256) chứa user, concert và hạn dùng, nên `POST /orders` kiểm tra
được mà không cần truy vấn DB. Pass hết hạn sau `pass_minutes` kể từ lúc người dùng lần đầu
thấy mình được vào; hết hạn thì vào hàng lại từ cuối.
"""
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt

from app.services.errors import NotFound, WaitingRoomRequired
from app.services.unit_of_work import UnitOfWork

_PASS_TYPE = "waiting_room_pass"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class WaitingRoomStatus:
    """inactive: không cần xếp hàng, đặt mua bình thường.
    not_open: chưa tới lúc được vào hàng (`opens_at` là lúc bắt đầu cho xếp hàng).
    not_joined: chưa vào hàng (chỉ trả từ GET).
    waiting: đang chờ; `position` là số thứ tự (1 = người kế tiếp được vào).
    admitted: được vào; gửi `pass_token` trong header X-Waiting-Room-Pass khi đặt mua.
    expired: pass đã hết hạn; gọi vào hàng lại (POST) để xếp cuối hàng.
    """
    status: str
    position: int | None = None
    estimated_wait_seconds: int | None = None
    pass_token: str | None = None
    pass_expires_at: datetime | None = None
    opens_at: datetime | None = None
    retry_after_seconds: int | None = None


class WaitingRoomService:
    def __init__(self, uow_factory: Callable[[], UnitOfWork], secret: str, clock=_utcnow,
                 execute=None, *, window_minutes: float = 30, lead_minutes: float = 15,
                 initial_batch: int = 100, admit_per_minute: float = 100,
                 pass_minutes: float = 10):
        self.uow_factory = uow_factory
        self.secret = secret
        self.clock = clock
        self.execute = execute or (lambda operation: operation())
        self.window = timedelta(minutes=window_minutes)
        self.lead = timedelta(minutes=lead_minutes)
        self.initial_batch = initial_batch
        self.admit_per_minute = admit_per_minute
        self.pass_duration = timedelta(minutes=pass_minutes)

    # --- Quy tắc thời gian ---------------------------------------------------

    def requires_pass(self, concert, now: datetime) -> bool:
        """Đang trong đợt mở bán có waiting room: đặt mua phải có pass."""
        return self.window > timedelta(0) and concert.sale_open_time <= now < concert.sale_open_time + self.window

    def _can_queue(self, concert, now: datetime) -> bool:
        return self.window > timedelta(0) and \
            concert.sale_open_time - self.lead <= now < concert.sale_open_time + self.window

    def admitted_count(self, concert, now: datetime) -> int:
        """Số suất đã mở tính tới `now` (người ở vị trí < số này được vào)."""
        if now < concert.sale_open_time:
            return 0
        minutes = (now - concert.sale_open_time).total_seconds() / 60
        return self.initial_batch + math.floor(self.admit_per_minute * minutes)

    # --- Pass (thẻ vào cửa) ---------------------------------------------------

    def _issue_pass(self, user_id: int, concert_id: int, expires_at: datetime) -> str:
        claims = {"typ": _PASS_TYPE, "sub": str(user_id), "concert": concert_id,
                  "exp": expires_at.replace(tzinfo=timezone.utc)}
        return jwt.encode(claims, self.secret, algorithm="HS256")

    def verify_pass(self, token: str | None, user_id: int, concert, now: datetime) -> None:
        """Ném WaitingRoomRequired nếu đang có waiting room mà pass thiếu/sai/hết hạn."""
        if not self.requires_pass(concert, now):
            return
        if not token:
            raise WaitingRoomRequired("waiting room pass required")
        try:
            # Tự so hạn theo đồng hồ của service (test được) thay vì đồng hồ thật của PyJWT.
            claims = jwt.decode(token, self.secret, algorithms=["HS256"], options={"verify_exp": False})
        except jwt.PyJWTError:
            raise WaitingRoomRequired("invalid waiting room pass")
        if (claims.get("typ") != _PASS_TYPE or claims.get("sub") != str(user_id)
                or claims.get("concert") != concert.id
                or datetime.fromtimestamp(claims.get("exp", 0), timezone.utc).replace(tzinfo=None) <= now):
            raise WaitingRoomRequired("invalid or expired waiting room pass")

    # --- Vào hàng / hỏi vị trí ------------------------------------------------

    def join(self, user_id: int, concert_id: int) -> WaitingRoomStatus:
        """Vào hàng chờ (gọi lại nhiều lần vẫn giữ chỗ cũ). Pass đã hết hạn thì xếp lại cuối hàng."""
        return self.execute(lambda: self._status(user_id, concert_id, join=True))

    def status(self, user_id: int, concert_id: int) -> WaitingRoomStatus:
        """Hỏi vị trí hiện tại mà không vào hàng."""
        return self.execute(lambda: self._status(user_id, concert_id, join=False))

    def _status(self, user_id: int, concert_id: int, join: bool) -> WaitingRoomStatus:
        now = self.clock()
        with self.uow_factory() as uow:
            concert = uow.concerts.get_by_id(concert_id)
            if concert is None:
                raise NotFound(f"concert {concert_id}")
            sale_open = concert.sale_open_time  # đọc trước commit: commit làm hết hạn đối tượng ORM
            if not self._can_queue(concert, now):
                if now < concert.sale_open_time - self.lead and self.window > timedelta(0):
                    opens_at = concert.sale_open_time - self.lead
                    return WaitingRoomStatus("not_open", opens_at=opens_at,
                                             retry_after_seconds=self._retry_after((opens_at - now).total_seconds()))
                return WaitingRoomStatus("inactive")

            entry = uow.waiting_room.get(concert_id, user_id)
            if entry is None:
                if not join:
                    return WaitingRoomStatus("not_joined")
                entry = uow.waiting_room.join(concert_id, user_id, now)
            elif join and entry.admitted_at is not None and entry.admitted_at + self.pass_duration <= now:
                entry = uow.waiting_room.requeue(entry, now)

            ahead = uow.waiting_room.count_ahead(concert_id, entry.id)
            admitted = self.admitted_count(concert, now)
            if ahead < admitted:
                if entry.admitted_at is None:
                    uow.waiting_room.mark_admitted(entry.id, now)
                    admitted_at = now
                else:
                    admitted_at = entry.admitted_at
                expires_at = min(admitted_at + self.pass_duration, concert.sale_open_time + self.window)
                uow.commit()
                if expires_at <= now:
                    return WaitingRoomStatus("expired")
                return WaitingRoomStatus("admitted", pass_token=self._issue_pass(user_id, concert_id, expires_at),
                                         pass_expires_at=expires_at)
            uow.commit()

        position = ahead - admitted + 1
        wait = self._estimated_wait(sale_open, now, ahead)
        return WaitingRoomStatus("waiting", position=position, estimated_wait_seconds=wait,
                                 retry_after_seconds=self._retry_after(wait))

    def _estimated_wait(self, sale_open: datetime, now: datetime, ahead: int) -> int:
        """Thời gian tới khi số suất vượt vị trí `ahead` (tính từ 0)."""
        if self.admit_per_minute <= 0:
            seconds = (sale_open + self.window - now).total_seconds()
        else:
            minutes_needed = max(0, ahead + 1 - self.initial_batch) / self.admit_per_minute
            seconds = (sale_open - now).total_seconds() + minutes_needed * 60
        return max(1, math.ceil(seconds))

    @staticmethod
    def _retry_after(wait_seconds: float) -> int:
        """Người còn xa thì hỏi thưa hơn: giảm tải cho chính waiting room."""
        return int(min(15, max(2, wait_seconds / 4)))
