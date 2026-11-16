"""Chống tạo đơn trùng khi client retry POST /orders (timeout, mất mạng, bấm 2 lần).

Với Asynchronous Request-Reply, POST /orders chỉ xếp yêu cầu vào hàng đợi; gửi lại cùng
Idempotency-Key trả về ĐÚNG yêu cầu cũ (cùng id, cùng địa chỉ trạng thái), nên dù client
retry bao nhiêu lần cũng chỉ có một đơn.
"""
import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.repositories.database import init_db
from app.repositories.models import Concert, Order, OrderRequest, TicketType, User
from app.repositories.resilience import CircuitBreaker, DatabasePolicy
from app.repositories.unit_of_work import SqlAlchemyUnitOfWork
from app.services.order_request_service import OrderRequestService
from app.services.order_service import OrderService
from tests.conftest import add_ticket_type
from tests.test_api import auth_header, client, drain_order_queue  # noqa: F401  (fixture dùng chung)

KEY = "3f6c1b8e-6a4d-4c1e-9d3b-0f2a7c9e5b11"


def remaining(client):
    return client.get("/concerts/1/ticket-types").json()[0]["remaining"]


def submit(client, headers, quantity=2, ticket_type_id=1):
    return client.post("/orders", json={"ticket_type_id": ticket_type_id, "quantity": quantity},
                       headers=headers)


def test_retry_with_same_key_returns_original_request(client):
    headers = {**auth_header(client), "Idempotency-Key": KEY}

    first = submit(client, headers)
    assert first.status_code == 202
    assert "Idempotent-Replayed" not in first.headers

    # Timeout phía client → gửi lại: cùng yêu cầu, không xếp thêm.
    retry = submit(client, headers)
    assert retry.status_code == 202
    assert retry.headers["Idempotent-Replayed"] == "true"
    assert retry.json()["id"] == first.json()["id"]
    assert retry.headers["Location"] == first.headers["Location"]

    assert drain_order_queue() == 1
    assert remaining(client) == 1
    assert len(client.get("/orders/me", headers=headers).json()) == 1

    # Gửi lại sau khi đã xử lý xong: vẫn là yêu cầu đó, giờ đã có đơn.
    late = submit(client, headers)
    assert late.headers["Idempotent-Replayed"] == "true"
    assert late.json()["status"] == "succeeded" and late.json()["id"] == first.json()["id"]
    assert drain_order_queue() == 0
    assert remaining(client) == 1


def test_retry_after_cancel_does_not_book_again(client):
    headers = {**auth_header(client), "Idempotency-Key": KEY}
    location = submit(client, headers).headers["Location"]
    drain_order_queue()
    order_id = client.get(location, headers=headers).json()["order_id"]
    assert client.delete(f"/orders/{order_id}", headers=headers).status_code == 200

    retry = submit(client, headers)
    assert retry.headers["Idempotent-Replayed"] == "true"
    assert retry.json()["order_id"] == order_id
    assert drain_order_queue() == 0
    assert client.get(f"/orders/{order_id}", headers=headers).json()["status"] == "cancelled"
    assert remaining(client) == 3


def test_same_key_with_different_payload_is_rejected(client):
    headers = {**auth_header(client), "Idempotency-Key": KEY}
    assert submit(client, headers, quantity=1).status_code == 202

    response = submit(client, headers, quantity=2)
    assert response.status_code == 409
    assert response.json()["error"] == "IdempotencyKeyReused"
    drain_order_queue()
    assert remaining(client) == 2
    assert len(client.get("/orders/me", headers=headers).json()) == 1


def test_same_key_from_different_users_creates_separate_orders(client):
    for email in ("u1@example.com", "u2@example.com"):
        headers = {**auth_header(client, email), "Idempotency-Key": KEY}
        assert submit(client, headers, quantity=1).status_code == 202
    assert drain_order_queue() == 2
    assert remaining(client) == 1


def test_rejected_at_submit_leaves_key_free_for_a_later_retry(client):
    headers = {**auth_header(client), "Idempotency-Key": KEY}
    sold_out = submit(client, headers, quantity=5)
    assert sold_out.status_code == 409 and sold_out.json()["error"] == "SoldOut"

    # Không có gì được lưu: cùng khóa, nội dung đã sửa, được nhận như yêu cầu mới.
    retry = submit(client, headers, quantity=3)
    assert retry.status_code == 202 and "Idempotent-Replayed" not in retry.headers
    drain_order_queue()
    assert remaining(client) == 0


def test_failure_decided_by_worker_is_replayed_not_requeued(client):
    first = {**auth_header(client, "first@example.com"), "Idempotency-Key": KEY}
    second = {**auth_header(client, "second@example.com"), "Idempotency-Key": KEY}
    submit(client, first, quantity=3)
    late = submit(client, second, quantity=3)  # lúc gửi vẫn còn vé nên được xếp hàng
    assert late.status_code == 202

    drain_order_queue()
    result = client.get(late.headers["Location"], headers=second).json()
    assert result["status"] == "failed" and result["error"] == "SoldOut"

    # Cùng yêu cầu → cùng kết quả: gửi lại không xếp hàng lần nữa.
    retry = submit(client, second, quantity=3)
    assert retry.headers["Idempotent-Replayed"] == "true"
    assert retry.json()["status"] == "failed"
    assert drain_order_queue() == 0


def test_requests_without_key_are_independent(client):
    headers = auth_header(client)
    first, second = submit(client, headers, quantity=1), submit(client, headers, quantity=1)
    assert first.json()["id"] != second.json()["id"]
    assert drain_order_queue() == 2
    assert remaining(client) == 1


@pytest.mark.parametrize("key", ["short", "x" * 65, "has space in key", "bad/slash-key"])
def test_invalid_idempotency_key_is_rejected(client, key):
    headers = {**auth_header(client), "Idempotency-Key": key}
    response = submit(client, headers, quantity=1)
    assert response.status_code == 422
    assert drain_order_queue() == 0
    assert remaining(client) == 3


# --- Đồng thời thật: nhiều kết nối/luồng trên SQLite dạng file ------------------

@pytest.fixture
def file_db(tmp_path):
    """DB SQLite dạng file để nhiều connection/thread ghi song song như môi trường thật."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'race.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    init_db(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with sessions() as db:
        concert = Concert(name="Race", start_time=now + timedelta(days=1), sale_open_time=now - timedelta(hours=1))
        db.add_all([concert, User(email="race@example.com", password_hash="x")])
        db.flush()
        add_ticket_type(db, concert_id=concert.id, name="GA", price=1, total_quantity=100)
        db.commit()
    yield sessions
    engine.dispose()


def run_parallel(workers, fn):
    barrier = threading.Barrier(workers)
    results, failures = [], []

    def run():
        try:
            barrier.wait()
            results.append(fn())
        except Exception as exc:  # pragma: no cover - chỉ để báo lỗi rõ ràng
            failures.append(exc)

    threads = [threading.Thread(target=run) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert failures == []
    return results


def queue_service(sessions):
    policy = DatabasePolicy(CircuitBreaker(threshold=1000, window=60, recovery=1), max_attempts=10)
    uow = lambda: SqlAlchemyUnitOfWork(sessions)  # noqa: E731
    return OrderRequestService(uow, OrderService(uow, execute=policy.run), execute=policy.run)


def test_concurrent_retries_with_same_key_create_exactly_one_order(file_db):
    """8 lần gửi cùng khóa đến đồng thời → 1 yêu cầu; 4 worker xử lý song song → 1 đơn."""
    svc = queue_service(file_db)
    submitted = run_parallel(8, lambda: svc.submit(1, 1, 3, idempotency_key=KEY))
    assert len({request.id for request, _ in submitted}) == 1
    assert sum(created for _, created in submitted) == 1

    run_parallel(4, svc.drain)

    with file_db() as db:
        assert db.scalar(select(func.count()).select_from(OrderRequest)) == 1
        assert db.scalar(select(func.count()).select_from(Order)) == 1
        assert db.scalar(select(OrderRequest.status)) == "succeeded"
        assert db.get(TicketType, 1).remaining == 97


def test_order_service_concurrent_same_key_still_creates_one_order(file_db):
    """Lớp bảo vệ thứ hai: chính OrderService vẫn idempotent theo khóa của đơn."""
    svc = OrderService(lambda: SqlAlchemyUnitOfWork(file_db))
    results = run_parallel(8, lambda: svc.place_order(1, 1, 3, idempotency_key=KEY))
    assert len({order.id for order, _ in results}) == 1
    assert sum(created for _, created in results) == 1
    with file_db() as db:
        assert db.scalar(select(func.count()).select_from(Order)) == 1
        assert db.get(TicketType, 1).remaining == 97
