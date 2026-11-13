"""Unit test cho OrderService: repo giả in-memory, đồng hồ cố định, không cần DB."""
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.errors import (
    Forbidden, HoldLimitExceeded, IdempotencyKeyReused, InvalidState, NotFound,
    SaleClosed, SaleNotOpen, SoldOut,
)
from app.services.order_service import OrderService

NOW = datetime(2026, 9, 19, 12, 0, 0)


class FakeUserRepo:
    def lock(self, user_id):
        return True


class FakeConcertRepo:
    def __init__(self, concerts):
        self.concerts = {c.id: c for c in concerts}

    def get_by_id(self, concert_id):
        return self.concerts.get(concert_id)


class FakeTicketTypeRepo:
    def __init__(self, ticket_types):
        self.items = {t.id: t for t in ticket_types}

    def get_by_id(self, ticket_type_id):
        return self.items.get(ticket_type_id)

    def reserve(self, ticket_type_id, quantity):
        t = self.items[ticket_type_id]
        if t.remaining < quantity:
            return False
        t.remaining -= quantity
        return True

    def release(self, ticket_type_id, quantity):
        self.items[ticket_type_id].remaining += quantity


class FakeTicketRepo:
    """Chỉ đếm vé đang giữ theo đơn; đủ để kiểm tra giữ/trả vé."""

    def __init__(self):
        self.held = {}

    def hold(self, ticket_type_id, order_id, quantity):
        self.held[order_id] = quantity
        return [SimpleNamespace(id=i) for i in range(quantity)]

    def release(self, order_id):
        return self.held.pop(order_id, 0)

    def list_by_order(self, order_id):
        return []


class FakeOrderRepo:
    def __init__(self):
        self.orders = {}
        self._next = 1

    def create(self, idempotency_key=None, **fields):
        o = SimpleNamespace(id=self._next, status="held", paid_at=None, created_at=NOW,
                            idempotency_key=idempotency_key, **fields)
        self.orders[o.id] = o
        self._next += 1
        return o

    def create_idempotent(self, idempotency_key, **fields):
        if self.get_by_idempotency_key(fields["user_id"], idempotency_key) is not None:
            return None
        return self.create(idempotency_key=idempotency_key, **fields)

    def get_by_idempotency_key(self, user_id, idempotency_key):
        return next((o for o in self.orders.values()
                     if o.user_id == user_id and o.idempotency_key == idempotency_key), None)

    def get_by_id(self, order_id):
        return self.orders.get(order_id)

    def list_by_user(self, user_id):
        return [o for o in self.orders.values() if o.user_id == user_id]

    def held_quantity(self, user_id, now):
        return sum(o.quantity for o in self.orders.values()
                   if o.user_id == user_id and o.status == "held" and o.expires_at > now)

    def list_expired_ids(self, now, limit):
        return [o.id for o in self.orders.values() if o.status == "held" and o.expires_at <= now][:limit]

    # Giống UPDATE có điều kiện: chỉ đổi khi đơn vẫn đang "held".
    def _transition(self, order_id, status, condition=lambda o: True):
        o = self.orders.get(order_id)
        if o is None or o.status != "held" or not condition(o):
            return False
        o.status = status
        return True

    def cancel_hold(self, order_id, user_id):
        return self._transition(order_id, "cancelled", lambda o: o.user_id == user_id)

    def mark_expired(self, order_id, now):
        return self._transition(order_id, "expired", lambda o: o.expires_at <= now)


class FakeUnitOfWork:
    """UoW giả dùng chung các repo in-memory; đếm số lần commit để kiểm tra ranh giới transaction."""

    def __init__(self, orders, ticket_types, concerts):
        self.users = FakeUserRepo()
        self.orders = orders
        self.ticket_types = ticket_types
        self.tickets = FakeTicketRepo()
        self.concerts = concerts
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return None

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


@pytest.fixture
def uow():
    concerts = [
        SimpleNamespace(id=1, sale_open_time=NOW - timedelta(hours=1), start_time=NOW + timedelta(days=10)),
        SimpleNamespace(id=2, sale_open_time=NOW + timedelta(days=1), start_time=NOW + timedelta(days=10)),
        SimpleNamespace(id=3, sale_open_time=NOW - timedelta(days=10), start_time=NOW - timedelta(minutes=1)),
    ]
    ticket_types = [
        SimpleNamespace(id=10, concert_id=1, remaining=5, price=Decimal("100")),
        SimpleNamespace(id=20, concert_id=2, remaining=5, price=Decimal("100")),
        SimpleNamespace(id=30, concert_id=3, remaining=5, price=Decimal("100")),
    ]
    return FakeUnitOfWork(FakeOrderRepo(), FakeTicketTypeRepo(ticket_types), FakeConcertRepo(concerts))


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def svc(uow, clock):
    return OrderService(lambda: uow, clock=clock, hold_minutes=12, max_per_order=10, max_held_per_user=10)


def remaining(uow, ticket_type_id):
    return uow.ticket_types.get_by_id(ticket_type_id).remaining


# --- Giữ chỗ ---------------------------------------------------------------

def test_hold_ok(svc, uow):
    order = svc.create_order(user_id=1, ticket_type_id=10, quantity=2)
    assert order.status == "held"
    assert order.unit_price == 100 and order.total_amount == 200
    assert order.expires_at == NOW + timedelta(minutes=12)
    assert remaining(uow, 10) == 3
    assert uow.tickets.held == {order.id: 2}
    assert uow.commits == 1


def test_hold_sold_out(svc, uow):
    with pytest.raises(SoldOut):
        svc.create_order(user_id=1, ticket_type_id=10, quantity=6)
    assert remaining(uow, 10) == 5
    assert uow.commits == 0


def test_hold_sale_not_open(svc, uow):
    with pytest.raises(SaleNotOpen):
        svc.create_order(user_id=1, ticket_type_id=20, quantity=1)
    assert uow.commits == 0


def test_hold_after_concert_started(svc, uow):
    with pytest.raises(SaleClosed):
        svc.create_order(user_id=1, ticket_type_id=30, quantity=1)
    assert remaining(uow, 30) == 5
    assert uow.commits == 0


def test_hold_unknown_ticket_type(svc, uow):
    with pytest.raises(NotFound):
        svc.create_order(user_id=1, ticket_type_id=999, quantity=1)
    assert uow.commits == 0


def test_hold_min_valid_quantity(svc, uow):
    svc.create_order(user_id=1, ticket_type_id=10, quantity=1)
    assert remaining(uow, 10) == 4


def test_hold_max_valid_quantity(svc, uow):
    uow.ticket_types.get_by_id(10).remaining = 20
    svc.create_order(user_id=1, ticket_type_id=10, quantity=10)
    assert remaining(uow, 10) == 10


def test_hold_invalid_quantity_zero_or_negative(svc, uow):
    with pytest.raises(ValueError):
        svc.create_order(user_id=1, ticket_type_id=10, quantity=0)
    with pytest.raises(ValueError):
        svc.create_order(user_id=1, ticket_type_id=10, quantity=-1)
    assert uow.commits == 0


def test_hold_exceeds_max_per_order(svc, uow):
    uow.ticket_types.get_by_id(10).remaining = 20
    with pytest.raises(ValueError):
        svc.create_order(user_id=1, ticket_type_id=10, quantity=11)
    assert remaining(uow, 10) == 20


def test_hold_limit_per_user(svc, uow, clock):
    uow.ticket_types.get_by_id(10).remaining = 50
    svc.create_order(user_id=1, ticket_type_id=10, quantity=8)
    with pytest.raises(HoldLimitExceeded):
        svc.create_order(user_id=1, ticket_type_id=10, quantity=3)
    assert remaining(uow, 10) == 42
    svc.create_order(user_id=1, ticket_type_id=10, quantity=2)
    svc.create_order(user_id=2, ticket_type_id=10, quantity=10)  # người khác không bị ảnh hưởng

    clock.now = NOW + timedelta(minutes=13)  # đơn cũ hết hạn → không còn tính vào giới hạn
    svc.create_order(user_id=1, ticket_type_id=10, quantity=10)


# --- Hủy giữ chỗ -----------------------------------------------------------

def test_cancel_returns_tickets(svc, uow):
    order = svc.create_order(user_id=1, ticket_type_id=10, quantity=2)
    cancelled = svc.cancel_order(user_id=1, order_id=order.id)
    assert cancelled.status == "cancelled"
    assert remaining(uow, 10) == 5
    assert uow.tickets.held == {}
    assert uow.commits == 2


def test_cancel_other_users_order_forbidden(svc, uow):
    order = svc.create_order(user_id=1, ticket_type_id=10, quantity=1)
    with pytest.raises(Forbidden):
        svc.cancel_order(user_id=2, order_id=order.id)
    assert remaining(uow, 10) == 4


def test_cancel_twice_invalid(svc, uow):
    order = svc.create_order(user_id=1, ticket_type_id=10, quantity=1)
    svc.cancel_order(user_id=1, order_id=order.id)
    with pytest.raises(InvalidState):
        svc.cancel_order(user_id=1, order_id=order.id)
    assert remaining(uow, 10) == 5


def test_cancel_paid_order_invalid(svc, uow):
    order = svc.create_order(user_id=1, ticket_type_id=10, quantity=1)
    uow.orders.get_by_id(order.id).status = "paid"
    with pytest.raises(InvalidState):
        svc.cancel_order(user_id=1, order_id=order.id)
    assert remaining(uow, 10) == 4


def test_cancel_order_not_found(svc):
    with pytest.raises(NotFound):
        svc.cancel_order(user_id=1, order_id=9999)


def test_cancel_lost_race_does_not_release(svc, uow):
    """Đơn bị request khác hủy sau khi đã đọc: UPDATE có điều kiện thất bại thì không hoàn kho."""
    order = svc.create_order(user_id=1, ticket_type_id=10, quantity=2)
    stale = SimpleNamespace(**vars(uow.orders.get_by_id(order.id)))
    uow.orders.get_by_id(order.id).status = "cancelled"   # request khác đã hủy
    uow.orders.get_by_id = lambda order_id: stale          # request này vẫn đọc bản cũ "held"

    with pytest.raises(InvalidState):
        svc.cancel_order(user_id=1, order_id=order.id)
    assert remaining(uow, 10) == 3
    assert uow.commits == 1


# --- Hết hạn ---------------------------------------------------------------

def test_expire_holds_releases_only_overdue(svc, uow, clock):
    old = svc.create_order(user_id=1, ticket_type_id=10, quantity=2)
    clock.now = NOW + timedelta(minutes=5)
    fresh = svc.create_order(user_id=2, ticket_type_id=10, quantity=1)

    clock.now = NOW + timedelta(minutes=12)
    assert svc.expire_holds() == 1
    assert uow.orders.get_by_id(old.id).status == "expired"
    assert uow.orders.get_by_id(fresh.id).status == "held"
    assert remaining(uow, 10) == 4
    assert svc.expire_holds() == 0  # chạy lại không hoàn thêm
    assert remaining(uow, 10) == 4


def test_expire_skips_order_cancelled_meanwhile(svc, uow, clock):
    order = svc.create_order(user_id=1, ticket_type_id=10, quantity=2)
    clock.now = NOW + timedelta(minutes=13)
    svc.cancel_order(user_id=1, order_id=order.id)
    assert svc.expire_holds() == 0
    assert remaining(uow, 10) == 5


# --- Idempotency-Key -------------------------------------------------------

def test_retry_with_same_idempotency_key_returns_same_order(svc, uow):
    first, created = svc.place_order(1, 10, 2, idempotency_key="key-retry-1")
    again, created_again = svc.place_order(1, 10, 2, idempotency_key="key-retry-1")
    assert created is True and created_again is False
    assert again == first
    assert len(uow.orders.orders) == 1
    assert remaining(uow, 10) == 3
    assert uow.commits == 1


def test_retry_after_cancel_does_not_rebook(svc, uow):
    order = svc.create_order(1, 10, 2, idempotency_key="key-retry-2")
    svc.cancel_order(1, order.id)
    replay = svc.create_order(1, 10, 2, idempotency_key="key-retry-2")
    assert replay.id == order.id and replay.status == "cancelled"
    assert remaining(uow, 10) == 5


def test_idempotency_key_reused_with_different_payload(svc, uow):
    svc.create_order(1, 10, 2, idempotency_key="key-retry-3")
    with pytest.raises(IdempotencyKeyReused):
        svc.create_order(1, 10, 3, idempotency_key="key-retry-3")
    assert remaining(uow, 10) == 3


def test_idempotency_key_is_scoped_per_user(svc, uow):
    a = svc.create_order(1, 10, 1, idempotency_key="shared-key")
    b = svc.create_order(2, 10, 1, idempotency_key="shared-key")
    assert a.id != b.id
    assert remaining(uow, 10) == 3


def test_failed_attempt_can_be_retried_with_same_key(svc, uow):
    with pytest.raises(SoldOut):
        svc.create_order(1, 10, 6, idempotency_key="key-retry-4")
    assert uow.commits == 0
    # Trong hệ thống thật, UoW rollback khi thoát nên đơn chèn tạm cũng biến mất.
    uow.orders.orders.clear()
    uow.ticket_types.get_by_id(10).remaining = 10
    order = svc.create_order(1, 10, 6, idempotency_key="key-retry-4")
    assert order.status == "held"
