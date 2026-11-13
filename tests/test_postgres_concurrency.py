"""Kiểm thử đồng thời trên PostgreSQL THẬT: nhiều luồng, nhiều kết nối, khóa dòng thật.

Chạy:  TEST_DATABASE_URL=postgresql+psycopg://concert:concert@localhost:5432/concert_test \
       pytest tests/test_postgres_concurrency.py
Bỏ qua khi không đặt TEST_DATABASE_URL. Mỗi test tạo lại lược đồ từ đầu.
"""
import os
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from app.integrations.mock_payment import MockPaymentGateway
from app.repositories.models import Base, Concert, Order, Ticket, TicketType, User
from app.repositories.resilience import CircuitBreaker, DatabasePolicy
from app.repositories.unit_of_work import SqlAlchemyUnitOfWork
from app.services.errors import HoldLimitExceeded, InvalidState, SoldOut
from app.services.order_service import OrderService
from app.services.payment_service import PaymentService
from app.services.ticket_codes import TicketCodes
from tests.conftest import add_ticket_type

URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL or not URL.startswith("postgresql"),
                                reason="TEST_DATABASE_URL (PostgreSQL) chưa được đặt")


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def sessions():
    engine = create_engine(URL, pool_size=20, max_overflow=100)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as db:
        concert = Concert(name="Race", start_time=now() + timedelta(days=1),
                          sale_open_time=now() - timedelta(hours=1))
        db.add(concert)
        db.add_all([User(email=f"u{i}@example.com", password_hash="x") for i in range(1, 201)])
        db.flush()
        add_ticket_type(db, concert_id=concert.id, name="GA", price=Decimal("100"), total_quantity=20)
        db.commit()
    yield factory
    engine.dispose()


def policy():
    return DatabasePolicy(CircuitBreaker(threshold=1000, window=60, recovery=1), max_attempts=5)


def make_order_service(sessions, **kw):
    return OrderService(lambda: SqlAlchemyUnitOfWork(sessions), execute=policy().run, **kw)


def run_parallel(workers, fn):
    barrier = threading.Barrier(workers)
    outcomes = []

    def worker(i):
        barrier.wait()
        try:
            outcomes.append(("ok", fn(i)))
        except Exception as exc:  # noqa: BLE001 — thu lại để phân loại
            outcomes.append(("err", exc))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return outcomes


def inventory(sessions):
    with sessions() as db:
        tt = db.get(TicketType, 1)
        active = db.scalar(select(func.coalesce(func.sum(Order.quantity), 0))
                           .where(Order.status.in_(["held", "paid"])))
        by_status = dict(db.execute(select(Ticket.status, func.count()).group_by(Ticket.status)).all())
        return tt.remaining, active, tt.total_quantity, by_status


def test_burst_of_holds_never_oversells(sessions):
    svc = make_order_service(sessions)
    outcomes = run_parallel(100, lambda i: svc.create_order(user_id=i + 1, ticket_type_id=1, quantity=1))

    ok = [o for kind, o in outcomes if kind == "ok"]
    errors = [e for kind, e in outcomes if kind == "err"]
    assert len(ok) == 20, [type(e).__name__ for e in errors][:5]
    assert all(isinstance(e, SoldOut) for e in errors), {type(e).__name__ for e in errors}

    remaining, active, total, by_status = inventory(sessions)
    assert remaining == 0 and active == 20 == total
    assert by_status == {"held": 20}
    with sessions() as db:  # mỗi vé gán cho đúng một đơn, không vé nào gán hai lần
        assert db.scalar(select(func.count(func.distinct(Ticket.order_id)))) == 20


def test_duplicate_cancellation_refunds_once(sessions):
    svc = make_order_service(sessions)
    order = svc.create_order(user_id=1, ticket_type_id=1, quantity=3)
    outcomes = run_parallel(10, lambda i: svc.cancel_order(user_id=1, order_id=order.id))

    assert sum(1 for kind, _ in outcomes if kind == "ok") == 1
    assert all(isinstance(e, InvalidState) for kind, e in outcomes if kind == "err")
    remaining, active, total, by_status = inventory(sessions)
    assert remaining == 20 and active == 0 and by_status == {"available": 20}


def test_hold_limit_cannot_be_bypassed_in_parallel(sessions):
    svc = make_order_service(sessions, max_held_per_user=10)
    with sessions() as db:
        db.execute(text("UPDATE ticket_types SET total_quantity = 1000, remaining = 1000"))
        db.commit()
        from app.repositories.ticket_repo import TicketRepository
        TicketRepository(db).generate(1, 980)
        db.commit()
    outcomes = run_parallel(20, lambda i: svc.create_order(user_id=1, ticket_type_id=1, quantity=4))

    held = sum(o.quantity for kind, o in outcomes if kind == "ok")
    assert held <= 10 and held >= 8  # 2 đơn x 4 vé lọt, đơn thứ 3 bị chặn
    assert all(isinstance(e, HoldLimitExceeded) for kind, e in outcomes if kind == "err")


def test_payment_and_expiry_race(sessions):
    """Đơn hết hạn đúng lúc tiền về: chỉ MỘT trong hai thắng; không bao giờ vừa xuất vé vừa hoàn kho."""
    gateway = MockPaymentGateway("secret", "http://test")
    codes = TicketCodes("qr-secret")
    orders = make_order_service(sessions)
    payments = PaymentService(lambda: SqlAlchemyUnitOfWork(sessions), gateway, codes.issue,
                              execute=policy().run)

    for trial in range(10):
        order = orders.create_order(user_id=trial + 1, ticket_type_id=1, quantity=1)
        payment = payments.start_payment(trial + 1, order.id)
        with sessions() as db:
            db.get(Order, order.id).expires_at = now()
            db.commit()
        headers, body = gateway.build_webhook(payment.payment_id, Decimal("100"), True)

        outcomes = run_parallel(2, lambda i: payments.handle_webhook(headers, body) if i == 0
                                else orders.expire_holds())
        assert all(kind == "ok" for kind, _ in outcomes), outcomes

        with sessions() as db:
            status = db.get(Order, order.id).status
            sold = db.scalar(select(func.count()).select_from(Ticket)
                             .where(Ticket.order_id == order.id, Ticket.status == "sold"))
        assert status in ("paid", "expired")
        assert sold == (1 if status == "paid" else 0)

    remaining, active, total, by_status = inventory(sessions)
    assert remaining + active == total
    assert by_status.get("available", 0) == remaining and by_status.get("held", 0) == 0


def test_duplicate_webhooks_in_parallel_apply_once(sessions):
    gateway = MockPaymentGateway("secret", "http://test")
    orders = make_order_service(sessions)
    payments = PaymentService(lambda: SqlAlchemyUnitOfWork(sessions), gateway,
                              TicketCodes("qr").issue, execute=policy().run)
    order = orders.create_order(user_id=1, ticket_type_id=1, quantity=2)
    payment = payments.start_payment(1, order.id)
    headers, body = gateway.build_webhook(payment.payment_id, Decimal("200"), True)

    outcomes = run_parallel(8, lambda i: payments.handle_webhook(headers, body))
    results = sorted(o.result for kind, o in outcomes if kind == "ok")
    assert all(kind == "ok" for kind, _ in outcomes)
    assert results == ["duplicate"] * 7 + ["paid"]
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Ticket).where(Ticket.status == "sold")) == 2
