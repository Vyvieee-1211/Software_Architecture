"""Chống tạo đơn trùng khi client retry POST /orders (timeout, mất mạng, bấm 2 lần)."""
import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.repositories.database import init_db
from app.repositories.models import Concert, Order, TicketType, User
from app.repositories.unit_of_work import SqlAlchemyUnitOfWork
from app.services.order_service import OrderService
from tests.conftest import add_ticket_type
from tests.test_api import auth_header, client  # noqa: F401  (fixture dùng chung)

KEY = "3f6c1b8e-6a4d-4c1e-9d3b-0f2a7c9e5b11"


def remaining(client):
    return client.get("/concerts/1/ticket-types").json()[0]["remaining"]


def test_retry_with_same_key_returns_original_order(client):
    headers = {**auth_header(client), "Idempotency-Key": KEY}
    body = {"ticket_type_id": 1, "quantity": 2}

    first = client.post("/orders", json=body, headers=headers)
    assert first.status_code == 201
    assert "Idempotent-Replayed" not in first.headers

    retry = client.post("/orders", json=body, headers=headers)
    assert retry.status_code == 200
    assert retry.headers["Idempotent-Replayed"] == "true"
    assert retry.json() == first.json()

    assert client.get("/orders/me", headers=headers).json() == [first.json()]
    assert remaining(client) == 1


def test_retry_after_cancel_does_not_book_again(client):
    headers = {**auth_header(client), "Idempotency-Key": KEY}
    body = {"ticket_type_id": 1, "quantity": 2}
    order = client.post("/orders", json=body, headers=headers).json()
    assert client.delete(f"/orders/{order['id']}", headers=headers).status_code == 200

    retry = client.post("/orders", json=body, headers=headers)
    assert retry.status_code == 200
    assert retry.json()["status"] == "cancelled"
    assert remaining(client) == 3


def test_same_key_with_different_payload_is_rejected(client):
    headers = {**auth_header(client), "Idempotency-Key": KEY}
    assert client.post("/orders", json={"ticket_type_id": 1, "quantity": 1}, headers=headers).status_code == 201

    response = client.post("/orders", json={"ticket_type_id": 1, "quantity": 2}, headers=headers)
    assert response.status_code == 409
    assert response.json()["error"] == "IdempotencyKeyReused"
    assert remaining(client) == 2
    assert len(client.get("/orders/me", headers=headers).json()) == 1


def test_same_key_from_different_users_creates_separate_orders(client):
    body = {"ticket_type_id": 1, "quantity": 1}
    for email in ("u1@example.com", "u2@example.com"):
        headers = {**auth_header(client, email), "Idempotency-Key": KEY}
        assert client.post("/orders", json=body, headers=headers).status_code == 201
    assert remaining(client) == 1


def test_sold_out_attempt_leaves_key_free_for_a_later_retry(client):
    headers = {**auth_header(client), "Idempotency-Key": KEY}
    body = {"ticket_type_id": 1, "quantity": 5}
    sold_out = client.post("/orders", json=body, headers=headers)
    assert sold_out.status_code == 409 and sold_out.json()["error"] == "SoldOut"
    assert client.get("/orders/me", headers=headers).json() == []
    assert remaining(client) == 3


def test_requests_without_key_keep_old_behaviour(client):
    headers = auth_header(client)
    body = {"ticket_type_id": 1, "quantity": 1}
    assert client.post("/orders", json=body, headers=headers).status_code == 201
    assert client.post("/orders", json=body, headers=headers).status_code == 201
    assert remaining(client) == 1


@pytest.mark.parametrize("key", ["short", "x" * 65, "has space in key", "bad/slash-key"])
def test_invalid_idempotency_key_is_rejected(client, key):
    headers = {**auth_header(client), "Idempotency-Key": key}
    response = client.post("/orders", json={"ticket_type_id": 1, "quantity": 1}, headers=headers)
    assert response.status_code == 422
    assert remaining(client) == 3


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


def test_concurrent_retries_with_same_key_create_exactly_one_order(file_db):
    workers = 8
    barrier = threading.Barrier(workers)
    results, failures = [], []

    def buy():
        try:
            # Service tự mở UoW, commit và đóng session cho từng lần đặt.
            svc = OrderService(lambda: SqlAlchemyUnitOfWork(file_db))
            barrier.wait()
            order, created = svc.place_order(1, 1, 3, idempotency_key=KEY)
            results.append((order.id, created))
        except Exception as exc:  # pragma: no cover - chỉ để báo lỗi rõ ràng
            failures.append(exc)

    threads = [threading.Thread(target=buy) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []
    assert len({order_id for order_id, _ in results}) == 1
    assert sum(created for _, created in results) == 1
    with file_db() as db:
        assert db.scalar(select(func.count()).select_from(Order)) == 1
        assert db.get(TicketType, 1).remaining == 97

