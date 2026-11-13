"""Nghiệp vụ đặt/hủy vé tự xác định ranh giới transaction thông qua UoW.

Chống tạo đơn trùng khi client retry (timeout, mất mạng, bấm 2 lần): client gửi kèm
Idempotency-Key. Lần đầu tạo đơn bình thường; các lần sau với cùng key trả lại ĐÚNG
đơn đã tạo mà không trừ vé thêm lần nào.
"""
from collections.abc import Callable
from datetime import datetime, timezone

from app.services.errors import (
    Forbidden,
    IdempotencyKeyReused,
    InvalidState,
    NotFound,
    SaleNotOpen,
    SoldOut,
)
from app.services.order_data import OrderData
from app.services.unit_of_work import UnitOfWork


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class OrderService:
    def __init__(self, uow_factory: Callable[[], UnitOfWork], clock=_utcnow, execute=None):
        self.uow_factory = uow_factory
        self.clock = clock
        # Infrastructure may inject retries without coupling services to SQLAlchemy.
        self.execute = execute or (lambda operation: operation())

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

        return self.execute(
            lambda: self._place_order(user_id, ticket_type_id, quantity, idempotency_key)
        )

    def _place_order(self, user_id, ticket_type_id, quantity, idempotency_key):
        with self.uow_factory() as uow:
            if idempotency_key is not None:
                existing = uow.orders.get_by_idempotency_key(user_id, idempotency_key)
                if existing is not None:
                    return self._replay(existing, ticket_type_id, quantity), False

            ticket_type = uow.ticket_types.get_by_id(ticket_type_id)
            if ticket_type is None:
                raise NotFound(f"ticket_type {ticket_type_id}")

            concert = uow.concerts.get_by_id(ticket_type.concert_id)
            if self.clock() < concert.sale_open_time:
                raise SaleNotOpen(concert.sale_open_time.isoformat())

            if idempotency_key is None:
                order = None
            else:
                # Giành key TRƯỚC khi trừ vé: nếu request trùng key khác đã thắng thì
                # dừng ở đây, kho vé không bị đụng tới.
                order = uow.orders.create_idempotent(user_id, ticket_type_id, quantity, idempotency_key)
                if order is None:
                    existing = uow.orders.get_by_idempotency_key(user_id, idempotency_key)
                    return self._replay(existing, ticket_type_id, quantity), False

            # Hết vé → ném lỗi → UoW rollback khi thoát, đơn vừa chèn (nếu có) cũng bị huỷ.
            if not uow.ticket_types.reserve(ticket_type_id, quantity):
                raise SoldOut(f"ticket_type {ticket_type_id}")

            if order is None:
                order = uow.orders.create(user_id=user_id, ticket_type_id=ticket_type_id, quantity=quantity)
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

    def cancel_order(self, user_id: int, order_id: int):
        return self.execute(lambda: self._cancel_order(user_id, order_id))

    def _cancel_order(self, user_id, order_id):
        with self.uow_factory() as uow:
            order = uow.orders.get_by_id(order_id)
            if order is None:
                raise NotFound(f"order {order_id}")
            if order.user_id != user_id:
                raise Forbidden("not your order")
            if order.status != "confirmed":
                raise InvalidState(f"order already {order.status}")

            # Trạng thái đã đọc có thể cũ; DB quyết định request được hoàn kho.
            if not uow.orders.cancel_if_confirmed(order_id, user_id):
                raise InvalidState("order is no longer confirmed")

            uow.ticket_types.release(order.ticket_type_id, order.quantity)
            result = OrderData.from_order(order)
            uow.commit()
        return result

    def list_my_orders(self, user_id: int):
        return self.execute(lambda: self._list_my_orders(user_id))

    def _list_my_orders(self, user_id):
        with self.uow_factory() as uow:
            return [OrderData.from_order(order) for order in uow.orders.list_by_user(user_id)]
