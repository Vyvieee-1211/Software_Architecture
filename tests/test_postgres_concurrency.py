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
from app.repositories.models import Base, Concert, Order, OrderRequest, Ticket, TicketType, User
from app.repositories.resilience import CircuitBreaker, DatabasePolicy
from app.repositories.unit_of_work import SqlAlchemyUnitOfWork
from app.services.errors import HoldLimitExceeded, InvalidState, SoldOut
from app.services.order_request_service import OrderRequestService
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


def test_parallel_sweepers_expire_each_hold_once(sessions):
    """Bốn sweeper (như bốn tiến trình API) cùng quét 20 đơn quá hạn trên nhiều hạng vé:
    SKIP LOCKED chia việc, mỗi đơn hết hạn đúng một lần, kho về đủ, không deadlock."""
    with sessions() as db:
        add_ticket_type(db, concert_id=1, name="VIP", price=Decimal("200"), total_quantity=20)
        db.commit()
    orders = make_order_service(sessions)
    for i in range(20):
        orders.create_order(user_id=i + 1, ticket_type_id=1 + i % 2, quantity=1)
    with sessions() as db:
        db.execute(text("UPDATE orders SET expires_at = now() at time zone 'utc' - interval '1 second'"))
        db.commit()

    outcomes = run_parallel(4, lambda i: orders.expire_holds(batch_size=3))
    assert all(kind == "ok" for kind, _ in outcomes), outcomes
    assert sum(count for _, count in outcomes) == 20
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Order).where(Order.status == "expired")) == 20
        remaining = dict(db.execute(select(TicketType.id, TicketType.remaining)).all())
        available = db.scalar(select(func.count()).select_from(Ticket).where(Ticket.status == "available"))
    assert remaining == {1: 20, 2: 20} and available == 40


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


def test_duplicate_refund_in_parallel_refunds_once(sessions):
    """10 request hủy đơn đã thanh toán cùng lúc: một request hoàn kho và gọi cổng, còn lại InvalidState."""
    gateway = MockPaymentGateway("secret", "http://test")
    refund_calls = []
    original_refund = gateway.refund
    gateway.refund = lambda *args: (refund_calls.append(args), original_refund(*args))[1]
    orders = make_order_service(sessions)
    payments = PaymentService(lambda: SqlAlchemyUnitOfWork(sessions), gateway,
                              TicketCodes("qr").issue, execute=policy().run, refund_cutoff_hours=1)
    order = orders.create_order(user_id=1, ticket_type_id=1, quantity=3)
    payment = payments.start_payment(1, order.id)
    headers, body = gateway.build_webhook(payment.payment_id, Decimal("300"), True)
    assert payments.handle_webhook(headers, body).result == "paid"

    outcomes = run_parallel(10, lambda i: payments.refund_order(user_id=1, order_id=order.id))

    assert sum(1 for kind, _ in outcomes if kind == "ok") == 1, [repr(e) for kind, e in outcomes if kind == "err"]
    assert all(isinstance(e, InvalidState) for kind, e in outcomes if kind == "err"), outcomes
    assert len(refund_calls) == 1
    remaining, active, total, by_status = inventory(sessions)
    assert remaining == 20 and active == 0 and by_status == {"available": 20}
    with sessions() as db:
        assert db.get(Order, order.id).status == "refunded"
        assert db.scalar(select(func.count()).select_from(Ticket).where(Ticket.qr_code.is_not(None))) == 0


def test_idempotent_hold_reserves_stock_and_replays(sessions):
    """Đặt vé kèm Idempotency-Key trên PostgreSQL thật: lần đầu trừ kho và gán vé, gửi lại trả đúng đơn cũ."""
    svc = make_order_service(sessions)
    first, created = svc.place_order(1, 1, 2, idempotency_key="key-123456789")
    assert created and first.status == "held"
    remaining, active, total, by_status = inventory(sessions)
    assert remaining == 18 and active == 2 and by_status == {"available": 18, "held": 2}

    outcomes = run_parallel(10, lambda i: svc.place_order(1, 1, 2, idempotency_key="key-123456789"))
    assert all(kind == "ok" and o.id == first.id and not created for kind, (o, created) in outcomes), outcomes
    assert inventory(sessions)[:2] == (18, 2)


# --- Hàng đợi đặt vé: Queue-Based Load Leveling + Competing Consumers ------------

def make_queue_service(sessions):
    uow = lambda: SqlAlchemyUnitOfWork(sessions)  # noqa: E731
    return OrderRequestService(uow, OrderService(uow, execute=policy().run), execute=policy().run)


def test_competing_workers_process_each_request_once(sessions):
    """100 người gửi yêu cầu cùng lúc, 8 worker tranh nhau xử lý: đúng 20 đơn, không bán vượt,
    mỗi yêu cầu có đúng một kết quả."""
    svc = make_queue_service(sessions)
    submitted = run_parallel(100, lambda i: svc.submit(user_id=i + 1, ticket_type_id=1, quantity=1)[0].id)
    assert all(kind == "ok" for kind, _ in submitted), [o for kind, o in submitted if kind == "err"][:3]

    processed = run_parallel(8, lambda i: svc.drain())
    assert all(kind == "ok" for kind, _ in processed), processed
    assert sum(count for _, count in processed) == 100  # không yêu cầu nào bị xử lý hai lần

    with sessions() as db:
        rows = db.execute(select(OrderRequest.id, OrderRequest.status, OrderRequest.error)
                          .order_by(OrderRequest.id)).all()
        statuses = [status for _, status, _ in rows]
        assert statuses.count("succeeded") == 20 and statuses.count("failed") == 80
        assert {error for _, status, error in rows if status == "failed"} == {"SoldOut"}
        assert db.scalar(select(func.count()).select_from(Order)) == 20
    remaining, active, total, by_status = inventory(sessions)
    assert remaining == 0 and active == 20 == total and by_status == {"held": 20}


def test_same_key_burst_then_competing_workers_create_one_order(sessions):
    svc = make_queue_service(sessions)
    submitted = run_parallel(20, lambda i: svc.submit(1, 1, 2, idempotency_key="retry-key-1")[0].id)
    assert {request_id for _, request_id in submitted} == {submitted[0][1]}

    run_parallel(4, lambda i: svc.drain())
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(OrderRequest)) == 1
        assert db.scalar(select(func.count()).select_from(Order)) == 1
    assert inventory(sessions)[:2] == (18, 2)
