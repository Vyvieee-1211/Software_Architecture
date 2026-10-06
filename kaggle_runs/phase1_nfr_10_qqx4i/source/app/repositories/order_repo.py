from sqlalchemy import select
from sqlalchemy.orm import Session

from app.repositories.models import Order


class OrderRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, user_id: int, ticket_type_id: int, quantity: int) -> Order:
        order = Order(
            user_id=user_id,
            ticket_type_id=ticket_type_id,
            quantity=quantity,
            status="confirmed",
        )
        self.db.add(order)
        self.db.flush()
        return order

    def get_by_id(self, order_id: int) -> Order | None:
        return self.db.get(Order, order_id)

    def list_by_user(self, user_id: int) -> list[Order]:
        return list(
            self.db.scalars(
                select(Order).where(Order.user_id == user_id).order_by(Order.created_at.desc())
            )
        )

    def set_status(self, order: Order, status: str) -> Order:
        order.status = status
        self.db.flush()
        return order
