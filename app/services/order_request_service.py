"""Đặt vé bất đồng bộ qua hàng đợi.

Áp dụng các pattern trong Azure Architecture Center:

- Asynchronous Request-Reply: `submit` chỉ kiểm tra nhanh rồi xếp yêu cầu vào hàng đợi;
  API trả ngay 202 kèm địa chỉ trạng thái. Client hỏi `get` cho đến khi có kết quả, nên
  request HTTP không còn phải chờ phần việc tranh chấp nhất (khóa user, trừ kho, gán vé)
  và không còn tình huống "timeout rồi không biết đơn đã tạo hay chưa".
- Queue-Based Load Leveling: lúc mở bán, hàng nghìn request chỉ chèn một dòng vào
  `order_requests`; số worker cố định mới là thứ chạm vào dòng tồn kho nóng, nên tải
  lên DB được san phẳng thay vì dồn hết vào một lúc.
- Competing Consumers: nhiều worker (luồng hoặc tiến trình) cùng gọi `process_next`;
  SKIP LOCKED chia việc, mỗi yêu cầu được đánh dấu xong trong CÙNG transaction tạo đơn.
- Retry (kèm idempotency): client gửi lại cùng Idempotency-Key nhận lại đúng yêu cầu cũ;
  lỗi DB tạm thời được `execute` chạy lại từ đầu; yêu cầu làm worker lỗi lặp lại bị
  loại sau `max_attempts` lần (poison message) để không chặn hàng đợi.
"""
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from app.services.errors import BusinessError, Forbidden, IdempotencyKeyReused, NotFound, SoldOut
from app.services.infrastructure_errors import DatabaseUnavailable
from app.services.order_data import OrderData
from app.services.order_service import OrderService, check_sale_window
from app.services.unit_of_work import OrderRequestRecord, UnitOfWork

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class OrderRequestData:
    id: int
    user_id: int
    ticket_type_id: int
    quantity: int
    status: str  # pending | succeeded | failed
    order_id: int | None
    error: str | None
    detail: str | None
    created_at: datetime
    completed_at: datetime | None

    @classmethod
    def from_record(cls, request: OrderRequestRecord) -> "OrderRequestData":
        return cls(
            id=request.id, user_id=request.user_id, ticket_type_id=request.ticket_type_id,
            quantity=request.quantity, status=request.status, order_id=request.order_id,
            error=request.error, detail=request.detail, created_at=request.created_at,
            completed_at=request.completed_at,
        )


def order_key(request_id: int) -> str:
    """Khóa idempotency của đơn sinh từ một yêu cầu: dù yêu cầu bị xử lý lại (worker
    chết sau commit, hai worker trên SQLite lấy trùng) cũng chỉ có một đơn."""
    return f"request:{request_id}"


class OrderRequestService:
    def __init__(self, uow_factory: Callable[[], UnitOfWork], orders: OrderService,
                 clock=_utcnow, execute=None, *, max_attempts: int = 3,
                 on_submitted: Callable[[], None] | None = None, waiting_room=None):
        self.uow_factory = uow_factory
        self.orders = orders
        self.clock = clock
        self.execute = execute or (lambda operation: operation())
        self.max_attempts = max_attempts
        # Báo worker trong cùng tiến trình có việc mới (không bắt buộc: worker vẫn tự hỏi định kỳ).
        self.on_submitted = on_submitted
        # WaitingRoomService (nếu có): trong đợt mở bán, yêu cầu phải kèm pass hợp lệ.
        self.waiting_room = waiting_room

    # --- Phía client: gửi yêu cầu, hỏi trạng thái ----------------------------

    def submit(self, user_id: int, ticket_type_id: int, quantity: int,
               idempotency_key: str | None = None,
               waiting_room_pass: str | None = None) -> tuple[OrderRequestData, bool]:
        """Xếp yêu cầu đặt vé vào hàng đợi. Trả về (yêu cầu, created).

        created=False: đây là lần gửi lại cùng Idempotency-Key, trả về yêu cầu cũ (dù nó
        đang chờ, đã thành công hay đã thất bại) — không xếp thêm.
        Lỗi chắc chắn thấy được ngay (không có hạng vé, ngoài giờ bán, đã hết vé) được
        ném ra luôn, không lưu gì, để client sửa và gửi lại mà không phải hỏi trạng thái.
        """
        if quantity <= 0:
            raise ValueError("quantity must be greater than 0")
        if quantity > self.orders.max_per_order:
            raise ValueError(f"quantity must not exceed {self.orders.max_per_order} per order")

        result = self.execute(lambda: self._submit(user_id, ticket_type_id, quantity, idempotency_key,
                                                   waiting_room_pass))
        if result[1] and self.on_submitted is not None:
            self.on_submitted()
        return result

    def _submit(self, user_id, ticket_type_id, quantity, idempotency_key, waiting_room_pass=None):
        with self.uow_factory() as uow:
            if idempotency_key is not None:
                existing = uow.order_requests.get_by_key(user_id, idempotency_key)
                if existing is not None:
                    return self._replay(existing, ticket_type_id, quantity), False

            ticket_type = uow.ticket_types.get_by_id(ticket_type_id)
            if ticket_type is None:
                raise NotFound(f"ticket_type {ticket_type_id}")
            concert = uow.concerts.get_by_id(ticket_type.concert_id)
            now = self.clock()
            check_sale_window(concert, now)
            if self.waiting_room is not None:
                # Gửi lại cùng Idempotency-Key đã trả kết quả ở trên, không cần pass còn hạn.
                self.waiting_room.verify_pass(waiting_room_pass, user_id, concert, now)
            # Đọc không khóa, chỉ để từ chối sớm khi đã hết: worker mới là nơi trừ kho thật.
            if ticket_type.remaining < quantity:
                raise SoldOut(f"ticket_type {ticket_type_id}")

            request = uow.order_requests.create(user_id=user_id, ticket_type_id=ticket_type_id,
                                                quantity=quantity, idempotency_key=idempotency_key)
            if request is None:  # lần gửi trùng khóa khác vừa thắng
                existing = uow.order_requests.get_by_key(user_id, idempotency_key)
                return self._replay(existing, ticket_type_id, quantity), False
            result = OrderRequestData.from_record(request)
            uow.commit()
        return result, True

    @staticmethod
    def _replay(request, ticket_type_id: int, quantity: int) -> OrderRequestData:
        if request.ticket_type_id != ticket_type_id or request.quantity != quantity:
            raise IdempotencyKeyReused("Idempotency-Key was already used for a different order request")
        return OrderRequestData.from_record(request)

    def get(self, user_id: int, request_id: int) -> tuple[OrderRequestData, OrderData | None]:
        """Trạng thái yêu cầu; kèm đơn đã tạo khi yêu cầu thành công."""
        return self.execute(lambda: self._get(user_id, request_id))

    def _get(self, user_id, request_id):
        with self.uow_factory() as uow:
            request = uow.order_requests.get_by_id(request_id)
            if request is None:
                raise NotFound(f"order request {request_id}")
            if request.user_id != user_id:
                raise Forbidden("not your order request")
            order = None
            if request.order_id is not None:
                order = OrderData.from_order(uow.orders.get_by_id(request.order_id))
            return OrderRequestData.from_record(request), order

    # --- Phía worker: xử lý hàng đợi ------------------------------------------

    def process_next(self) -> bool:
        """Xử lý MỘT yêu cầu chờ lâu nhất. Trả False khi hàng đợi rỗng.

        Ném DatabaseUnavailable khi DB không dùng được (worker tự nghỉ rồi thử lại);
        yêu cầu khi đó vẫn `pending` vì transaction đã rollback.
        """
        claimed: list[int] = []  # id yêu cầu của lần thử gần nhất, để ghi lỗi sau rollback

        def attempt() -> bool:
            claimed.clear()
            with self.uow_factory() as uow:
                request = uow.order_requests.claim_next()
                if request is None:
                    return False
                claimed.append(request.id)
                order, _ = self.orders.place_in(uow, request.user_id, request.ticket_type_id,
                                                request.quantity, order_key(request.id))
                # Đánh dấu xong cùng transaction với tạo đơn: commit cả hai hoặc không gì cả.
                # False chỉ xảy ra khi worker khác đã xong yêu cầu này trước (SQLite).
                if uow.order_requests.complete(request.id, order.id, self.clock()):
                    uow.commit()
            return True

        try:
            return self.execute(attempt)
        except DatabaseUnavailable:
            raise
        except BusinessError as exc:
            # Kết quả nghiệp vụ chắc chắn (hết vé, vượt giới hạn giữ…): transaction tạo đơn
            # đã rollback, ghi thất bại bằng một transaction riêng.
            self.execute(lambda: self._fail(claimed[0], type(exc).__name__, str(exc)))
            return True
        except Exception as exc:
            if not claimed:
                raise
            logger.exception("order_request_failed id=%s", claimed[0])
            self.execute(lambda: self._record_crash(claimed[0], f"{type(exc).__name__}: {exc}"))
            return True

    def _fail(self, request_id, error, detail):
        with self.uow_factory() as uow:
            # Có điều kiện `status = 'pending'`: nếu worker khác đã xử lý xong thì giữ kết quả đó.
            if uow.order_requests.fail(request_id, error, detail, self.clock()):
                uow.commit()

    def _record_crash(self, request_id, detail):
        with self.uow_factory() as uow:
            if uow.order_requests.record_crash(request_id, detail, self.max_attempts, self.clock()):
                uow.commit()

    def drain(self, limit: int = 10_000) -> int:
        """Xử lý đến khi hàng đợi rỗng (hoặc đủ `limit` yêu cầu). Trả về số yêu cầu đã xử lý."""
        processed = 0
        while processed < limit and self.process_next():
            processed += 1
        return processed
