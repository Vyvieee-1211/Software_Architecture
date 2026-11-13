"""Nghiệp vụ giữ chỗ / hủy / hết hạn — tự xác định ranh giới transaction thông qua UoW.

Đặt vé không còn xác nhận ngay: đơn được tạo ở trạng thái `held`, vé bị trừ tạm khỏi
kho và giữ trong HOLD_MINUTES. Sau đó đơn chuyển sang đúng MỘT trong các trạng thái:
`paid` (PaymentService chốt khi cổng thanh toán báo thành công), `failed`, `cancelled`
(khách tự hủy) hoặc `expired` (quá hạn). Ba trạng thái sau đều hoàn vé về kho.

Chống tạo đơn trùng khi client retry (timeout, mất mạng, bấm 2 lần): client gửi kèm
Idempotency-Key. Lần đầu tạo đơn bình thường; các lần sau với cùng key trả lại ĐÚNG
đơn đã tạo mà không trừ vé thêm lần nào.
"""
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from app.services.errors import (
    Forbidden,
    HoldLimitExceeded,
    IdempotencyKeyReused,
    InvalidState,
    NotFound,
    SaleClosed,
    SaleNotOpen,
    SoldOut,
)
from app.services.order_data import OrderData, TicketData
from app.services.unit_of_work import UnitOfWork


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def release_hold(uow: UnitOfWork, order) -> None:
    """Hoàn kho cho một đơn VỪA rời trạng thái `held` trong chính transaction này.

    Chỉ gọi sau khi câu UPDATE chuyển trạng thái có điều kiện đã đổi đúng một dòng;
    đó là điều bảo đảm mỗi đơn chỉ được hoàn kho một lần.
    """
    uow.tickets.release(order.id)
    uow.ticket_types.release(order.ticket_type_id, order.quantity)


class OrderService:
    def __init__(self, uow_factory: Callable[[], UnitOfWork], clock=_utcnow, execute=None, *,
                 hold_minutes: float = 12, max_per_order: int = 10, max_held_per_user: int = 10):
        self.uow_factory = uow_factory
        self.clock = clock
        # Infrastructure may inject retries without coupling services to SQLAlchemy.
        self.execute = execute or (lambda operation: operation())
        self.hold_duration = timedelta(minutes=hold_minutes)
        self.max_per_order = max_per_order
        self.max_held_per_user = max_held_per_user

    # --- Giữ chỗ -----------------------------------------------------------

    def create_order(self, user_id: int, ticket_type_id: int, quantity: int,
                     idempotency_key: str | None = None):
        """Trả về đơn hàng. Xem place_order nếu cần biết đơn có phải vừa được tạo."""
        order, _created = self.place_order(user_id, ticket_type_id, quantity, idempotency_key)
        return order

    def place_order(self, user_id: int, ticket_type_id: int, quantity: int,
                    idempotency_key: str | None = None):
        """Trả về (order, created). created=False nghĩa là trả lại đơn của lần gửi trước."""
        if quantity <= 0:
            raise ValueError("quantity must be greater than 0")
        if quantity > self.max_per_order:
            raise ValueError(f"quantity must not exceed {self.max_per_order} per order")

        return self.execute(
            lambda: self._place_order(user_id, ticket_type_id, quantity, idempotency_key)
        )

    def _place_order(self, user_id, ticket_type_id, quantity, idempotency_key):
        with self.uow_factory() as uow:
            # Khóa dòng user: các lần đặt của CÙNG một người chạy tuần tự, nên giới hạn
            # vé đang giữ không thể bị lách bằng nhiều request song song, và request
            # retry cùng Idempotency-Key luôn thấy đơn của lần gửi trước.
            if not uow.users.lock(user_id):
                raise NotFound(f"user {user_id}")

            if idempotency_key is not None:
                existing = uow.orders.get_by_idempotency_key(user_id, idempotency_key)
                if existing is not None:
                    return self._replay(existing, ticket_type_id, quantity), False

            ticket_type = uow.ticket_types.get_by_id(ticket_type_id)
            if ticket_type is None:
                raise NotFound(f"ticket_type {ticket_type_id}")

            concert = uow.concerts.get_by_id(ticket_type.concert_id)
            now = self.clock()
            if now < concert.sale_open_time:
                raise SaleNotOpen(concert.sale_open_time.isoformat())
            if now >= concert.start_time:
                raise SaleClosed(concert.start_time.isoformat())

            held = uow.orders.held_quantity(user_id, now)
            if held + quantity > self.max_held_per_user:
                raise HoldLimitExceeded(
                    f"holding {held} tickets; at most {self.max_held_per_user} may be held at once"
                )

            fields = dict(
                user_id=user_id, ticket_type_id=ticket_type_id, quantity=quantity,
                unit_price=ticket_type.price, total_amount=ticket_type.price * quantity,
                expires_at=now + self.hold_duration,
            )
            if idempotency_key is None:
                order = None
            else:
                # Giành key TRƯỚC khi trừ vé: nếu request trùng key khác đã thắng thì
                # dừng ở đây, kho vé không bị đụng tới.
                order = uow.orders.create_idempotent(idempotency_key, **fields)
                if order is None:
                    existing = uow.orders.get_by_idempotency_key(user_id, idempotency_key)
                    return self._replay(existing, ticket_type_id, quantity), False

            # Trừ tạm bộ đếm bằng một UPDATE có điều kiện: đây là chốt chống bán vượt.
            # Hết vé → ném lỗi → UoW rollback khi thoát, đơn vừa chèn (nếu có) cũng bị hủy.
            if not uow.ticket_types.reserve(ticket_type_id, quantity):
                raise SoldOut(f"ticket_type {ticket_type_id}")

            if order is None:
                order = uow.orders.create(**fields)
            if uow.tickets.hold(ticket_type_id, order.id, quantity) is None:
                raise SoldOut(f"ticket_type {ticket_type_id}")

            result = OrderData.from_order(order)
            uow.commit()
        return result, True

    @staticmethod
    def _replay(order, ticket_type_id: int, quantity: int) -> OrderData:
        if order.ticket_type_id != ticket_type_id or order.quantity != quantity:
            raise IdempotencyKeyReused(
                "Idempotency-Key was already used for a different order request"
            )
        return OrderData.from_order(order)

    # --- Hủy giữ chỗ -------------------------------------------------------

    def cancel_order(self, user_id: int, order_id: int):
        """Hủy đơn đang giữ chỗ. Đơn đã thanh toán là đơn hoàn thành: không hủy ở đây."""
        return self.execute(lambda: self._cancel_order(user_id, order_id))

    def _cancel_order(self, user_id, order_id):
        with self.uow_factory() as uow:
            order = self._owned_order(uow, user_id, order_id)
            if order.status != "held":
                raise InvalidState(f"order already {order.status}")

            # Trạng thái đã đọc có thể cũ; DB quyết định request nào được hoàn kho.
            if not uow.orders.cancel_hold(order_id, user_id):
                raise InvalidState("order is no longer held")

            release_hold(uow, order)
            result = OrderData.from_order(order)
            uow.commit()
        return result

    # --- Hết hạn giữ chỗ ---------------------------------------------------

    def expire_holds(self, limit: int = 100) -> int:
        """Chuyển các đơn giữ chỗ quá hạn sang `expired` và hoàn kho. Trả về số đơn đã xử lý.

        Mỗi đơn một transaction riêng: một đơn lỗi không chặn các đơn còn lại, và nhiều
        tiến trình quét cùng lúc cũng an toàn vì chỉ một bên đổi được trạng thái.
        """
        order_ids = self.execute(lambda: self._expired_ids(limit))
        return sum(1 for order_id in order_ids if self.execute(lambda: self._expire(order_id)))

    def _expired_ids(self, limit):
        with self.uow_factory() as uow:
            return uow.orders.list_expired_ids(self.clock(), limit)

    def _expire(self, order_id) -> bool:
        with self.uow_factory() as uow:
            if not uow.orders.mark_expired(order_id, self.clock()):
                return False  # vừa được thanh toán / hủy / tiến trình khác xử lý
            release_hold(uow, uow.orders.get_by_id(order_id))
            uow.commit()
        return True

    # --- Tra cứu -----------------------------------------------------------

    def list_my_orders(self, user_id: int):
        return self.execute(lambda: self._list_my_orders(user_id))

    def _list_my_orders(self, user_id):
        with self.uow_factory() as uow:
            return [OrderData.from_order(order) for order in uow.orders.list_by_user(user_id)]

    def get_order(self, user_id: int, order_id: int):
        return self.execute(lambda: self._get_order(user_id, order_id))

    def _get_order(self, user_id, order_id):
        with self.uow_factory() as uow:
            return OrderData.from_order(self._owned_order(uow, user_id, order_id))

    def list_tickets(self, user_id: int, order_id: int):
        """Các vé của đơn. Mã QR chỉ có khi đơn đã thanh toán."""
        return self.execute(lambda: self._list_tickets(user_id, order_id))

    def _list_tickets(self, user_id, order_id):
        with self.uow_factory() as uow:
            self._owned_order(uow, user_id, order_id)
            return [TicketData.from_ticket(ticket) for ticket in uow.tickets.list_by_order(order_id)]

    @staticmethod
    def _owned_order(uow, user_id, order_id):
        order = uow.orders.get_by_id(order_id)
        if order is None:
            raise NotFound(f"order {order_id}")
        if order.user_id != user_id:
            raise Forbidden("not your order")
        return order
