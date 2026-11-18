"""Transaction contract for application services; independent of SQLAlchemy."""
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Protocol, Self


class OrderRecord(Protocol):
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


class TicketTypeRecord(Protocol):
    id: int
    concert_id: int
    name: str
    price: Decimal
    total_quantity: int
    remaining: int


class ConcertRecord(Protocol):
    id: int
    name: str
    artist: str | None
    venue: str | None
    sale_open_time: datetime
    start_time: datetime


class TicketRecord(Protocol):
    id: int
    ticket_type_id: int
    seat_no: str
    order_id: int | None
    status: str
    qr_code: str | None


class TransactionRecord(Protocol):
    order_id: int
    payment_id: str
    provider: str
    type: str
    status: str
    amount: Decimal
    raw_log: str


class OrderRequestRecord(Protocol):
    id: int
    user_id: int
    ticket_type_id: int
    quantity: int
    idempotency_key: str | None
    status: str
    order_id: int | None
    error: str | None
    detail: str | None
    attempts: int
    created_at: datetime
    completed_at: datetime | None


class Users(Protocol):
    def lock(self, user_id: int) -> bool: ...


class Orders(Protocol):
    def get_by_id(self, order_id: int) -> OrderRecord | None: ...
    def create(self, **fields) -> OrderRecord: ...
    def create_idempotent(self, idempotency_key: str, **fields) -> OrderRecord | None: ...
    def get_by_idempotency_key(self, user_id: int, idempotency_key: str) -> OrderRecord | None: ...
    def list_by_user(self, user_id: int) -> list[OrderRecord]: ...
    def held_quantity(self, user_id: int, now: datetime) -> int: ...
    def list_expired_ids(self, now: datetime, limit: int) -> list[int]: ...
    def cancel_hold(self, order_id: int, user_id: int) -> bool: ...
    def mark_paid(self, order_id: int, now: datetime) -> bool: ...
    def mark_failed(self, order_id: int) -> bool: ...
    def mark_expired(self, order_id: int, now: datetime) -> bool: ...
    def mark_refunded(self, order_id: int, user_id: int) -> bool: ...


class TicketTypes(Protocol):
    def get_by_id(self, ticket_type_id: int) -> TicketTypeRecord | None: ...
    def list_by_concert(self, concert_id: int) -> list[TicketTypeRecord]: ...
    def reserve(self, ticket_type_id: int, quantity: int) -> bool: ...
    def release(self, ticket_type_id: int, quantity: int) -> None: ...


class Tickets(Protocol):
    def hold(self, ticket_type_id: int, order_id: int, quantity: int) -> list[TicketRecord] | None: ...
    def sell(self, order_id: int, make_code: Callable[[int], str]) -> list[TicketRecord]: ...
    def release(self, order_id: int, status: str = "held") -> int: ...
    def list_by_order(self, order_id: int) -> list[TicketRecord]: ...


class Concerts(Protocol):
    def get_by_id(self, concert_id: int) -> ConcertRecord | None: ...
    def list_all(self) -> list[ConcertRecord]: ...
    def list_on_sale(self, now: datetime) -> list[ConcertRecord]: ...


class Transactions(Protocol):
    def record(self, *, order_id: int, payment_id: str, provider: str, type: str,
               status: str, amount: Decimal, raw_log: str) -> bool: ...
    def get_event(self, provider: str, payment_id: str, type: str, status: str) -> TransactionRecord | None: ...
    def latest_pending_payment(self, order_id: int) -> TransactionRecord | None: ...
    def succeeded_payment(self, order_id: int) -> TransactionRecord | None: ...


class OrderRequests(Protocol):
    def create(self, *, user_id: int, ticket_type_id: int, quantity: int,
               idempotency_key: str | None) -> OrderRequestRecord | None: ...
    def get_by_id(self, request_id: int) -> OrderRequestRecord | None: ...
    def get_by_key(self, user_id: int, idempotency_key: str) -> OrderRequestRecord | None: ...
    def claim_next(self) -> OrderRequestRecord | None: ...
    def complete(self, request_id: int, order_id: int, now: datetime) -> bool: ...
    def fail(self, request_id: int, error: str, detail: str, now: datetime) -> bool: ...
    def record_crash(self, request_id: int, detail: str, max_attempts: int, now: datetime) -> bool: ...


class UnitOfWork(Protocol):
    users: Users
    orders: Orders
    ticket_types: TicketTypes
    tickets: Tickets
    concerts: Concerts
    transactions: Transactions
    order_requests: OrderRequests

    def __enter__(self) -> Self: ...

    def __exit__(self, exc_type, exc_value, traceback) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
