from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.errors import Forbidden, InvalidState, NotFound, SaleNotOpen, SoldOut
from app.services.order_service import OrderService

NOW = datetime(2026, 9, 19, 12, 0, 0)


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


class FakeOrderRepo:
    def __init__(self):
        self.orders = {}
        self._next = 1

    def create(self, user_id, ticket_type_id, quantity):
        o = SimpleNamespace(
            id=self._next, user_id=user_id, ticket_type_id=ticket_type_id,
            quantity=quantity, status="confirmed", created_at=NOW,
        )
        self.orders[o.id] = o
        self._next += 1
        return o

    def get_by_id(self, order_id):
        return self.orders.get(order_id)

    def cancel_if_confirmed(self, order_id, user_id):
        # Giống UPDATE có điều kiện: chỉ đổi khi đơn đúng chủ và vẫn confirmed.
        o = self.orders.get(order_id)
        if o is None or o.user_id != user_id or o.status != "confirmed":
            return False
        o.status = "cancelled"
        return True

    def list_by_user(self, user_id):
        return [o for o in self.orders.values() if o.user_id == user_id]


class FakeUnitOfWork:
    """UoW giả dùng chung các repo in-memory; đếm số lần commit để kiểm tra ranh giới transaction."""

    def __init__(self, orders, ticket_types, concerts):
        self.orders = orders
        self.ticket_types = ticket_types
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


@pytest.fixture
def uow():
    concerts = [
        SimpleNamespace(id=1, sale_open_time=NOW - timedelta(hours=1)),
        SimpleNamespace(id=2, sale_open_time=NOW + timedelta(days=1)),
    ]
    ticket_types = [
        SimpleNamespace(id=10, concert_id=1, remaining=5),
        SimpleNamespace(id=20, concert_id=2, remaining=5),
    ]
    return FakeUnitOfWork(FakeOrderRepo(), FakeTicketTypeRepo(ticket_types), FakeConcertRepo(concerts))


@pytest.fixture
def svc(uow):
    return OrderService(lambda: uow, clock=lambda: NOW)


def remaining(uow, ticket_type_id):
    return uow.ticket_types.get_by_id(ticket_type_id).remaining


def test_create_order_ok(svc, uow):
    order = svc.create_order(user_id=1, ticket_type_id=10, quantity=2)
    assert order.status == "confirmed"
    assert remaining(uow, 10) == 3
    assert uow.commits == 1


def test_create_order_sold_out(svc, uow):
    with pytest.raises(SoldOut):
        svc.create_order(user_id=1, ticket_type_id=10, quantity=6)
    assert remaining(uow, 10) == 5
    assert uow.commits == 0


def test_create_order_sale_not_open(svc, uow):
    with pytest.raises(SaleNotOpen):
        svc.create_order(user_id=1, ticket_type_id=20, quantity=1)
    assert uow.commits == 0


def test_create_order_unknown_ticket_type(svc, uow):
    with pytest.raises(NotFound):
        svc.create_order(user_id=1, ticket_type_id=999, quantity=1)
    assert uow.commits == 0


def test_create_order_min_valid_quantity(svc, uow):
    order = svc.create_order(user_id=1, ticket_type_id=10, quantity=1)
    assert order.status == "confirmed"
    assert remaining(uow, 10) == 4


def test_create_order_max_valid_quantity(svc, uow):
    uow.ticket_types.get_by_id(10).remaining = 20

    order = svc.create_order(user_id=1, ticket_type_id=10, quantity=10)
    assert order.status == "confirmed"
    assert remaining(uow, 10) == 10


def test_create_order_invalid_quantity_zero_or_negative(svc, uow):
    with pytest.raises(ValueError):
        svc.create_order(user_id=1, ticket_type_id=10, quantity=0)

    with pytest.raises(ValueError):
        svc.create_order(user_id=1, ticket_type_id=10, quantity=-1)
    assert uow.commits == 0


def test_create_order_exceeds_max_limit_per_request(svc, uow):
    uow.ticket_types.get_by_id(10).remaining = 20
    with pytest.raises(ValueError):
        svc.create_order(user_id=1, ticket_type_id=10, quantity=11)
    assert remaining(uow, 10) == 20


def test_cancel_order_returns_tickets(svc, uow):
    order = svc.create_order(user_id=1, ticket_type_id=10, quantity=2)
    cancelled = svc.cancel_order(user_id=1, order_id=order.id)
    assert cancelled.status == "cancelled"
    assert remaining(uow, 10) == 5
    assert uow.commits == 2


def test_cancel_order_of_other_user_forbidden(svc, uow):
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


def test_cancel_order_not_found(svc):
    with pytest.raises(NotFound):
        svc.cancel_order(user_id=1, order_id=9999)


def test_cancel_lost_race_does_not_release(svc, uow):
    """Đơn bị request khác huỷ sau khi đã đọc: UPDATE có điều kiện thất bại thì không hoàn kho."""
    order = svc.create_order(user_id=1, ticket_type_id=10, quantity=2)
    stale = SimpleNamespace(**vars(uow.orders.get_by_id(order.id)))
    uow.orders.get_by_id(order.id).status = "cancelled"   # request khác đã huỷ
    uow.orders.get_by_id = lambda order_id: stale          # request này vẫn đọc bản cũ "confirmed"

    with pytest.raises(InvalidState):
        svc.cancel_order(user_id=1, order_id=order.id)
    assert remaining(uow, 10) == 3
    assert uow.commits == 1
