"""Test tích hợp toàn bộ API trên SQLite in-memory (không đụng file concert.db).

Chạy: pytest
"""
import os
from datetime import datetime, timedelta

os.environ["DATABASE_URL"] = "sqlite://"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

from app.main import app
from app.repositories import database
from app.repositories.models import Base, Concert, TicketType


@pytest.fixture(scope="module")
def client():
    from sqlalchemy import create_engine

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    database.engine = engine
    database.SessionLocal.configure(bind=engine)
    Base.metadata.create_all(engine)

    now = datetime.utcnow()
    with database.SessionLocal() as db:
        c = Concert(name="Test Concert", artist="A", venue="V",
                    start_time=now + timedelta(days=10), sale_open_time=now - timedelta(hours=1))
        db.add(c)
        db.flush()
        db.add(TicketType(concert_id=c.id, name="GA", price=100, total_quantity=3, remaining=3))
        db.commit()

    with TestClient(app) as c:
        yield c


def auth_header(client, email="a@example.com"):
    client.post("/auth/register", json={"email": email, "password": "secret123", "full_name": "A"})
    token = client.post("/auth/login", json={"email": email, "password": "secret123"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_register_and_login(client):
    r = client.post("/auth/register", json={"email": "u1@example.com", "password": "secret123"})
    assert r.status_code == 201
    assert "password" not in r.json()

    r = client.post("/auth/register", json={"email": "u1@example.com", "password": "secret123"})
    assert r.status_code == 409

    r = client.post("/auth/login", json={"email": "u1@example.com", "password": "wrong"})
    assert r.status_code == 401


def test_public_endpoints(client):
    r = client.get("/concerts")
    assert r.status_code == 200 and len(r.json()) == 1
    concert_id = r.json()[0]["id"]
    r = client.get(f"/concerts/{concert_id}/ticket-types")
    assert r.status_code == 200 and r.json()[0]["remaining"] == 3


def test_orders_require_auth(client):
    assert client.get("/orders/me").status_code == 401
    assert client.post("/orders", json={"ticket_type_id": 1, "quantity": 1}).status_code == 401
    assert client.delete("/orders/1").status_code == 401


def test_order_lifecycle(client):
    h = auth_header(client)

    r = client.post("/orders", json={"ticket_type_id": 1, "quantity": 2}, headers=h)
    assert r.status_code == 201
    order_id = r.json()["id"]
    assert client.get("/concerts/1/ticket-types").json()[0]["remaining"] == 1

    r = client.post("/orders", json={"ticket_type_id": 1, "quantity": 2}, headers=h)
    assert r.status_code == 409 and r.json()["error"] == "SoldOut"
    assert client.get("/concerts/1/ticket-types").json()[0]["remaining"] == 1

    r = client.get("/orders/me", headers=h)
    assert r.status_code == 200 and len(r.json()) == 1

    r = client.delete(f"/orders/{order_id}", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert client.get("/concerts/1/ticket-types").json()[0]["remaining"] == 3

    r = client.delete(f"/orders/{order_id}", headers=h)
    assert r.status_code == 409


def test_cannot_cancel_other_users_order(client):
    h1 = auth_header(client, "b@example.com")
    h2 = auth_header(client, "c@example.com")
    order_id = client.post("/orders", json={"ticket_type_id": 1, "quantity": 1}, headers=h1).json()["id"]
    assert client.delete(f"/orders/{order_id}", headers=h2).status_code == 403
