"""Transaction contract for application services; independent of SQLAlchemy."""
from datetime import datetime
from typing import Protocol, Self


class OrderRecord(Protocol):
    id: int
    user_id: int
    ticket_type_id: int
    quantity: int
    status: str
    created_at: datetime


class TicketTypeRecord(Protocol):
    concert_id: int


class ConcertRecord(Protocol):
    sale_open_time: datetime


class Orders(Protocol):
    def get_by_id(self, order_id: int) -> OrderRecord | None: ...
    def create(self, user_id: int, ticket_type_id: int, quantity: int) -> OrderRecord: ...
    def create_idempotent(
        self, user_id: int, ticket_type_id: int, quantity: int, idempotency_key: str
    ) -> OrderRecord | None: ...
    def get_by_idempotency_key(self, user_id: int, idempotency_key: str) -> OrderRecord | None: ...
    def cancel_if_confirmed(self, order_id: int, user_id: int) -> bool: ...
    def list_by_user(self, user_id: int) -> list[OrderRecord]: ...


class TicketTypes(Protocol):
    def get_by_id(self, ticket_type_id: int) -> TicketTypeRecord | None: ...
    def reserve(self, ticket_type_id: int, quantity: int) -> bool: ...
    def release(self, ticket_type_id: int, quantity: int) -> None: ...


class Concerts(Protocol):
    def get_by_id(self, concert_id: int) -> ConcertRecord | None: ...


class UnitOfWork(Protocol):
    orders: Orders
    ticket_types: TicketTypes
    concerts: Concerts

    def __enter__(self) -> Self: ...

    def __exit__(self, exc_type, exc_value, traceback) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
