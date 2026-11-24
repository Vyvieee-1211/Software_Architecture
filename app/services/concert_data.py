"""Dữ liệu tra cứu concert và hạng vé độc lập với session DB."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Self

from app.services.unit_of_work import ConcertRecord, TicketTypeRecord


@dataclass(frozen=True)
class ConcertData:
    id: int
    name: str
    artist: str | None
    venue: str | None
    start_time: datetime
    sale_open_time: datetime

    @classmethod
    def from_concert(cls, concert: ConcertRecord) -> Self:
        return cls(
            id=concert.id, name=concert.name, artist=concert.artist, venue=concert.venue,
            start_time=concert.start_time, sale_open_time=concert.sale_open_time,
        )


@dataclass(frozen=True)
class TicketTypeData:
    id: int
    concert_id: int
    name: str
    price: Decimal
    total_quantity: int
    remaining: int

    @classmethod
    def from_ticket_type(cls, ticket_type: TicketTypeRecord) -> Self:
        return cls(
            id=ticket_type.id, concert_id=ticket_type.concert_id, name=ticket_type.name,
            price=ticket_type.price, total_quantity=ticket_type.total_quantity,
            remaining=ticket_type.remaining,
        )
