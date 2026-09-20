"""Kịch bản kiểm thử tải bằng Locust — mô phỏng "săn vé".

Mỗi user ảo: đăng ký + đăng nhập một lần, sau đó lặp: xem concert, xem hạng vé,
đặt vé (nặng nhất), thỉnh thoảng huỷ vé.

Chạy (headless, 200 user, tăng 20 user/giây, 2 phút):
    locust -f loadtest/locustfile.py --host http://localhost:8000 \
           --headless -u 200 -r 20 -t 2m --csv loadtest/result

Trên Kaggle: xem README mục "Kiểm thử tải".
"""
import random
import uuid

from locust import HttpUser, between, task


class TicketBuyer(HttpUser):
    wait_time = between(0.5, 2)

    def on_start(self):
        email = f"load-{uuid.uuid4().hex[:10]}@example.com"
        self.client.post("/auth/register", json={"email": email, "password": "secret123"})
        r = self.client.post("/auth/login", json={"email": email, "password": "secret123"})
        self.headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
        self.my_orders: list[int] = []

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
    def buy(self):
        if not self.ticket_type_ids:
            return
        with self.client.post(
            "/orders",
            json={"ticket_type_id": random.choice(self.ticket_type_ids), "quantity": random.randint(1, 4)},
            headers=self.headers,
            name="/orders [POST]",
            catch_response=True,
        ) as r:
            if r.status_code == 201:
                self.my_orders.append(r.json()["id"])
            elif r.status_code in (400, 409):
                r.success()
            else:
                r.failure(f"unexpected {r.status_code}")

    @task(1)
    def cancel(self):
        if self.my_orders:
            order_id = self.my_orders.pop()
            self.client.delete(f"/orders/{order_id}", headers=self.headers, name="/orders/{id} [DELETE]")

    @task(1)
    def my_orders_list(self):
        self.client.get("/orders/me", headers=self.headers)
