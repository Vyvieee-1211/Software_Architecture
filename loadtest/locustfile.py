"""Kịch bản đo tải Locust cho luồng giữ chỗ → thanh toán (Phase 2), viết theo mô tả
trong `kaggle_baseline_fixed.ipynb` (mục 2: S0–S10) và chỉ dùng hợp đồng HTTP công khai.

Chọn kịch bản bằng biến môi trường SCENARIO (mặc định `funnel`):

    smoke      S0  luồng đầy đủ, ít user, kiểm tra API chạy được
    browse     S1  chỉ đọc: /concerts, /concerts/{id}, /concerts/{id}/ticket-types
    flash      S2  mở bán: mọi user chờ đủ quân số rồi cùng lúc giữ chỗ MỘT hạng vé
    funnel     S3  phễu mua: xem → giữ → thanh toán (90% thành công) → xem vé; 20% bỏ đơn
    abandon    S4  giữ chỗ rồi bỏ, để đo sweeper trả kho (đặt HOLD_MINUTES nhỏ ở API)
    retry      S5  mỗi lần giữ chỗ bắn 3 request cùng Idempotency-Key song song
    contention S6  10 lệnh hủy song song trên cùng đơn (held và paid), pay-vs-expire
    replay     S7  webhook gửi lặp 5 lần cho cùng giao dịch
    (S8 soak = funnel chạy 30 phút; S9 scalability = lặp flash/funnel với --workers/pool
     khác nhau; S10 chịu lỗi = funnel + `docker compose stop db` giữa chừng)

Biến môi trường khác:
    CONCERT_ID             concert dùng để đọc/giữ chỗ (mặc định 1)
    TICKET_TYPE_ID         hạng vé cho flash/contention/replay (mặc định: hạng đầu của concert)
    LOADTEST_DATABASE_URL  nếu đặt (postgresql://...), cuối lượt chạy kiểm tra bất biến kho
                           và ghi loadtest/inventory_check_<scenario>.csv

Chạy một tiến trình (tới ~1.000 user):
    locust -f loadtest/locustfile.py --host http://localhost:8000 --headless -u 200 -r 20 -t 2m --csv kaggle_runs/phase2_s3

Chạy phân tán cho 3.000 user (S1/S2): một master + 4 worker trong 4 terminal
    locust -f loadtest/locustfile.py --master --headless -u 3000 -r 200 -t 3m --expect-workers 4 --csv kaggle_runs/phase2_s2
    locust -f loadtest/locustfile.py --worker            (x4)

API phải chạy với PAYMENT_PROVIDER=mock. Lỗi nghiệp vụ (409 SoldOut/HoldLimitExceeded/
InvalidState, 400 SaleClosed) được tính là THÀNH CÔNG về hạ tầng; mọi mã khác là lỗi.
Các kiểm tra "đúng 1 thắng" ghi vào bộ đếm `verify_*` in ra lúc dừng.
"""
import csv
import logging
import os
import random
import time
import uuid
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import gevent
from gevent.event import Event
from locust import HttpUser, between, constant, events, task
from locust.exception import StopUser

log = logging.getLogger("loadtest")

SCENARIO = os.getenv("SCENARIO", "funnel").lower()
CONCERT_ID = int(os.getenv("CONCERT_ID", "1"))
TICKET_TYPE_ID = os.getenv("TICKET_TYPE_ID")
PASSWORD = "secret123"
BUSINESS_REJECTS = (400, 409)  # từ chối nghiệp vụ hợp lệ, không phải lỗi hạ tầng

COUNTERS = Counter()      # held / sold_out / paid / verify_ok / verify_fail ...
SALE_OPEN = Event()        # rào chắn cho kịch bản flash


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
        email = f"load-{uuid.uuid4().hex[:12]}@example.com"
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
        self.ticket_types = self._ticket_types()

    def _ticket_types(self) -> list[int]:
        r = self.client.get(f"/concerts/{CONCERT_ID}/ticket-types", name="/concerts/{id}/ticket-types [setup]")
        ids = [t["id"] for t in r.json()] if r.status_code == 200 else []
        if TICKET_TYPE_ID:
            return [int(TICKET_TYPE_ID)]
        return ids

    def target_type(self) -> int:
        return int(TICKET_TYPE_ID) if TICKET_TYPE_ID else random.choice(self.ticket_types)

    # --- các bước nghiệp vụ, trả về dữ liệu hoặc None ---

    def submit(self, ticket_type_id, quantity, key, name="/orders [POST]"):
        """Gửi yêu cầu giữ chỗ (202 + Location). Trả về response, hoặc None nếu lỗi hạ tầng."""
        body = {"ticket_type_id": ticket_type_id, "quantity": quantity}
        headers = {**self.headers, "Idempotency-Key": key}
        with self.client.post("/orders", json=body, headers=headers, name=name, catch_response=True) as r:
            if r.status_code == 202:
                count("replayed" if r.headers.get("Idempotent-Replayed") == "true" else "queued")
                return r
            if r.status_code in BUSINESS_REJECTS:  # từ chối ngay khi gửi: hết vé, ngoài giờ bán
                count(r.json().get("error", str(r.status_code)))
                r.success()
                return r
            r.failure(f"unexpected {r.status_code}: {r.text[:120]}")
            return None

    def wait_request(self, location, timeout=60):
        """Hỏi trạng thái theo Retry-After đến khi yêu cầu xong. Trả về JSON cuối, hoặc None."""
        started = time.monotonic()
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
                    # Thời gian từ lúc gửi đến khi có kết quả: chỉ số chính của luồng bất đồng bộ.
                    events.request.fire(request_type="QUEUE", name="order request end-to-end",
                                        response_time=(time.monotonic() - started) * 1000,
                                        response_length=0, exception=None, context={})
                    return body
                delay = float(r.headers.get("Retry-After", delay))
        count("request_timeout")
        return None

    def hold(self, ticket_type_id=None, quantity=None, key=None, name="/orders [POST]"):
        """Giữ chỗ đến khi có kết quả. Trả về `Result` 201 + đơn khi thành công, 409 + lỗi khi
        yêu cầu thất bại, response gốc khi bị từ chối ngay, hoặc None khi lỗi hạ tầng."""
        r = self.submit(ticket_type_id or self.target_type(), quantity or random.randint(1, 4),
                        key or uuid.uuid4().hex, name)
        if r is None or r.status_code != 202:
            return r
        final = self.wait_request(r.headers["Location"])
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
            if r.status_code in BUSINESS_REJECTS:
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
            elif r.status_code in BUSINESS_REJECTS:
                r.success()
            else:
                r.failure(f"unexpected {r.status_code}")
            return r


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
            self.client.get(f"/orders/{order_id}/tickets", headers=self.headers, name="/orders/{id}/tickets [GET]")
            if random.random() < 0.05:
                self.cancel(order_id, name="/orders/{id} [DELETE refund]")

    @task(1)
    def my_orders(self):
        self.client.get("/orders/me", headers=self.headers)


# --- S1 Browse --------------------------------------------------------------------

class BrowseUser(HttpUser):
    """Chỉ đọc, không đăng nhập: nền tải của người xem."""
    abstract = True
    wait_time = between(0.5, 2)

    @task(4)
    def concerts(self):
        self.client.get("/concerts")
        self.client.get("/concerts?on_sale=true", name="/concerts?on_sale=true")

    @task(3)
    def concert_detail(self):
        self.client.get(f"/concerts/{CONCERT_ID}", name="/concerts/{id}")

    @task(3)
    def ticket_types(self):
        self.client.get(f"/concerts/{CONCERT_ID}/ticket-types", name="/concerts/{id}/ticket-types")


# --- S2 Flash sale ----------------------------------------------------------------

class FlashSaleUser(BaseUser):
    """Đăng nhập xong thì đứng chờ; khi đủ quân số (spawning_complete) tất cả cùng giữ chỗ
    một hạng vé có kho nhỏ (đặt TICKET_TYPE_ID), rồi thanh toán và dừng.

    Phân tán: mỗi worker tự bắn `spawning_complete` khi xong phần của mình, nên thời điểm
    mở bán trên các worker lệch nhau cỡ ramp-up của worker đó.
    """
    abstract = True
    wait_time = constant(0)

    @task
    def rush(self):
        SALE_OPEN.wait()
        r = self.hold(name="/orders [POST flash]")
        if r is not None and r.status_code == 201:
            order_id = self.held.pop()
            if self.pay_full(order_id) == "paid":
                count("paid")
        raise StopUser()


@events.spawning_complete.add_listener
def _open_sale(user_count, **kwargs):
    if SCENARIO == "flash":
        log.info("MỞ BÁN: %s user sẵn sàng", user_count)
        SALE_OPEN.set()


# --- S4 Abandon -------------------------------------------------------------------

class AbandonUser(BaseUser):
    """Giữ chỗ một lần rồi không làm gì: đo thời gian kho hồi về (sweeper)."""
    abstract = True
    wait_time = constant(5)

    @task
    def hold_once(self):
        self.hold(name="/orders [POST abandon]")
        raise StopUser()


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
        if len(fresh) == 1 and len(ids) == 1 and len(accepted) == len(results):
            count("verify_ok")
        else:
            count("verify_fail")
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
            count("verify_ok")
        else:
            count("verify_fail")
            log.error("cancel x10 (%s) order %s: %s", label, order_id, dict(codes))

    @task(1)
    def cancel_held(self):
        r = self.hold(quantity=1)
        if r is not None and r.status_code == 201:
            self._parallel_cancel(self.held.pop(), "held")

    @task(1)
    def refund_paid(self):
        r = self.hold(quantity=1)
        if r is None or r.status_code != 201:
            return
        order_id = self.held.pop()
        if self.pay_full(order_id) == "paid":
            self._parallel_cancel(order_id, "paid")

    @task(1)
    def pay_vs_cancel(self):
        """Thanh toán và hủy cùng lúc: đúng một bên thắng (paid hoặc cancelled)."""
        r = self.hold(quantity=1)
        if r is None or r.status_code != 201:
            return
        order_id = self.held.pop()
        token = self.start_payment(order_id)
        if token is None:
            return
        pay_job = gevent.spawn(self.gateway_pay, token, "success", "/mock-gateway/pay [POST race]")
        cancel_job = gevent.spawn(self.cancel, order_id, "/orders/{id} [DELETE race]")
        gevent.joinall([pay_job, cancel_job])
        paid = pay_job.value == "paid"
        cancel_r = cancel_job.value
        # DELETE đến sau khi webhook thắng sẽ hoàn tiền (200, status=refunded): vẫn là
        # "thanh toán thắng". Chỉ status=cancelled mới là hủy thắng.
        final = cancel_r.json().get("status") if cancel_r is not None and cancel_r.status_code == 200 else None
        cancelled = final == "cancelled"
        if paid != cancelled:  # đúng một bên thắng
            count("verify_ok")
            if paid and final != "refunded":
                self.cancel(order_id)  # hoàn tiền để trả kho
        else:
            count("verify_fail")
            log.error("pay-vs-cancel order %s: paid=%s cancel=%s", order_id, paid, final)


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
        jobs = [gevent.spawn(self.gateway_pay, token, "success", "/mock-gateway/pay [POST x5]") for _ in range(5)]
        gevent.joinall(jobs)
        outcomes = Counter(j.value for j in jobs)
        if outcomes.get("paid") == 1 and outcomes.get("duplicate") == 4:
            count("verify_ok")
        else:
            count("verify_fail")
            log.error("webhook x5 order %s: %s", order_id, dict(outcomes))
        self.cancel(order_id)  # hoàn tiền để trả kho


# --- Chọn kịch bản ------------------------------------------------------------------

SCENARIOS = {
    "smoke": FunnelUser, "funnel": FunnelUser, "browse": BrowseUser, "flash": FlashSaleUser,
    "abandon": AbandonUser, "retry": RetryStormUser, "contention": ContentionUser, "replay": ReplayUser,
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
    url = os.getenv("LOADTEST_DATABASE_URL")
    if url:
        check_inventory(url)


def check_inventory(url: str) -> None:
    """Bất biến kho: remaining + Σ quantity(held, paid) == total; vé available == remaining;
    không vé nào có order_id mà không ở held/sold. Chạy sau khi tải dừng (đơn held còn hạn
    vẫn tính vào Σ, nên kết quả đúng ngay cả khi sweeper chưa chạy)."""
    try:
        import psycopg
    except ImportError:
        log.error("Cần psycopg để kiểm tra kho: pip install psycopg[binary]")
        return
    query = """
        SELECT tt.id, tt.name, tt.total_quantity, tt.remaining,
               COALESCE((SELECT SUM(o.quantity) FROM orders o
                         WHERE o.ticket_type_id = tt.id AND o.status IN ('held', 'paid')), 0) AS active,
               (SELECT COUNT(*) FROM tickets t WHERE t.ticket_type_id = tt.id AND t.status = 'available') AS available,
               (SELECT COUNT(*) FROM tickets t WHERE t.ticket_type_id = tt.id AND t.status = 'held') AS held,
               (SELECT COUNT(*) FROM tickets t WHERE t.ticket_type_id = tt.id AND t.status = 'sold') AS sold
        FROM ticket_types tt ORDER BY tt.id
    """
    with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://")) as conn:
        rows = conn.execute(query).fetchall()
    out = Path(__file__).with_name(f"inventory_check_{SCENARIO}.csv")
    ok = True
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["ticket_type_id", "name", "total", "remaining", "active_orders", "available", "held", "sold", "ok"])
        for tid, name, total, remaining, active, available, held, sold in rows:
            row_ok = (remaining + active == total and available == remaining
                      and held + sold == active and remaining >= 0)
            ok &= row_ok
            writer.writerow([tid, name, total, remaining, active, available, held, sold, row_ok])
    log.log(logging.INFO if ok else logging.ERROR, "Bất biến kho: %s → %s", "ĐÚNG" if ok else "SAI", out)
