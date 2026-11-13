"""Snapshots that remain usable after the transaction session closes."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.services.unit_of_work import OrderRecord, TicketRecord


@dataclass(frozen=True)
class OrderData:
    id: int
    user_id: int
    ticket_type_id: int
    quantity: int
    status: str
    unit_price: Decimal
    total_amount: Decimal
    expires_at: datetime
    paid_at: datetime | None
    created_at: datetime

    @classmethod
    def from_order(cls, order: OrderRecord) -> "OrderData":
        return cls(
            id=order.id, user_id=order.user_id,
            ticket_type_id=order.ticket_type_id, quantity=order.quantity,
            status=order.status, unit_price=order.unit_price,
            total_amount=order.total_amount, expires_at=order.expires_at,
            paid_at=order.paid_at, created_at=order.created_at,
        )


@dataclass(frozen=True)
class TicketData:
    id: int
    ticket_type_id: int
    seat_no: str
    order_id: int | None
    status: str
    qr_code: str | None

    @classmethod
    def from_ticket(cls, ticket: TicketRecord) -> "TicketData":
        return cls(
            id=ticket.id, ticket_type_id=ticket.ticket_type_id, seat_no=ticket.seat_no,
            order_id=ticket.order_id, status=ticket.status, qr_code=ticket.qr_code,
        )
