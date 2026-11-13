"""Order snapshots that remain usable after the transaction session closes."""
from dataclasses import dataclass
from datetime import datetime

from app.services.unit_of_work import OrderRecord


@dataclass(frozen=True)
class OrderData:
    id: int
    user_id: int
    ticket_type_id: int
    quantity: int
    status: str
    created_at: datetime

    @classmethod
    def from_order(cls, order: OrderRecord) -> "OrderData":
        return cls(
            id=order.id, user_id=order.user_id,
            ticket_type_id=order.ticket_type_id, quantity=order.quantity,
            status=order.status, created_at=order.created_at,
        )
