"""Nghiệp vụ đặt/hủy vé tự xác định ranh giới transaction thông qua UoW."""
from collections.abc import Callable
from datetime import datetime

from app.services.errors import Forbidden, InvalidState, NotFound, SaleNotOpen, SoldOut
from app.services.order_data import OrderData
from app.services.unit_of_work import UnitOfWork


class OrderService:
    def __init__(self, uow_factory: Callable[[], UnitOfWork], clock=datetime.utcnow, execute=None):
        self.uow_factory = uow_factory
        self.clock = clock
        # Infrastructure may inject retries without coupling services to SQLAlchemy.
        self.execute = execute or (lambda operation: operation())

    def create_order(self, user_id: int, ticket_type_id: int, quantity: int):
        if quantity <= 0:
            raise ValueError("quantity must be greater than 0")
        if quantity > 10:
            raise ValueError("quantity must not exceed 10 per order")

        return self.execute(lambda: self._create_order(user_id, ticket_type_id, quantity))

    def _create_order(self, user_id, ticket_type_id, quantity):
        with self.uow_factory() as uow:
            ticket_type = uow.ticket_types.get_by_id(ticket_type_id)
            if ticket_type is None:
                raise NotFound(f"ticket_type {ticket_type_id}")

            concert = uow.concerts.get_by_id(ticket_type.concert_id)
            if self.clock() < concert.sale_open_time:
                raise SaleNotOpen(concert.sale_open_time.isoformat())

            if not uow.ticket_types.reserve(ticket_type_id, quantity):
                raise SoldOut(f"ticket_type {ticket_type_id}")

            order = uow.orders.create(user_id=user_id, ticket_type_id=ticket_type_id, quantity=quantity)
            result = OrderData.from_order(order)
            uow.commit()
        return result

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
