"""Nghiệp vụ đặt vé / huỷ vé — phần trọng tâm của hệ thống.

Mọi thao tác trong một hàm chạy chung MỘT transaction do tầng API mở (get_db);
service chỉ gọi repo, không commit. Lỗi ném ra → tầng API rollback + trả status code.

Chống tạo đơn trùng khi client retry (timeout, mất mạng, bấm 2 lần): client gửi kèm
Idempotency-Key. Lần đầu tạo đơn bình thường; các lần sau với cùng key trả lại ĐÚNG
đơn đã tạo mà không trừ vé thêm lần nào.
"""
from datetime import datetime, timezone

from app.services.errors import (
    Forbidden,
    IdempotencyKeyReused,
    InvalidState,
    NotFound,
    SaleNotOpen,
    SoldOut,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class OrderService:
    def __init__(self, order_repo, ticket_type_repo, concert_repo, clock=_utcnow):
        self.order_repo = order_repo
        self.ticket_type_repo = ticket_type_repo
        self.concert_repo = concert_repo
        self.clock = clock

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
        if quantity > 10:
            raise ValueError("quantity must not exceed 10 per order")

        if idempotency_key is not None:
            existing = self.order_repo.get_by_idempotency_key(user_id, idempotency_key)
            if existing is not None:
                return self._replay(existing, ticket_type_id, quantity), False

        ticket_type = self.ticket_type_repo.get_by_id(ticket_type_id)
        if ticket_type is None:
            raise NotFound(f"ticket_type {ticket_type_id}")

        concert = self.concert_repo.get_by_id(ticket_type.concert_id)
        if self.clock() < concert.sale_open_time:
            raise SaleNotOpen(concert.sale_open_time.isoformat())

        if idempotency_key is None:
            order = None
        else:
            # Giành key TRƯỚC khi trừ vé: nếu request trùng key khác đã thắng thì
            # dừng ở đây, kho vé không bị đụng tới.
            order = self.order_repo.create_idempotent(user_id, ticket_type_id, quantity, idempotency_key)
            if order is None:
                existing = self.order_repo.get_by_idempotency_key(user_id, idempotency_key)
                return self._replay(existing, ticket_type_id, quantity), False

        # Hết vé → ném lỗi → transaction rollback, đơn vừa chèn (nếu có) cũng bị huỷ.
        if not self.ticket_type_repo.reserve(ticket_type_id, quantity):
            raise SoldOut(f"ticket_type {ticket_type_id}")

        if order is None:
            order = self.order_repo.create(user_id=user_id, ticket_type_id=ticket_type_id, quantity=quantity)
        return order, True

    @staticmethod
    def _replay(order, ticket_type_id: int, quantity: int):
        if order.ticket_type_id != ticket_type_id or order.quantity != quantity:
            raise IdempotencyKeyReused(
                "Idempotency-Key was already used for a different order request"
            )
        return order

    def cancel_order(self, user_id: int, order_id: int):
        order = self.order_repo.get_by_id(order_id)
        if order is None:
            raise NotFound(f"order {order_id}")
        if order.user_id != user_id:
            raise Forbidden("not your order")
        if order.status != "confirmed":
            raise InvalidState(f"order already {order.status}")

        self.ticket_type_repo.release(order.ticket_type_id, order.quantity)
        return self.order_repo.set_status(order, "cancelled")

    def list_my_orders(self, user_id: int):
        return self.order_repo.list_by_user(user_id)
