"""Nghiệp vụ đặt vé / huỷ vé — phần trọng tâm của hệ thống.

Mọi thao tác trong một hàm chạy chung MỘT transaction do tầng API mở (get_db);
service chỉ gọi repo, không commit. Lỗi ném ra → tầng API rollback + trả status code.
"""
from datetime import datetime

from app.services.errors import Forbidden, InvalidState, NotFound, SaleNotOpen, SoldOut


class OrderService:
    def __init__(self, order_repo, ticket_type_repo, concert_repo, clock=datetime.utcnow):
        self.order_repo = order_repo
        self.ticket_type_repo = ticket_type_repo
        self.concert_repo = concert_repo
        self.clock = clock

    def create_order(self, user_id: int, ticket_type_id: int, quantity: int):
        if quantity <= 0:
            raise ValueError("quantity must be greater than 0")
        if quantity > 10:
            raise ValueError("quantity must not exceed 10 per order")

        ticket_type = self.ticket_type_repo.get_by_id(ticket_type_id)
        if ticket_type is None:
            raise NotFound(f"ticket_type {ticket_type_id}")

        concert = self.concert_repo.get_by_id(ticket_type.concert_id)
        if self.clock() < concert.sale_open_time:
            raise SaleNotOpen(concert.sale_open_time.isoformat())

        if not self.ticket_type_repo.reserve(ticket_type_id, quantity):
            raise SoldOut(f"ticket_type {ticket_type_id}")

        return self.order_repo.create(user_id=user_id, ticket_type_id=ticket_type_id, quantity=quantity)

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
