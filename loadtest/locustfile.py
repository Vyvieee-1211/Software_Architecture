"""S0–S10 theo mục 2 notebook, dùng API giữ chỗ bất đồng bộ hiện tại.

Chạy: python -m loadtest.run S0 --start-api --prepare
Chọn bằng SCENARIO=S0...S10 hoặc tên workload; mặc định smoke.
Xem loadtest/README.md để cấu hình PostgreSQL, mock payment và tải phân tán.

Waiting room / rate limit / load shedding (đợt mở bán):
    Trước khi đặt mua, mỗi user vào hàng chờ của CONCERT_ID và chờ tới lượt (đo bằng mẫu
    WAITING "waiting room end-to-end"), rồi gửi thẻ vào cửa trong header X-Waiting-Room-Pass.
    Ngoài đợt mở bán server trả `inactive` và user đặt mua ngay. 429 (RateLimited) và 503
    (ServerBusy/DatabaseUnavailable) là lỗi hạ tầng, được đếm riêng trong bộ đếm `http_<mã>`.
    Đo hiệu năng thuần (không muốn bị giới hạn) thì chạy API với RATE_LIMIT_*_PER_MINUTE=0
    và MAX_IN_FLIGHT_REQUESTS=0: Locust đăng ký mọi user từ cùng một IP.

Biến môi trường thêm:
    WAITING_ROOM_TIMEOUT_SECONDS  chờ tới lượt tối đa (mặc định 900)
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import random
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse
from pathlib import Path
from itertools import count as sequence

import gevent
from gevent.event import Event
from gevent.lock import Semaphore
from locust import HttpUser, between, constant, events, task
from locust.exception import StopUser
from loadtest import runtime

log = logging.getLogger("loadtest")

SCENARIO = os.getenv("SCENARIO", "smoke").lower()
SCENARIO = {"s0": "smoke", "s1": "browse", "s2": "flash", "s3": "funnel",
            "s4": "abandon", "s5": "retry", "s6": "contention", "s7": "replay",
            "s8": "soak", "s9": "scalability", "s10": "fault"}.get(SCENARIO, SCENARIO)
CONCERT_ID = int(os.getenv("CONCERT_ID", "1"))
TICKET_TYPE_ID = os.getenv("TICKET_TYPE_ID")
WAITING_ROOM_TIMEOUT = float(os.getenv("WAITING_ROOM_TIMEOUT_SECONDS", "900"))
PASSWORD = "secret123"
# Chuẩn bị tài khoản ngoài pha đo: không dồn bcrypt theo tốc độ mở bán.
AUTH_SETUP_SLOTS = Semaphore(int(os.getenv("SETUP_AUTH_CONCURRENCY", "2")))
AUTH_SEQUENCE = sequence()
AUTH_ACCOUNTS = json.loads(Path(os.environ["AUTH_ACCOUNTS_FILE"]).read_text(encoding="utf-8")) if os.getenv("AUTH_ACCOUNTS_FILE") else []
BUSINESS_ERRORS = {400: {"SaleClosed", "SaleNotOpen"},
                   409: {"SoldOut", "HoldLimitExceeded", "InvalidState", "HoldExpired"}}

COUNTERS = Counter()      # held / sold_out / paid / verify_ok / verify_fail ...
SALE_OPEN = Event()        # rào chắn cho kịch bản flash
runtime.install(events, COUNTERS, SALE_OPEN)


def business_reject(response):
    try:
        return response.json().get("error") in BUSINESS_ERRORS.get(response.status_code, set())
    except ValueError:
        return False


def verify(ok, label):
    count("verify_ok" if ok else "verify_fail")
    events.request.fire(request_type="VERIFY", name=label, response_time=0,
                        response_length=0, exception=None if ok else AssertionError(label), context={})


def park():
    # Giữ user sống để Locust không tự sinh user thay thế cho tác vụ chạy một lần.
    gevent.sleep(10**8)


def count(key, n=1):
    COUNTERS[key] += n


class Result:
    """Kết quả cuối của một lần giữ chỗ bất đồng bộ, cùng dạng với response (status_code, json())."""

    def __init__(self, status_code, body):
        self.status_code, self._body = status_code, body

    def json(self):
        return self._body


# --- Phần dùng chung -----------------------------------------------------------

class BaseUser(HttpUser):
    """Đăng ký + đăng nhập một lần (ngoài phần đo chính: tên request có hậu tố [setup])."""
    abstract = True
    wait_time = between(0.5, 2)

    def on_start(self):
        self.client.request = runtime.timed_request(self.client.request)
        email = f"load-{uuid.uuid4().hex[:12]}@example.com"
        if AUTH_ACCOUNTS:
            worker = self.environment.runner.worker_index if hasattr(self.environment.runner, "client_id") else 0
            position = next(AUTH_SEQUENCE) * int(os.getenv("LOADTEST_WORKERS", "1")) + worker
            if position >= len(AUTH_ACCOUNTS):
                count("setup_failed")
                raise StopUser()
            email = AUTH_ACCOUNTS[position]
        with AUTH_SETUP_SLOTS:
            if not AUTH_ACCOUNTS:
                self.client.post("/auth/register", json={"email": email, "password": PASSWORD},
                                 name="/auth/register [setup]")
            r = self.client.post("/auth/login", json={"email": email, "password": PASSWORD},
                                 name="/auth/login [setup]")
        if r.status_code != 200:
            # Đăng ký/đăng nhập lỗi (vd. 503 khi pool DB cạn lúc ramp-up): user này dừng,
            # được đếm vào `setup_failed` và hiện ở cột failures của request [setup].
            count("setup_failed")
            self.headers = None
            self.stop()
            return
        self.headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
        self.held: list[int] = []
        self.room_pass: str | None = None      # thẻ vào cửa waiting room
        self.room_valid_until = 0.0             # epoch: dùng lại pass / kết quả "inactive" tới lúc này
        self.ticket_types = self._ticket_types()
        if not self.ticket_types:
            count("setup_failed")
            raise StopUser()
        self.user_index = runtime.ready(self.environment)
        SALE_OPEN.wait()
        if hasattr(self.environment.runner, "client_id"):
            workers = int(os.getenv("LOADTEST_WORKERS", "1"))
            self.user_index = (self.user_index - 1) * workers + self.environment.runner.worker_index + 1

    def _ticket_types(self) -> list[int]:
        r = self.client.get(f"/concerts/{CONCERT_ID}/ticket-types", name="/concerts/{id}/ticket-types [setup]")
        ids = [t["id"] for t in r.json()] if r.status_code == 200 else []
        if TICKET_TYPE_ID:
            return [int(TICKET_TYPE_ID)]
        return ids

    def target_type(self) -> int:
        if TICKET_TYPE_ID:
            return int(TICKET_TYPE_ID)
        return self.ticket_types[0] if SCENARIO == "flash" else random.choice(self.ticket_types)

    # --- các bước nghiệp vụ, trả về dữ liệu hoặc None ---

    def waiting_room(self):
        """Lấy thẻ vào cửa cho CONCERT_ID. Trả về (True, pass hoặc None khi không cần), hoặc
        (False, None) khi lỗi hạ tầng / chờ quá lâu."""
        if time.time() < self.room_valid_until:
            return True, self.room_pass
        started = time.monotonic()
        while time.monotonic() - started < WAITING_ROOM_TIMEOUT:
            with self.client.post(f"/concerts/{CONCERT_ID}/waiting-room", headers=self.headers,
                                  name="/concerts/{id}/waiting-room [POST]", catch_response=True) as r:
                if r.status_code != 200:
                    count(f"http_{r.status_code}")
                    r.failure(f"unexpected {r.status_code}: {r.text[:120]}")
                    return False, None
                body = r.json()
                retry_after = float(r.headers.get("Retry-After", 2))
            if body["status"] == "inactive":
                # Ngoài đợt mở bán: không cần pass; hỏi lại sau 30 giây phòng khi đợt bắt đầu.
                self.room_pass, self.room_valid_until = None, time.time() + 30
                return True, None
            if body["status"] == "admitted":
                waited = (time.monotonic() - started) * 1000
                events.request.fire(request_type="WAITING", name="waiting room end-to-end",
                                    response_time=waited, response_length=0, exception=None, context={})
                count("room_admitted")
                expires = datetime.fromisoformat(body["pass_expires_at"]).replace(tzinfo=timezone.utc)
                self.room_pass = body["pass_token"]
                self.room_valid_until = expires.timestamp() - 5   # chừa biên đồng hồ
                return True, self.room_pass
            gevent.sleep(retry_after)      # waiting / not_open / expired (POST tự xếp lại)
        count("room_timeout")
        return False, None

    def submit(self, ticket_type_id, quantity, key, name="/orders [POST]"):
        """Gửi yêu cầu giữ chỗ (202 + Location). Trả về response, hoặc None nếu lỗi hạ tầng."""
        ok, room_pass = self.waiting_room()
        if not ok:
            return None
        body = {"ticket_type_id": ticket_type_id, "quantity": quantity}
        headers = {**self.headers, "Idempotency-Key": key}
        if room_pass:
            headers["X-Waiting-Room-Pass"] = room_pass
        with self.client.post("/orders", json=body, headers=headers, name=name, catch_response=True) as r:
            if r.status_code == 202:
                count("replayed" if r.headers.get("Idempotent-Replayed") == "true" else "queued")
                return r
            if business_reject(r):
                count(r.json().get("error", str(r.status_code)))
                r.success()
                return r
            count(f"http_{r.status_code}")
            if r.status_code == 403 and "WaitingRoomRequired" in r.text:
                self.room_valid_until = 0          # pass hết hạn/không hợp lệ: lần sau xếp hàng lại
            r.failure(f"unexpected {r.status_code}: {r.text[:120]}")
            return None

    def wait_request(self, location, timeout=None, started=None):
        """Hỏi trạng thái theo Retry-After đến khi yêu cầu xong. Trả về JSON cuối, hoặc None."""
        started = started if started is not None else time.monotonic()
        timeout = timeout or float(os.getenv("QUEUE_TIMEOUT_SECONDS", "60"))
        delay = 0.5
        while time.monotonic() - started < timeout:
            gevent.sleep(delay)
            with self.client.get(location, headers=self.headers, name="/orders/requests/{id} [GET]",
                                 catch_response=True) as r:
                if r.status_code != 200:
                    r.failure(f"unexpected {r.status_code}: {r.text[:120]}")
                    return None
                body = r.json()
                if body["status"] != "pending":
                    error = body.get("error")
                    expected = body["status"] == "succeeded" or error in set().union(*BUSINESS_ERRORS.values())
                    if not expected:
                        r.failure(f"queue failed: {error}")
                    # Thời gian từ lúc gửi đến khi có kết quả: chỉ số chính của luồng bất đồng bộ.
                    events.request.fire(request_type="QUEUE", name="order request end-to-end",
                                        response_time=(time.monotonic() - started) * 1000,
                                        response_length=0, exception=None if expected else RuntimeError(str(error)), context={})
                    return body
                delay = float(r.headers.get("Retry-After", delay))
        count("request_timeout")
        events.request.fire(request_type="QUEUE", name="order request end-to-end",
                            response_time=(time.monotonic() - started) * 1000,
                            response_length=0, exception=TimeoutError("queue timeout"), context={})
        return None

    def hold(self, ticket_type_id=None, quantity=None, key=None, name="/orders [POST]"):
        """Giữ chỗ đến khi có kết quả. Trả về `Result` 201 + đơn khi thành công, 409 + lỗi khi
        yêu cầu thất bại, response gốc khi bị từ chối ngay, hoặc None khi lỗi hạ tầng."""
        started = time.monotonic()
        r = self.submit(ticket_type_id or self.target_type(), quantity or random.randint(1, 4),
                        key or uuid.uuid4().hex, name)
        if r is None or r.status_code != 202:
            return r
        final = self.wait_request(r.headers["Location"], started=started)
        if final is None:
            return None
        if final["status"] == "succeeded":
            count("held")
            self.held.append(final["order_id"])
            return Result(201, final["order"])
        count(final["error"])
        return Result(409, {"error": final["error"], "detail": final["detail"]})

    def start_payment(self, order_id):
        with self.client.post(f"/orders/{order_id}/pay", headers=self.headers,
                              name="/orders/{id}/pay [POST]", catch_response=True) as r:
            if r.status_code == 200:
                return parse_qs(urlparse(r.json()["payment_url"]).query)["token"][0]
            if business_reject(r):
                r.success()
            else:
                r.failure(f"unexpected {r.status_code}")
            return None

    def gateway_pay(self, token, result="success", name="/mock-gateway/pay [POST]"):
        with self.client.post("/mock-gateway/pay", json={"token": token, "result": result},
                              name=name, catch_response=True) as r:
            if r.status_code != 200:
                r.failure(f"unexpected {r.status_code}")
                return None
            outcome = r.json().get("result")
            count(f"webhook_{outcome}")
            return outcome

    def pay_full(self, order_id, result="success"):
        """Thanh toán trọn gói: tạo giao dịch rồi mô phỏng cổng trả kết quả."""
        token = self.start_payment(order_id)
        if token is None:
            return None
        return self.gateway_pay(token, result)

    def cancel(self, order_id, name="/orders/{id} [DELETE]"):
        with self.client.delete(f"/orders/{order_id}", headers=self.headers, name=name, catch_response=True) as r:
            if r.status_code == 200:
                count(f"cancel_{r.json().get('status')}")
            elif business_reject(r):
                r.success()
            else:
                r.failure(f"unexpected {r.status_code}")
            return r

    def check_tickets(self, order_id, quantity):
        with self.client.get(f"/orders/{order_id}/tickets", headers=self.headers,
                             name="/orders/{id}/tickets [GET]", catch_response=True) as r:
            if SCENARIO == "fault" and r.status_code == 503:
                count("tickets_temporarily_unavailable")
                r.failure("temporary database outage")
                return
            ok = r.status_code == 200 and len(r.json()) == quantity and all(t.get("qr_code") and t.get("status") == "sold" for t in r.json())
            if not ok:
                r.failure("missing tickets or QR")
            verify(ok, "issued tickets and QR")


class SmokeUser(BaseUser):
    """S0 luôn đi hết luồng giữ → trả tiền → lấy vé, không chọn ngẫu nhiên bỏ đơn."""
    abstract = True

    @task
    def full_flow(self):
        self.client.get("/concerts")
        r = self.hold(quantity=1)
        if r is None or r.status_code != 201:
            verify(False, "smoke hold")
            return
        order_id = self.held.pop()
        ok = self.pay_full(order_id) == "paid"
        verify(ok, "smoke payment")
        if ok:
            self.check_tickets(order_id, 1)
            self.cancel(order_id)


# --- S0 Smoke / S3 Funnel (S8 soak dùng cùng lớp) -------------------------------

class FunnelUser(BaseUser):
    """Xem → giữ 1–4 vé → thanh toán (90% thành công) → xem vé. 20% đơn bị bỏ cho sweeper."""
    abstract = True

    @task(3)
    def browse(self):
        self.client.get("/concerts")
        self.client.get(f"/concerts/{CONCERT_ID}", name="/concerts/{id}")

    @task(2)
    def view_ticket_types(self):
        self.client.get(f"/concerts/{CONCERT_ID}/ticket-types", name="/concerts/{id}/ticket-types")

    @task(5)
    def hold_and_pay(self):
        self.client.get(f"/concerts/{CONCERT_ID}/ticket-types", name="/concerts/{id}/ticket-types")
        r = self.hold()
        if r is None or r.status_code != 201:
            return
        order_id = self.held.pop()
        if random.random() < 0.2:
            count("abandoned")  # bỏ đơn: sweeper sẽ trả kho sau HOLD_MINUTES
            return
        outcome = self.pay_full(order_id, "success" if random.random() < 0.9 else "failed")
        if outcome == "paid":
            count("paid")
            self.check_tickets(order_id, r.json()["quantity"])

    @task(1)
    def my_orders(self):
        self.client.get("/orders/me", headers=self.headers)


# --- S1 Browse --------------------------------------------------------------------

class BrowseUser(HttpUser):
    """Bắt đầu đọc ngay khi user được tạo để lưu lượng tăng dần trong pha ramp-up."""
    abstract = True
    wait_time = between(0.5, 2)

    def on_start(self):
        self.client.request = runtime.timed_request(self.client.request)
        runtime.ready(self.environment)

    @task(4)
    def concerts(self):
        self.client.get("/concerts")

    @task(3)
    def ticket_types(self):
        self.client.get(f"/concerts/{CONCERT_ID}/ticket-types", name="/concerts/{id}/ticket-types")


# --- S2 Flash sale ----------------------------------------------------------------

class FlashSaleUser(BaseUser):
    """Chờ master mở rào khi mọi user sẵn sàng; cùng giữ một hạng vé và không thanh toán."""
    abstract = True
    wait_time = constant(0)

    @task
    def rush(self):
        SALE_OPEN.wait()
        r = self.hold(name="/orders [POST flash]")
        if r is not None and r.status_code == 201:
            order_id = self.held.pop()
            count("flash_quantity", r.json()["quantity"])
        park()


@events.spawning_complete.add_listener
def _open_sale(user_count, **kwargs):
    # runtime chỉ mở rào khi mọi user đã hoàn tất đăng nhập, kể cả trên nhiều worker.
    pass


# --- S4 Abandon -------------------------------------------------------------------

class AbandonUser(BaseUser):
    """Giữ chỗ một lần rồi không làm gì: đo thời gian kho hồi về (sweeper)."""
    abstract = True
    wait_time = constant(5)

    @task
    def hold_once(self):
        r = self.hold(name="/orders [POST abandon]")
        if r is not None and r.status_code == 201:
            order = r.json()
            deadline = datetime.fromisoformat(order["expires_at"]).replace(tzinfo=timezone.utc).timestamp()
            while time.time() < deadline + 90:
                gevent.sleep(1)
                response = self.client.get(f"/orders/{order['id']}", headers=self.headers,
                                           name="/orders/{id} [expiry]")
                if response.status_code == 200 and response.json()["status"] == "expired":
                    lag = max(0, time.time() - deadline)
                    events.request.fire(request_type="SWEEPER", name="expiry lag", response_time=lag * 1000,
                                        response_length=0, exception=None, context={})
                    verify(lag < 10, "sweeper lag < 10s")
                    count("expired")
                    break
            else:
                verify(False, "hold expires within deadline + 90s")
        park()


# --- S5 Retry storm ---------------------------------------------------------------

class RetryStormUser(BaseUser):
    """3 request giữ chỗ song song cùng Idempotency-Key: mong đợi cả 3 nhận 202 với CÙNG một
    yêu cầu (đúng 1 lần mới, 2 lần Idempotent-Replayed), và cuối cùng có tối đa một đơn."""
    abstract = True

    @task
    def retry_burst(self):
        key = uuid.uuid4().hex
        ticket_type, quantity = self.target_type(), random.randint(1, 4)
        jobs = [gevent.spawn(self.submit, ticket_type, quantity, key, "/orders [POST retry x3]") for _ in range(3)]
        gevent.joinall(jobs)
        results = [j.value for j in jobs if j.value is not None]
        accepted = [r for r in results if r.status_code == 202]
        if not accepted:
            return  # hết vé: cả 3 cùng bị từ chối ngay là đúng
        fresh = [r for r in accepted if r.headers.get("Idempotent-Replayed") != "true"]
        ids = {r.json()["id"] for r in accepted}
        ok = len(fresh) == 1 and len(ids) == 1 and len(accepted) == 3
        verify(ok, "retry x3 same request, one fresh")
        if not ok:
            log.error("retry x3: %s", [(r.status_code, r.text[:80]) for r in results])
        final = self.wait_request(accepted[0].headers["Location"])
        if final is not None and final["status"] == "succeeded":
            self.cancel(final["order_id"])  # trả kho để lượt sau còn vé


# --- S6 Contention ----------------------------------------------------------------

class ContentionUser(BaseUser):
    """Trên cùng một đơn: 10 DELETE song song (đơn held → cancelled, đơn paid → refunded).
    Mong đợi đúng 1 phản hồi 200, 9 phản hồi 409 InvalidState."""
    abstract = True

    def _parallel_cancel(self, order_id, label):
        jobs = [gevent.spawn(self.cancel, order_id, f"/orders/{{id}} [DELETE x10 {label}]") for _ in range(10)]
        gevent.joinall(jobs)
        codes = Counter(j.value.status_code for j in jobs if j.value is not None)
        if codes.get(200) == 1 and codes.get(409) == 9:
            verify(True, f"cancel x10 {label}")
        else:
            verify(False, f"cancel x10 {label}")
            log.error("cancel x10 (%s) order %s: %s", label, order_id, dict(codes))

    def cancel_held(self):
        r = self.hold(quantity=1)
        if r is not None and r.status_code == 201:
            self._parallel_cancel(self.held.pop(), "held")
            count("cancel_held_cases")

    def refund_paid(self):
        r = self.hold(quantity=1)
        if r is None or r.status_code != 201:
            return
        order_id = self.held.pop()
        if self.pay_full(order_id) == "paid":
            self._parallel_cancel(order_id, "paid")
            count("refund_paid_cases")

    def pay_vs_expire(self):
        """S6 gửi webhook sát expires_at, để sweeper thực sự tranh chấp với thanh toán."""
        r = self.hold(quantity=1)
        if r is None or r.status_code != 201:
            return
        order_id = self.held.pop()
        token = self.start_payment(order_id)
        if token is None:
            return
        expiry = datetime.fromisoformat(r.json()["expires_at"]).replace(tzinfo=timezone.utc).timestamp()
        gevent.sleep(max(0, expiry - time.time() + random.uniform(-0.1, 0.1)))
        outcome = self.gateway_pay(token, "success", "/mock-gateway/pay [POST pay-vs-expire]")
        gevent.sleep(float(os.getenv("SWEEP_WAIT_SECONDS", "6")))
        response = self.client.get(f"/orders/{order_id}", headers=self.headers, name="/orders/{id} [race result]")
        status = response.json().get("status") if response.status_code == 200 else None
        verify((outcome, status) in {("paid", "paid"), ("refund_pending", "expired")}, "pay vs expire")
        count("expiry_race_cases")
        if status == "paid":
            self.check_tickets(order_id, 1)
            self.cancel(order_id)

    @task
    def contention_once(self):
        self.cancel_held()
        self.refund_paid()
        if self.user_index <= int(os.getenv("EXPIRY_RACE_USERS", "100")):
            self.pay_vs_expire()
        park()


# --- S7 Webhook replay ------------------------------------------------------------

class ReplayUser(BaseUser):
    """Cùng một giao dịch, cổng gửi webhook 5 lần song song: 1 `paid` + 4 `duplicate`."""
    abstract = True

    @task
    def replay(self):
        r = self.hold(quantity=1)
        if r is None or r.status_code != 201:
            return
        order_id = self.held.pop()
        token = self.start_payment(order_id)
        if token is None:
            return
        payload = json.loads(base64.urlsafe_b64decode(token.split(".")[0]))
        body = json.dumps({"payment_id": payload["payment_id"], "amount": payload["amount"],
                           "status": "succeeded"}).encode()
        secret = os.getenv("PAYMENT_WEBHOOK_SECRET", "dev-payment-secret-change-me")
        headers = {"Content-Type": "application/json",
                   "x-mock-signature": hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()}

        def webhook():
            with self.client.post("/payments/webhook", data=body, headers=headers,
                                  name="/payments/webhook [POST x5]", catch_response=True) as response:
                if response.status_code != 200:
                    response.failure(f"unexpected {response.status_code}")
                    return None
                return response.json().get("result")

        jobs = [gevent.spawn(webhook) for _ in range(5)]
        gevent.joinall(jobs)
        outcomes = Counter(j.value for j in jobs)
        count("replay_cases")
        if outcomes.get("paid") == 1 and outcomes.get("duplicate") == 4:
            verify(True, "webhook x5 one paid four duplicate")
            self.check_tickets(order_id, 1)
        else:
            verify(False, "webhook x5 one paid four duplicate")
            log.error("webhook x5 order %s: %s", order_id, dict(outcomes))
        park()


# --- Chọn kịch bản ------------------------------------------------------------------

SCENARIOS = {
    "smoke": SmokeUser, "funnel": FunnelUser, "browse": BrowseUser, "flash": FlashSaleUser,
    "abandon": AbandonUser, "retry": RetryStormUser, "contention": ContentionUser, "replay": ReplayUser,
    "soak": FunnelUser, "scalability": FunnelUser, "fault": FunnelUser,
}
if SCENARIO not in SCENARIOS:
    raise SystemExit(f"SCENARIO={SCENARIO!r} không hợp lệ; chọn một trong {sorted(SCENARIOS)}")
SCENARIOS[SCENARIO].abstract = False


# --- Báo cáo cuối lượt ---------------------------------------------------------------

@events.test_start.add_listener
def _on_start(environment, **kwargs):
    log.info("Kịch bản: %s | concert %s | hạng vé %s", SCENARIO, CONCERT_ID, TICKET_TYPE_ID or "ngẫu nhiên")


@events.test_stop.add_listener
def _on_stop(environment, **kwargs):
    log.info("Bộ đếm nghiệp vụ: %s", dict(sorted(COUNTERS.items())))
    if COUNTERS.get("verify_fail"):
        log.error("CÓ %s kiểm tra 'đúng một bên thắng' THẤT BẠI", COUNTERS["verify_fail"])
    # Báo cáo và kiểm tra DB thực hiện ở runner sau khi mọi worker đã dừng.
