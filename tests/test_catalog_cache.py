"""Cache-Aside + single-flight cho danh sách concert/hạng vé."""
import threading
from datetime import datetime, timedelta, timezone

import pytest

from app.api import deps
from app.services.errors import NotFound
from app.services.read_cache import ReadThroughCache
from tests.test_api import auth_header, client, place_order  # noqa: F401  (fixture)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_value_is_reused_until_ttl_expires():
    clock = FakeClock()
    cache = ReadThroughCache(2, clock=clock)
    calls = []

    def load():
        calls.append(1)
        return len(calls)

    assert cache.get_or_load("k", load) == 1
    clock.now = 1.9
    assert cache.get_or_load("k", load) == 1
    clock.now = 2.0
    assert cache.get_or_load("k", load) == 2
    assert len(calls) == 2


def test_ttl_zero_disables_cache():
    cache = ReadThroughCache(0)
    calls = []
    for _ in range(3):
        cache.get_or_load("k", lambda: calls.append(1))
    assert len(calls) == 3


def test_concurrent_misses_share_one_load():
    """Single-flight: 20 luồng cùng hỏi một khóa đang trống chỉ tạo MỘT lần đọc DB."""
    cache = ReadThroughCache(60)
    release = threading.Event()
    calls = []

    def slow_load():
        calls.append(1)
        release.wait(5)
        return "concerts"

    results = []
    threads = [threading.Thread(target=lambda: results.append(cache.get_or_load("k", slow_load)))
               for _ in range(20)]
    for thread in threads:
        thread.start()
    # Chờ tất cả các luồng đã đến cache (1 luồng dẫn + 19 luồng chờ) rồi mới cho DB trả lời.
    for _ in range(500):
        if cache.misses + cache.shared == 20:
            break
        threading.Event().wait(0.01)
    release.set()
    for thread in threads:
        thread.join(5)

    assert len(calls) == 1
    assert results == ["concerts"] * 20
    assert cache.misses == 1 and cache.shared == 19


def test_errors_reach_waiters_and_are_not_cached():
    cache = ReadThroughCache(60)
    entered, release = threading.Event(), threading.Event()

    def failing_load():
        entered.set()
        release.wait(5)
        raise NotFound("concert 9")

    errors = []

    def call():
        try:
            cache.get_or_load("k", failing_load)
        except NotFound as exc:
            errors.append(exc)

    leader = threading.Thread(target=call)
    leader.start()
    entered.wait(5)
    follower = threading.Thread(target=call)
    follower.start()
    for _ in range(500):
        if cache.shared == 1:
            break
        threading.Event().wait(0.01)
    release.set()
    leader.join(5)
    follower.join(5)

    assert len(errors) == 2
    # Lỗi không được lưu: lần sau đọc lại nguồn.
    assert cache.get_or_load("k", lambda: "ok") == "ok"


def test_invalidate_during_load_does_not_store_stale_value():
    cache = ReadThroughCache(60)
    entered, release = threading.Event(), threading.Event()

    def stale_load():
        entered.set()
        release.wait(5)
        return "old"

    thread = threading.Thread(target=lambda: cache.get_or_load("k", stale_load))
    thread.start()
    entered.wait(5)
    cache.invalidate()              # admin vừa commit thay đổi trong lúc lần đọc cũ chưa xong
    release.set()
    thread.join(5)

    assert cache.get_or_load("k", lambda: "new") == "new"


# --- Qua API -----------------------------------------------------------------

@pytest.fixture
def cached(monkeypatch):
    cache = ReadThroughCache(60)
    monkeypatch.setattr(deps, "catalog_cache", cache)
    return cache


def test_reads_are_served_from_cache(client, cached):
    first = client.get("/concerts/1/ticket-types").json()
    for _ in range(5):
        assert client.get("/concerts/1/ticket-types").json() == first
        client.get("/concerts")
        client.get("/concerts/1")
    assert cached.misses == 3          # ticket-types, danh sách, chi tiết: mỗi khóa đọc DB một lần
    assert cached.hits == 5 * 3 - 2


def test_unknown_concert_still_404_with_cache(client, cached):
    assert client.get("/concerts/999").status_code == 404
    assert client.get("/concerts/999/ticket-types").status_code == 404


def test_order_checks_stock_in_database_not_cache(client, cached):
    """Số vé hiển thị có thể cũ, nhưng đặt mua vẫn kiểm tra kho thật: không bán vượt."""
    assert client.get("/concerts/1/ticket-types").json()[0]["remaining"] == 3
    headers = auth_header(client)
    assert place_order(client, {"ticket_type_id": 1, "quantity": 3}, headers).status_code == 201
    # Cache còn hạn nên vẫn hiển thị 3, đúng với TTL đã chấp nhận...
    assert client.get("/concerts/1/ticket-types").json()[0]["remaining"] == 3
    # ...nhưng người khác đặt tiếp thì bị từ chối vì DB đã hết vé.
    other = auth_header(client, "b@example.com")
    assert place_order(client, {"ticket_type_id": 1, "quantity": 1}, other).json()["error"] == "SoldOut"


def test_admin_changes_invalidate_cache(client, cached, monkeypatch):
    monkeypatch.setattr(deps, "ADMIN_EMAILS", frozenset({"admin@example.com"}))
    admin = auth_header(client, "admin@example.com")
    concert = client.get("/concerts/1").json()
    assert len(client.get("/concerts/1/ticket-types").json()) == 1

    fields = {k: concert[k] for k in ("name", "artist", "venue", "start_time", "sale_open_time")}
    fields["name"] = "Tên mới"
    assert client.put("/concerts/1", json=fields, headers=admin).status_code == 200
    assert client.get("/concerts/1").json()["name"] == "Tên mới"
    assert client.get("/concerts").json()[0]["name"] == "Tên mới"

    added = client.post("/concerts/1/ticket-types", headers=admin,
                        json={"name": "VIP", "price": "500000", "total_quantity": 5})
    assert added.status_code == 201
    assert len(client.get("/concerts/1/ticket-types").json()) == 2


def test_failed_admin_change_keeps_cache(client, cached, monkeypatch):
    """Thay đổi bị từ chối (rollback, không commit) thì không cần xóa cache."""
    monkeypatch.setattr(deps, "ADMIN_EMAILS", frozenset({"admin@example.com"}))
    admin = auth_header(client, "admin@example.com")
    client.get("/concerts")
    before = cached.misses
    assert client.delete("/concerts/999", headers=admin).status_code == 404
    client.get("/concerts")
    assert cached.misses == before


def test_on_sale_filter_uses_current_time(client, cached):
    from app.repositories import database
    from app.repositories.models import Concert
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with database.SessionLocal() as db:
        db.add(Concert(name="Sắp mở", artist="B", venue="V",
                       start_time=now + timedelta(days=20), sale_open_time=now + timedelta(days=5)))
        db.commit()
    assert {c["name"] for c in client.get("/concerts").json()} == {"Test Concert", "Sắp mở"}
    assert [c["name"] for c in client.get("/concerts?on_sale=true").json()] == ["Test Concert"]
    assert cached.misses == 1          # cả hai lần dùng chung một bản danh sách trong cache
