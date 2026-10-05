import os
from datetime import datetime, timedelta, timezone

os.environ["DATABASE_URL"] = "sqlite://"

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import deps
from app.config import JWT_SECRET
from app.main import app
from app.repositories import database
from app.repositories.models import Base, Concert, TicketType


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    monkeypatch.setattr(deps, "SessionLocal", sessions)
    Base.metadata.create_all(engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with database.SessionLocal() as db:
        c = Concert(name="Test Concert", artist="A", venue="V",
                    start_time=now + timedelta(days=10), sale_open_time=now - timedelta(hours=1))
        db.add(c)
        db.flush()
        db.add(TicketType(concert_id=c.id, name="GA", price=100, total_quantity=3, remaining=3))
        db.commit()

    try:
        with TestClient(app) as c:
            yield c
    finally:
        engine.dispose()


def auth_header(client, email="a@example.com"):
    registered = client.post("/auth/register", json={"email": email, "password": "secret123", "full_name": "A"})
    assert registered.status_code == 201
    logged_in = client.post("/auth/login", json={"email": email, "password": "secret123"})
    assert logged_in.status_code == 200
    token = logged_in.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_login_success_returns_jwt(client):
    """AUTH-01: đăng nhập đúng trả 200 và JWT dùng được trên API bảo vệ."""
    registered = client.post(
        "/auth/register", json={"email": "login@example.com", "password": "secret123"},
    )
    assert registered.status_code == 201

    response = client.post(
        "/auth/login", json={"email": "login@example.com", "password": "secret123"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert isinstance(body["access_token"], str) and body["access_token"]
    payload = jwt.decode(body["access_token"], JWT_SECRET, algorithms=["HS256"])
    assert payload["sub"] == str(registered.json()["id"])
    assert payload["exp"] > payload["iat"]
    assert payload["exp"] > datetime.now(timezone.utc).timestamp()

    protected = client.get("/orders/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert protected.status_code == 200
    assert protected.json() == []


@pytest.mark.parametrize("token_case", ["expired", "forged", "tampered"])
@pytest.mark.parametrize("method,path,body", [
    ("GET", "/orders/me", None),
    ("POST", "/orders", {"ticket_type_id": 1, "quantity": 1}),
    ("DELETE", "/orders/1", None),
])
def test_invalid_tokens_are_rejected(client, token_case, method, path, body):
    """AUTH-04: token hết hạn, giả mạo hoặc bị sửa đều bị chặn với 401."""
    valid_header = auth_header(client)
    created = client.post(
        "/orders", json={"ticket_type_id": 1, "quantity": 1}, headers=valid_header,
    )
    assert created.status_code == 201
    existing_order = created.json()
    valid_token = valid_header["Authorization"].removeprefix("Bearer ")
    payload = jwt.decode(valid_token, JWT_SECRET, algorithms=["HS256"])

    if token_case == "expired":
        now = datetime.now(timezone.utc)
        payload["iat"] = now - timedelta(hours=2)
        payload["exp"] = now - timedelta(hours=1)
        token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
    elif token_case == "forged":
        token = jwt.encode(payload, JWT_SECRET + "-forged", algorithm="HS256")
    else:
        # Sửa hạn dùng, giữ nguyên chủ token và chữ ký cũ để kiểm tra chữ ký.
        payload["exp"] += 3600
        modified = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
        header, encoded_payload, _ = modified.split(".")
        signature = valid_token.split(".")[2]
        token = f"{header}.{encoded_payload}.{signature}"

    response = client.request(method, path, headers={"Authorization": f"Bearer {token}"}, json=body)
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or expired token"
    # Request bị chặn không được tạo/hủy đơn hoặc thay đổi tồn kho.
    assert client.get("/orders/me", headers=valid_header).json() == [existing_order]
    assert client.get("/concerts/1/ticket-types").json()[0]["remaining"] == 2


def test_register_success_returns_user(client):
    response = client.post(
        "/auth/register",
        json={"email": "u1@example.com", "password": "secret123", "full_name": "User One"},
    )
    assert response.status_code == 201
    user = response.json()
    assert isinstance(user["id"], int) and user["id"] > 0
    assert user["email"] == "u1@example.com"
    assert user["full_name"] == "User One"
    assert datetime.fromisoformat(user["created_at"])
    assert "password" not in user
    assert "password_hash" not in user


def test_register_duplicate_email_returns_conflict(client):
    body = {"email": "u1@example.com", "password": "secret123"}
    assert client.post("/auth/register", json=body).status_code == 201

    response = client.post("/auth/register", json=body)
    assert response.status_code == 409
    assert response.json() == {"error": "EmailAlreadyExists", "detail": "u1@example.com"}


@pytest.mark.parametrize("email,password", [
    ("u1@example.com", "wrong"),
    ("missing@example.com", "secret123"),
])
def test_login_invalid_credentials_returns_unauthorized(client, email, password):
    registered = client.post(
        "/auth/register", json={"email": "u1@example.com", "password": "secret123"},
    )
    assert registered.status_code == 201

    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 401
    assert response.json()["error"] == "InvalidCredentials"
    assert "access_token" not in response.json()


def test_list_concerts_on_sale(client):
    """GET-01: trả đủ concert mở bán, loại concert chưa tới giờ mở bán."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with database.SessionLocal() as db:
        db.add_all([
            Concert(id=2, name="Another Open Concert", artist="B", venue="V2",
                    start_time=now + timedelta(days=20), sale_open_time=now - timedelta(days=1)),
            Concert(id=3, name="Upcoming Sale", artist="C", venue="V3",
                    start_time=now + timedelta(days=30), sale_open_time=now + timedelta(days=1)),
        ])
        db.commit()

    response = client.get("/concerts", params={"on_sale": "true"})
    assert response.status_code == 200
    concerts = response.json()
    assert isinstance(concerts, list)
    assert [concert["id"] for concert in concerts] == [1, 2]
    assert [concert["name"] for concert in concerts] == ["Test Concert", "Another Open Concert"]
    for concert in concerts:
        assert {"id", "name", "artist", "venue", "start_time", "sale_open_time"} <= concert.keys()
        assert datetime.fromisoformat(concert["sale_open_time"]) <= now

    # Frontend vẫn xem được mọi concert nếu không yêu cầu lọc mở bán.
    all_concerts = client.get("/concerts")
    assert all_concerts.status_code == 200
    assert [concert["id"] for concert in all_concerts.json()] == [1, 2, 3]


def test_get_concert_detail(client):
    """GET-02: concert tồn tại trả 200 và đúng các trường chi tiết."""
    with database.SessionLocal() as db:
        concert = db.get(Concert, 1)
        expected = {
            "id": concert.id,
            "name": concert.name,
            "artist": concert.artist,
            "venue": concert.venue,
            "start_time": concert.start_time.isoformat(),
            "sale_open_time": concert.sale_open_time.isoformat(),
        }

    response = client.get("/concerts/1")
    assert response.status_code == 200
    assert response.json() == expected


@pytest.mark.parametrize("path", ["/concerts/9999", "/concerts/9999/ticket-types"])
def test_unknown_concert_returns_not_found(client, path):
    """GET-03: concert không tồn tại được dịch từ NotFound sang HTTP 404."""
    response = client.get(path)
    assert response.status_code == 404
    assert response.json() == {"error": "NotFound", "detail": "concert 9999"}


def test_public_ticket_types(client):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with database.SessionLocal() as db:
        db.add(Concert(
            id=2, name="Other Concert", artist="B", venue="V2",
            start_time=now + timedelta(days=20), sale_open_time=now - timedelta(hours=1),
        ))
        db.flush()
        db.add_all([
            TicketType(id=2, concert_id=1, name="VIP", price=200, total_quantity=2, remaining=2),
            TicketType(id=3, concert_id=2, name="Other GA", price=100, total_quantity=10, remaining=10),
        ])
        db.commit()

    response = client.get("/concerts/1/ticket-types")
    assert response.status_code == 200
    ticket_types = response.json()
    assert isinstance(ticket_types, list)
    assert {ticket_type["id"] for ticket_type in ticket_types} == {1, 2}
    for ticket_type in ticket_types:
        assert {
            "id", "concert_id", "name", "price", "total_quantity", "remaining",
        } <= ticket_type.keys()
        assert ticket_type["concert_id"] == 1
    assert {ticket_type["name"]: ticket_type["remaining"] for ticket_type in ticket_types} == {
        "GA": 3, "VIP": 2,
    }


def test_orders_require_auth(client):
    assert client.get("/orders/me").status_code == 401
    assert client.post("/orders", json={"ticket_type_id": 1, "quantity": 1}).status_code == 401
    assert client.delete("/orders/1").status_code == 401


def test_order_lifecycle(client):
    h = auth_header(client)

    r = client.post("/orders", json={"ticket_type_id": 1, "quantity": 2}, headers=h)
    assert r.status_code == 201
    order = r.json()
    assert {"id", "user_id", "ticket_type_id", "quantity", "status", "created_at"} <= order.keys()
    token = h["Authorization"].removeprefix("Bearer ")
    user_id = int(jwt.decode(token, JWT_SECRET, algorithms=["HS256"])["sub"])
    assert order["user_id"] == user_id
    assert order["ticket_type_id"] == 1
    assert order["quantity"] == 2
    assert order["status"] == "confirmed"
    order_id = order["id"]
    assert client.get("/concerts/1/ticket-types").json()[0]["remaining"] == 1

    r = client.post("/orders", json={"ticket_type_id": 1, "quantity": 2}, headers=h)
    assert r.status_code == 409 and r.json()["error"] == "SoldOut"
    assert client.get("/concerts/1/ticket-types").json()[0]["remaining"] == 1

    r = client.get("/orders/me", headers=h)
    assert r.status_code == 200
    assert r.json() == [order]

    r = client.delete(f"/orders/{order_id}", headers=h)
    assert r.status_code == 200
    cancelled_order = {**order, "status": "cancelled"}
    assert r.json() == cancelled_order
    assert client.get("/concerts/1/ticket-types").json()[0]["remaining"] == 3

    r = client.delete(f"/orders/{order_id}", headers=h)
    assert r.status_code == 409
    assert r.json() == {"error": "InvalidState", "detail": "order already cancelled"}
    assert client.get("/concerts/1/ticket-types").json()[0]["remaining"] == 3
    assert client.get("/orders/me", headers=h).json() == [cancelled_order]


def test_create_order_before_sale_returns_bad_request(client):
    headers = auth_header(client)
    sale_open_time = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1)
    with database.SessionLocal() as db:
        db.get(Concert, 1).sale_open_time = sale_open_time
        db.commit()

    response = client.post(
        "/orders", json={"ticket_type_id": 1, "quantity": 1}, headers=headers,
    )
    assert response.status_code == 400
    assert response.json() == {"error": "SaleNotOpen", "detail": sale_open_time.isoformat()}
    assert client.get("/concerts/1/ticket-types").json()[0]["remaining"] == 3
    assert client.get("/orders/me", headers=headers).json() == []


def test_create_order_unknown_ticket_type_returns_not_found(client):
    headers = auth_header(client)
    response = client.post(
        "/orders", json={"ticket_type_id": 9999, "quantity": 1}, headers=headers,
    )
    assert response.status_code == 404
    assert response.json() == {"error": "NotFound", "detail": "ticket_type 9999"}
    assert client.get("/concerts/1/ticket-types").json()[0]["remaining"] == 3
    assert client.get("/orders/me", headers=headers).json() == []


@pytest.mark.parametrize("quantity", [1, 10])
def test_create_order_valid_quantity_boundaries(client, quantity):
    headers = auth_header(client)
    with database.SessionLocal() as db:
        ticket_type = db.get(TicketType, 1)
        ticket_type.total_quantity = 20
        ticket_type.remaining = 20
        db.commit()

    response = client.post(
        "/orders", json={"ticket_type_id": 1, "quantity": quantity}, headers=headers,
    )
    assert response.status_code == 201
    order = response.json()
    assert order["quantity"] == quantity
    assert order["status"] == "confirmed"
    assert client.get("/concerts/1/ticket-types").json()[0]["remaining"] == 20 - quantity
    assert client.get("/orders/me", headers=headers).json() == [order]


@pytest.mark.parametrize("quantity", [0, -1, 11])
def test_create_order_invalid_quantity_returns_validation_error(client, quantity):
    headers = auth_header(client)
    response = client.post(
        "/orders", json={"ticket_type_id": 1, "quantity": quantity}, headers=headers,
    )
    assert response.status_code == 422
    assert any(error["loc"] == ["body", "quantity"] for error in response.json()["detail"])
    assert client.get("/concerts/1/ticket-types").json()[0]["remaining"] == 3
    assert client.get("/orders/me", headers=headers).json() == []


def test_list_orders_returns_only_current_users_orders(client):
    headers = [auth_header(client, "owner@example.com"), auth_header(client, "other@example.com")]
    orders = []
    for header in headers:
        response = client.post(
            "/orders", json={"ticket_type_id": 1, "quantity": 1}, headers=header,
        )
        assert response.status_code == 201
        order = response.json()
        token = header["Authorization"].removeprefix("Bearer ")
        user_id = int(jwt.decode(token, JWT_SECRET, algorithms=["HS256"])["sub"])
        assert order["user_id"] == user_id
        orders.append(order)

    for header, order in zip(headers, orders):
        response = client.get("/orders/me", headers=header)
        assert response.status_code == 200
        assert response.json() == [order]


def test_cannot_cancel_other_users_order(client):
    h1 = auth_header(client, "b@example.com")
    h2 = auth_header(client, "c@example.com")
    created = client.post("/orders", json={"ticket_type_id": 1, "quantity": 1}, headers=h1)
    assert created.status_code == 201
    order = created.json()

    response = client.delete(f"/orders/{order['id']}", headers=h2)
    assert response.status_code == 403
    assert response.json() == {"error": "Forbidden", "detail": "not your order"}
    assert client.get("/orders/me", headers=h1).json() == [order]
    assert client.get("/orders/me", headers=h2).json() == []
    assert client.get("/concerts/1/ticket-types").json()[0]["remaining"] == 2


def test_cancel_unknown_order_returns_not_found(client):
    headers = auth_header(client)
    created = client.post("/orders", json={"ticket_type_id": 1, "quantity": 1}, headers=headers)
    assert created.status_code == 201
    order = created.json()

    response = client.delete("/orders/9999", headers=headers)
    assert response.status_code == 404
    assert response.json() == {"error": "NotFound", "detail": "order 9999"}
    assert client.get("/orders/me", headers=headers).json() == [order]
    assert client.get("/concerts/1/ticket-types").json()[0]["remaining"] == 2
