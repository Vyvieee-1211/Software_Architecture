"""Đợt mở bán: Virtual Waiting Room → Rate limit → Load shedding."""
import asyncio
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

import pytest

from app import config
from app.api import deps
from app.api.load_shedding import LoadShedder
from app.api.rate_limit import RateLimiter, RateLimitExceeded
from app.repositories import database
from app.repositories.models import Concert
from app.repositories.unit_of_work import SqlAlchemyUnitOfWork
from app.services.errors import WaitingRoomRequired
from app.services.waiting_room_service import WaitingRoomService
from tests.test_api import auth_header, client, drain_order_queue  # noqa: F401  (fixture)


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def open_sale(minutes_ago: float) -> None:
    """Dời giờ mở bán của concert 1 để đang (hoặc chưa) trong đợt waiting room."""
    with database.SessionLocal() as db:
        db.get(Concert, 1).sale_open_time = now() - timedelta(minutes=minutes_ago)
        db.commit()


@pytest.fixture
def room(monkeypatch):
    """Waiting room 30 phút, cho vào 1 người lúc mở bán và không thêm ai: dễ kiểm tra thứ tự."""
    monkeypatch.setattr(config, "WAITING_ROOM_MINUTES", 30)
    monkeypatch.setattr(config, "WAITING_ROOM_LEAD_MINUTES", 15)
    monkeypatch.setattr(config, "WAITING_ROOM_INITIAL_BATCH", 1)
    monkeypatch.setattr(config, "WAITING_ROOM_ADMIT_PER_MINUTE", 0)
    monkeypatch.setattr(config, "WAITING_ROOM_PASS_MINUTES", 10)


def order(client, headers, pass_token=None, key=None):
    extra = {"X-Waiting-Room-Pass": pass_token} if pass_token else {}
    if key:
        extra["Idempotency-Key"] = key
    return client.post("/orders", json={"ticket_type_id": 1, "quantity": 1}, headers={**headers, **extra})


# --- Waiting room qua API ----------------------------------------------------

def test_outside_sale_window_no_waiting_room_needed(client, room):
    open_sale(minutes_ago=60)
    h = auth_header(client)
    assert client.post("/concerts/1/waiting-room", headers=h).json()["status"] == "inactive"
    assert order(client, h).status_code == 202


def test_first_in_line_gets_pass_others_wait(client, room):
    open_sale(minutes_ago=1)
    first, second = auth_header(client, "a@example.com"), auth_header(client, "b@example.com")

    admitted = client.post("/concerts/1/waiting-room", headers=first).json()
    assert admitted["status"] == "admitted" and admitted["pass_token"]
    waiting = client.post("/concerts/1/waiting-room", headers=second)
    assert waiting.json()["status"] == "waiting" and waiting.json()["position"] == 1
    assert int(waiting.headers["Retry-After"]) >= 2

    rejected = order(client, second)
    assert rejected.status_code == 403 and rejected.json()["error"] == "WaitingRoomRequired"
    # Pass gắn với người được cấp: người khác dùng lại cũng bị từ chối.
    assert order(client, second, admitted["pass_token"]).json()["error"] == "WaitingRoomRequired"
    assert order(client, first, admitted["pass_token"]).status_code == 202


def test_queue_is_first_come_first_served(client, room, monkeypatch):
    monkeypatch.setattr(config, "WAITING_ROOM_INITIAL_BATCH", 2)
    open_sale(minutes_ago=1)
    users = [auth_header(client, f"u{i}@example.com") for i in range(4)]
    statuses = [client.post("/concerts/1/waiting-room", headers=h).json() for h in users]
    assert [s["status"] for s in statuses] == ["admitted", "admitted", "waiting", "waiting"]
    assert [s["position"] for s in statuses[2:]] == [1, 2]
    # Vào hàng lại không làm mất chỗ, GET không tự vào hàng.
    assert client.post("/concerts/1/waiting-room", headers=users[3]).json()["position"] == 2
    assert client.get("/concerts/1/waiting-room", headers=auth_header(client, "late@example.com")).json()["status"] == "not_joined"


def test_queue_opens_before_sale_and_nobody_admitted_early(client, room):
    open_sale(minutes_ago=-30)       # mở bán sau 30 phút, chỉ được vào hàng trước 15 phút
    h = auth_header(client)
    early = client.post("/concerts/1/waiting-room", headers=h).json()
    assert early["status"] == "not_open" and early["opens_at"]

    open_sale(minutes_ago=-10)       # còn 10 phút: được vào hàng nhưng chưa ai được vào
    waiting = client.post("/concerts/1/waiting-room", headers=h).json()
    assert waiting["status"] == "waiting" and waiting["estimated_wait_seconds"] >= 590


def test_replay_with_same_idempotency_key_needs_no_new_pass(client, room):
    open_sale(minutes_ago=1)
    h = auth_header(client)
    token = client.post("/concerts/1/waiting-room", headers=h).json()["pass_token"]
    first = order(client, h, token, key="key-12345678")
    replay = order(client, h, key="key-12345678")
    assert replay.status_code == 202 and replay.json()["id"] == first.json()["id"]


def test_unknown_concert_is_404(client, room):
    assert client.post("/concerts/999/waiting-room", headers=auth_header(client)).status_code == 404


# --- Waiting room: hết hạn pass, xếp lại cuối hàng (đồng hồ giả) -------------

class Clock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


def test_expired_pass_goes_to_back_of_line(client):
    start = now() - timedelta(minutes=1)
    with database.SessionLocal() as db:
        db.get(Concert, 1).sale_open_time = start
        db.commit()
    concert = SimpleNamespace(id=1, sale_open_time=start)
    clock = Clock(start + timedelta(seconds=30))
    svc = WaitingRoomService(lambda: SqlAlchemyUnitOfWork(database.SessionLocal), "secret", clock=clock,
                             window_minutes=60, initial_batch=1, admit_per_minute=0, pass_minutes=10)
    a, b = (int(client.post("/auth/register", json={"email": e, "password": "secret123"}).json()["id"])
            for e in ("a@example.com", "b@example.com"))

    admitted = svc.join(a, 1)
    assert admitted.status == "admitted"
    assert svc.join(b, 1).status == "waiting"
    clock.value += timedelta(minutes=5)
    svc.verify_pass(admitted.pass_token, a, concert, clock())          # còn hạn
    assert svc.status(a, 1).pass_expires_at == admitted.pass_expires_at  # hạn không bị kéo dài

    clock.value += timedelta(minutes=6)
    with pytest.raises(WaitingRoomRequired):
        svc.verify_pass(admitted.pass_token, a, concert, clock())
    assert svc.status(a, 1).status == "expired"
    rejoined = svc.join(a, 1)                       # xếp lại: đứng sau b
    assert rejoined.status == "waiting" and rejoined.position == 1
    assert svc.status(b, 1).status == "admitted"


def test_admission_rate_opens_slots_over_time():
    svc = WaitingRoomService(None, "secret", initial_batch=100, admit_per_minute=100)
    concert = type("C", (), {"sale_open_time": datetime(2026, 1, 1), "id": 1})()
    assert svc.admitted_count(concert, datetime(2025, 12, 31, 23, 59)) == 0
    assert svc.admitted_count(concert, datetime(2026, 1, 1)) == 100
    assert svc.admitted_count(concert, datetime(2026, 1, 1, 0, 3, 30)) == 450


# --- Rate limit ------------------------------------------------------------------

def test_token_bucket_refills_over_time():
    t = [0.0]
    limiter = RateLimiter(per_minute=2, clock=lambda: t[0])
    limiter.hit("k")
    limiter.hit("k")
    with pytest.raises(RateLimitExceeded) as exc:
        limiter.hit("k")
    assert exc.value.retry_after == 30            # 2 token/phút: 30 giây có thêm một token
    limiter.hit("other")                          # khóa khác không bị ảnh hưởng
    t[0] = 30
    limiter.hit("k")


def test_orders_are_rate_limited_per_user(client, monkeypatch):
    monkeypatch.setitem(deps.rate_limiters, "orders", RateLimiter(per_minute=2))
    h, other = auth_header(client, "a@example.com"), auth_header(client, "b@example.com")
    assert [order(client, h).status_code for _ in range(2)] == [202, 202]
    limited = order(client, h)
    assert limited.status_code == 429 and limited.json()["error"] == "RateLimited"
    assert int(limited.headers["Retry-After"]) >= 1
    assert order(client, other).status_code == 202


def test_login_is_rate_limited_per_ip(client, monkeypatch):
    auth_header(client)
    monkeypatch.setitem(deps.rate_limiters, "auth", RateLimiter(per_minute=2))
    body = {"email": "a@example.com", "password": "secret123"}
    assert [client.post("/auth/login", json=body).status_code for _ in range(3)] == [200, 200, 429]


# --- Load shedding ----------------------------------------------------------------

def test_load_shedder_rejects_when_full_and_exempts_webhooks():
    release = asyncio.Event()

    async def slow_app(scope, receive, send):
        await release.wait()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    shedder = LoadShedder(slow_app, max_in_flight=1, retry_after=3)

    async def call(path):
        sent = []

        async def send(message):
            sent.append(message)

        await shedder(scope={"type": "http", "path": path}, receive=None, send=send)
        return sent

    async def scenario():
        busy = asyncio.create_task(call("/orders"))
        await asyncio.sleep(0)                      # request đầu đang được xử lý
        rejected = await call("/concerts")
        webhook = asyncio.create_task(call("/payments/webhook"))
        await asyncio.sleep(0)
        release.set()
        return rejected, await busy, await webhook

    rejected, accepted, webhook = asyncio.run(scenario())
    assert rejected[0]["status"] == 503 and (b"retry-after", b"3") in rejected[0]["headers"]
    assert b"ServerBusy" in rejected[1]["body"]
    assert accepted[0]["status"] == 200 and webhook[0]["status"] == 200
    assert shedder.in_flight == 0 and shedder.shed == 1
