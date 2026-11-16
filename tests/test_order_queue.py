"""Đặt vé bất đồng bộ: Asynchronous Request-Reply, hàng đợi FIFO, worker và poison message."""
import threading
import time

import pytest

from app import config
from app.api import deps
from app.order_worker import OrderRequestWorker
from app.repositories import database
from app.repositories.models import OrderRequest
from app.services.order_service import OrderService
from tests.test_api import auth_header, client, drain_order_queue  # noqa: F401  (fixture dùng chung)

BODY = {"ticket_type_id": 1, "quantity": 2}


def remaining(client):
    return client.get("/concerts/1/ticket-types").json()[0]["remaining"]


def test_submit_returns_202_with_status_location(client):
    h = auth_header(client)
    r = client.post("/orders", json=BODY, headers=h)

    assert r.status_code == 202
    request = r.json()
    assert request["status"] == "pending" and request["order_id"] is None
    assert r.headers["Location"] == f"/orders/requests/{request['id']}"
    assert r.headers["Retry-After"] == str(config.ORDER_STATUS_RETRY_AFTER_SECONDS)
    # Chưa xử lý thì chưa trừ kho: request HTTP không chạm vào dòng tồn kho.
    assert remaining(client) == 3

    status = client.get(r.headers["Location"], headers=h)
    assert status.status_code == 200 and status.json()["status"] == "pending"
    assert "Retry-After" in status.headers


def test_status_reports_order_once_processed(client):
    h = auth_header(client)
    location = client.post("/orders", json=BODY, headers=h).headers["Location"]
    assert drain_order_queue() == 1

    status = client.get(location, headers=h)
    body = status.json()
    assert status.status_code == 200 and "Retry-After" not in status.headers
    assert body["status"] == "succeeded" and body["completed_at"] is not None
    assert body["order"]["id"] == body["order_id"]
    assert body["order"]["status"] == "held" and body["order"]["quantity"] == 2
    assert remaining(client) == 1


def test_business_failure_is_reported_on_status(client):
    h = auth_header(client)
    with database.SessionLocal() as db:  # nới tồn kho để chạm giới hạn giữ vé, không phải hết vé
        from tests.conftest import add_ticket_type
        add_ticket_type(db, id=2, concert_id=1, name="Big", price=10, total_quantity=50)
        db.commit()
    client.post("/orders", json={"ticket_type_id": 2, "quantity": 9}, headers=h)
    location = client.post("/orders", json={"ticket_type_id": 2, "quantity": 2}, headers=h).headers["Location"]
    drain_order_queue()

    body = client.get(location, headers=h).json()
    assert body["status"] == "failed" and body["error"] == "HoldLimitExceeded"
    assert body["order"] is None and body["order_id"] is None


def test_queue_is_first_come_first_served(client):
    first, second = auth_header(client, "first@example.com"), auth_header(client, "second@example.com")
    first_location = client.post("/orders", json={"ticket_type_id": 1, "quantity": 3}, headers=first).headers["Location"]
    second_location = client.post("/orders", json={"ticket_type_id": 1, "quantity": 3}, headers=second).headers["Location"]
    drain_order_queue()

    assert client.get(first_location, headers=first).json()["status"] == "succeeded"
    assert client.get(second_location, headers=second).json()["error"] == "SoldOut"


def test_status_of_other_users_request_is_forbidden(client):
    owner, other = auth_header(client, "owner@example.com"), auth_header(client, "other@example.com")
    location = client.post("/orders", json=BODY, headers=owner).headers["Location"]
    assert client.get(location, headers=other).status_code == 403
    assert client.get("/orders/requests/9999", headers=owner).status_code == 404


def test_poison_request_is_failed_after_max_attempts(client, monkeypatch):
    h = auth_header(client)
    location = client.post("/orders", json=BODY, headers=h).headers["Location"]
    good = client.post("/orders", json={"ticket_type_id": 1, "quantity": 1}, headers=h).headers["Location"]

    real_place_in = OrderService.place_in

    def crash_on_first_request(self, uow, user_id, ticket_type_id, quantity, key=None):
        if key == "request:1":
            raise RuntimeError("bug khi xử lý")
        return real_place_in(self, uow, user_id, ticket_type_id, quantity, key)

    monkeypatch.setattr(OrderService, "place_in", crash_on_first_request)
    svc = deps.get_order_request_service()
    for attempt in range(1, config.ORDER_REQUEST_MAX_ATTEMPTS):
        assert svc.process_next()
        body = client.get(location, headers=h).json()
        assert body["status"] == "pending"  # chưa đủ số lần: còn được thử lại
    assert svc.process_next()

    body = client.get(location, headers=h).json()
    assert body["status"] == "failed" and body["error"] == "ProcessingFailed"
    with database.SessionLocal() as db:
        assert db.get(OrderRequest, 1).attempts == config.ORDER_REQUEST_MAX_ATTEMPTS
    # Yêu cầu lỗi không còn chặn đầu hàng đợi: yêu cầu phía sau được xử lý.
    drain_order_queue()
    assert client.get(good, headers=h).json()["status"] == "succeeded"
    assert remaining(client) == 2


def test_background_worker_wakes_up_on_submit(client):
    """Worker thật chạy trên luồng nền; tín hiệu đánh thức làm nó xử lý ngay, không chờ hết chu kỳ."""
    processed = threading.Event()
    svc = deps.get_order_request_service()

    def process_next():
        done = svc.process_next()
        if done:
            processed.set()
        return done

    worker = OrderRequestWorker(process_next, threads=1, poll_seconds=30)
    worker.start()
    try:
        h = auth_header(client)
        started = time.monotonic()
        location = client.post("/orders", json=BODY, headers=h).headers["Location"]
        assert processed.wait(5)
        assert time.monotonic() - started < 5
    finally:
        worker.stop()
    assert client.get(location, headers=h).json()["status"] == "succeeded"


@pytest.mark.parametrize("quantity", [0, 11])
def test_invalid_quantity_is_rejected_before_queueing(client, quantity):
    h = auth_header(client)
    assert client.post("/orders", json={"ticket_type_id": 1, "quantity": quantity}, headers=h).status_code == 422
    assert drain_order_queue() == 0
