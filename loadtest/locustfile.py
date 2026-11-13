"""Kịch bản kiểm thử tải bằng Locust — mô phỏng "săn vé" theo luồng giữ chỗ → thanh toán.

Mỗi user ảo: đăng ký + đăng nhập một lần, sau đó lặp: xem concert, xem hạng vé,
giữ chỗ (nặng nhất), thanh toán qua cổng giả lập, thỉnh thoảng hủy giữ chỗ.

Chạy (headless, 200 user, tăng 20 user/giây, 2 phút):
    locust -f loadtest/locustfile.py --host http://localhost:8000 \
           --headless -u 200 -r 20 -t 2m --csv loadtest/result

Yêu cầu PAYMENT_PROVIDER=mock ở API.
"""
import random
import uuid
from urllib.parse import parse_qs, urlparse

from locust import HttpUser, between, task


class TicketBuyer(HttpUser):
    wait_time = between(0.5, 2)

    def on_start(self):
        email = f"load-{uuid.uuid4().hex[:10]}@example.com"
        self.client.post("/auth/register", json={"email": email, "password": "secret123"})
        r = self.client.post("/auth/login", json={"email": email, "password": "secret123"})
        self.headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
        self.held: list[int] = []

        concerts = self.client.get("/concerts").json()
        self.ticket_type_ids: list[int] = []
        for c in concerts:
            for t in self.client.get(f"/concerts/{c['id']}/ticket-types").json():
                self.ticket_type_ids.append(t["id"])

    @task(3)
    def browse(self):
        self.client.get("/concerts")

    @task(3)
    def view_ticket_types(self):
        self.client.get("/concerts/1/ticket-types")

    @task(5)
    def hold(self):
        if not self.ticket_type_ids:
            return
        with self.client.post(
            "/orders",
            json={"ticket_type_id": random.choice(self.ticket_type_ids), "quantity": random.randint(1, 4)},
            headers={**self.headers, "Idempotency-Key": uuid.uuid4().hex},
            name="/orders [POST]",
            catch_response=True,
        ) as r:
            if r.status_code == 201:
                self.held.append(r.json()["id"])
            elif r.status_code in (400, 409):
                r.success()  # hết vé / vượt giới hạn giữ: kết quả nghiệp vụ đúng
            else:
                r.failure(f"unexpected {r.status_code}")

    @task(4)
    def pay(self):
        if not self.held:
            return
        order_id = self.held.pop()
        with self.client.post(f"/orders/{order_id}/pay", headers=self.headers,
                              name="/orders/{id}/pay [POST]", catch_response=True) as r:
            if r.status_code == 409:
                r.success()  # đã hết hạn / đã hủy
                return
            if r.status_code != 200:
                r.failure(f"unexpected {r.status_code}")
                return
            token = parse_qs(urlparse(r.json()["payment_url"]).query)["token"][0]
        result = "success" if random.random() < 0.9 else "failed"
        self.client.post("/mock-gateway/pay", json={"token": token, "result": result},
                         name="/mock-gateway/pay [POST]")
        if result == "success":
            self.client.get(f"/orders/{order_id}/tickets", headers=self.headers,
                            name="/orders/{id}/tickets [GET]")

    @task(1)
    def cancel(self):
        if self.held:
            order_id = self.held.pop()
            self.client.delete(f"/orders/{order_id}", headers=self.headers, name="/orders/{id} [DELETE]")

    @task(1)
    def my_orders_list(self):
        self.client.get("/orders/me", headers=self.headers)
