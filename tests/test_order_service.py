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
            quantity=quantity, status="confirmed",
        )
        self.orders[o.id] = o
        self._next += 1
        return o

    def get_by_id(self, order_id):
        return self.orders.get(order_id)

    def set_status(self, order, status):
        order.status = status
        return order

    def list_by_user(self, user_id):
        return [o for o in self.orders.values() if o.user_id == user_id]


@pytest.fixture
def svc():
    concerts = [
        SimpleNamespace(id=1, sale_open_time=NOW - timedelta(hours=1)),
        SimpleNamespace(id=2, sale_open_time=NOW + timedelta(days=1)),
    ]
    ticket_types = [
        SimpleNamespace(id=10, concert_id=1, remaining=5),
        SimpleNamespace(id=20, concert_id=2, remaining=5),
    ]
    return OrderService(FakeOrderRepo(), FakeTicketTypeRepo(ticket_types), FakeConcertRepo(concerts), clock=lambda: NOW)


def test_create_order_ok(svc):
    order = svc.create_order(user_id=1, ticket_type_id=10, quantity=2)
    assert order.status == "confirmed"
    assert svc.ticket_type_repo.get_by_id(10).remaining == 3


def test_create_order_sold_out(svc):
    with pytest.raises(SoldOut):
        svc.create_order(user_id=1, ticket_type_id=10, quantity=6)
    assert svc.ticket_type_repo.get_by_id(10).remaining == 5


def test_create_order_sale_not_open(svc):
    with pytest.raises(SaleNotOpen):
        svc.create_order(user_id=1, ticket_type_id=20, quantity=1)


def test_create_order_unknown_ticket_type(svc):
    with pytest.raises(NotFound):
        svc.create_order(user_id=1, ticket_type_id=999, quantity=1)

def test_create_order_min_valid_quantity(svc):
    order = svc.create_order(user_id=1, ticket_type_id=10, quantity=1)
    assert order.status == "confirmed"
    assert svc.ticket_type_repo.get_by_id(10).remaining == 4


def test_create_order_max_valid_quantity(svc):
    svc.ticket_type_repo.get_by_id(10).remaining = 20
    
    order = svc.create_order(user_id=1, ticket_type_id=10, quantity=10)
    assert order.status == "confirmed"
    assert svc.ticket_type_repo.get_by_id(10).remaining == 10


def test_create_order_invalid_quantity_zero_or_negative(svc):
    with pytest.raises(ValueError): 
        svc.create_order(user_id=1, ticket_type_id=10, quantity=0)
        
    with pytest.raises(ValueError):
        svc.create_order(user_id=1, ticket_type_id=10, quantity=-1)


def test_create_order_exceeds_max_limit_per_request(svc):
    svc.ticket_type_repo.get_by_id(10).remaining = 20
    with pytest.raises(ValueError): 
        svc.create_order(user_id=1, ticket_type_id=10, quantity=11)




def test_cancel_order_returns_tickets(svc):
    order = svc.create_order(user_id=1, ticket_type_id=10, quantity=2)
    svc.cancel_order(user_id=1, order_id=order.id)
    assert order.status == "cancelled"
    assert svc.ticket_type_repo.get_by_id(10).remaining == 5


def test_cancel_order_of_other_user_forbidden(svc):
    order = svc.create_order(user_id=1, ticket_type_id=10, quantity=1)
    with pytest.raises(Forbidden):
        svc.cancel_order(user_id=2, order_id=order.id)


def test_cancel_twice_invalid(svc):
    order = svc.create_order(user_id=1, ticket_type_id=10, quantity=1)
    svc.cancel_order(user_id=1, order_id=order.id)
    with pytest.raises(InvalidState):
        svc.cancel_order(user_id=1, order_id=order.id)

def test_cancel_order_not_found(svc):
    with pytest.raises(NotFound):
        svc.cancel_order(user_id=1, order_id=9999)


