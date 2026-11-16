"""Unit test cho PaymentService.refund_order: repo giả in-memory, cổng giả, không cần DB."""
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.errors import Forbidden, InvalidState, NotFound, PaymentGatewayError, RefundWindowClosed
from app.services.payment_gateway import RefundResult
from app.services.payment_service import PaymentService
from tests.test_order_service import (
    FakeConcertRepo, FakeOrderRepo, FakeTicketTypeRepo, FakeUnitOfWork, NOW,
)

START = NOW + timedelta(days=2)


class FakeTicketRepo:
    def __init__(self):
        self.released = []  # (order_id, status)

    def release(self, order_id, status="held"):
        self.released.append((order_id, status))
        return 1


class FakeTransactionRepo:
    def __init__(self):
        self.rows = []

    def record(self, **values):
        key = (values["provider"], values["payment_id"], values["type"], values["status"])
        if any(r["key"] == key for r in self.rows):
            return False
        self.rows.append({"key": key, **values})
        return True

    def succeeded_payment(self, order_id):
        return next((SimpleNamespace(**r) for r in self.rows
                     if r["order_id"] == order_id and r["type"] == "payment" and r["status"] == "succeeded"),
                    None)

    def events(self, order_id):
        return [(r["type"], r["status"]) for r in self.rows if r["order_id"] == order_id]


class FakeOrderRepoWithRefund(FakeOrderRepo):
    def mark_refunded(self, order_id, user_id):
        o = self.orders.get(order_id)
        if o is None or o.status != "paid" or o.user_id != user_id:
            return False
        o.status = "refunded"
        return True


class FakeGateway:
    name = "fake"

    def __init__(self):
        self.refunds = []
        self.fail = False

    def refund(self, payment_id, amount, reason, payment_raw=""):
        self.refunds.append((payment_id, amount, reason))
        if self.fail:
            raise PaymentGatewayError("gateway down")
        return RefundResult(True, {"refund_id": "rf_1"})


@pytest.fixture
def uow():
    concerts = [SimpleNamespace(id=1, sale_open_time=NOW - timedelta(hours=1), start_time=START)]
    ticket_types = [SimpleNamespace(id=10, concert_id=1, remaining=3, price=Decimal("100"))]
    uow = FakeUnitOfWork(FakeOrderRepoWithRefund(), FakeTicketTypeRepo(ticket_types), FakeConcertRepo(concerts))
    uow.tickets = FakeTicketRepo()
    uow.transactions = FakeTransactionRepo()
    return uow


@pytest.fixture
def gateway():
    return FakeGateway()


@pytest.fixture
def clock():
    return SimpleNamespace(now=NOW)


@pytest.fixture
def svc(uow, gateway, clock):
    return PaymentService(lambda: uow, gateway, lambda ticket_id: f"T{ticket_id}",
                          clock=lambda: clock.now, refund_cutoff_hours=24)


def paid_order(uow, user_id=1, quantity=2):
    order = uow.orders.create(user_id=user_id, ticket_type_id=10, quantity=quantity,
                              unit_price=Decimal("100"), total_amount=Decimal("100") * quantity,
                              expires_at=NOW + timedelta(minutes=12))
    order.status, order.paid_at = "paid", NOW
    uow.ticket_types.items[10].remaining -= quantity
    uow.transactions.record(order_id=order.id, payment_id="pay_1", provider="fake", type="payment",
                            status="pending", amount=order.total_amount, raw_log="{}")
    uow.transactions.record(order_id=order.id, payment_id="pay_1", provider="fake", type="payment",
                            status="succeeded", amount=order.total_amount, raw_log="{}")
    return order


def test_refund_returns_stock_logs_and_calls_gateway(svc, uow, gateway):
    order = paid_order(uow)
    result = svc.refund_order(1, order.id)

    assert result.status == "refunded" and uow.orders.get_by_id(order.id).status == "refunded"
    assert uow.ticket_types.items[10].remaining == 3
    assert uow.tickets.released == [(order.id, "sold")]
    assert gateway.refunds == [("pay_1", Decimal("200"), "cancelled by customer")]
    assert uow.transactions.events(order.id) == [("payment", "pending"), ("payment", "succeeded"),
                                                 ("refund", "pending"), ("refund", "succeeded")]
    assert uow.commits == 2  # một transaction hoàn kho, một transaction ghi kết quả cổng


def test_refund_window_boundary(svc, uow, gateway, clock):
    order = paid_order(uow)
    clock.now = START - timedelta(hours=23, minutes=59)
    with pytest.raises(RefundWindowClosed):
        svc.refund_order(1, order.id)
    assert uow.orders.get_by_id(order.id).status == "paid"
    assert uow.ticket_types.items[10].remaining == 1 and gateway.refunds == []

    clock.now = START - timedelta(hours=24)  # đúng mốc vẫn được
    assert svc.refund_order(1, order.id).status == "refunded"


def test_refund_rejects_non_paid_orders(svc, uow, gateway):
    order = paid_order(uow)
    order.status = "held"
    with pytest.raises(InvalidState, match="already held"):
        svc.refund_order(1, order.id)
    order.status = "refunded"
    with pytest.raises(InvalidState, match="already refunded"):
        svc.refund_order(1, order.id)
    assert gateway.refunds == [] and uow.tickets.released == []


def test_refund_checks_owner_and_existence(svc, uow):
    order = paid_order(uow, user_id=1)
    with pytest.raises(Forbidden):
        svc.refund_order(2, order.id)
    with pytest.raises(NotFound):
        svc.refund_order(1, 999)


def test_refund_lost_race_does_not_release(svc, uow, gateway):
    """Trạng thái đọc là paid nhưng UPDATE có điều kiện thất bại (request khác vừa thắng)."""
    order = paid_order(uow)
    uow.orders.mark_refunded = lambda order_id, user_id: False
    with pytest.raises(InvalidState, match="no longer paid"):
        svc.refund_order(1, order.id)
    assert uow.tickets.released == [] and uow.ticket_types.items[10].remaining == 1
    assert gateway.refunds == [] and uow.commits == 0


def test_refund_gateway_failure_still_refunds_order_and_logs_failure(svc, uow, gateway):
    order = paid_order(uow)
    gateway.fail = True
    assert svc.refund_order(1, order.id).status == "refunded"
    assert uow.ticket_types.items[10].remaining == 3
    assert uow.transactions.events(order.id)[-2:] == [("refund", "pending"), ("refund", "failed")]
