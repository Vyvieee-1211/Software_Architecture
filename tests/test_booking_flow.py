"""Luồng giữ chỗ → thanh toán → xuất vé QR / hoàn kho, đi qua API và cổng thanh toán giả lập."""
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from sqlalchemy import func, select, update

from app.api import deps
from app.repositories import database
from app.repositories.models import Concert, Order, Ticket, TicketType, Transaction
from tests.conftest import add_ticket_type
from tests.test_api import auth_header, client  # noqa: F401  (fixture dùng chung)


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def remaining(client, index=0):
    return client.get("/concerts/1/ticket-types").json()[index]["remaining"]


def hold(client, headers, quantity=2, ticket_type_id=1):
    r = client.post("/orders", json={"ticket_type_id": ticket_type_id, "quantity": quantity}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def start_payment(client, headers, order_id):
    r = client.post(f"/orders/{order_id}/pay", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def pay(client, payment, result="success"):
    token = parse_qs(urlparse(payment["payment_url"]).query)["token"][0]
    return client.post("/mock-gateway/pay", json={"token": token, "result": result})


def expire_now(order_id):
    with database.SessionLocal() as db:
        db.execute(update(Order).where(Order.id == order_id).values(expires_at=now() - timedelta(seconds=1)))
        db.commit()


def ticket_rows(status):
    with database.SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(Ticket).where(Ticket.status == status))


def test_hold_sets_price_expiry_and_holds_ticket_rows(client):
    h = auth_header(client)
    before = now()
    order = hold(client, h, quantity=2)

    assert order["status"] == "held"
    assert float(order["unit_price"]) == 100 and float(order["total_amount"]) == 200
    expires_at = datetime.fromisoformat(order["expires_at"])
    assert timedelta(minutes=11) < expires_at - before <= timedelta(minutes=12, seconds=5)
    assert remaining(client) == 1
    assert ticket_rows("held") == 2 and ticket_rows("available") == 1

    tickets = client.get(f"/orders/{order['id']}/tickets", headers=h).json()
    assert [t["status"] for t in tickets] == ["held", "held"]
    assert all(t["qr_code"] is None for t in tickets)


def test_successful_payment_issues_qr_tickets(client):
    h = auth_header(client)
    order = hold(client, h, quantity=2)
    payment = start_payment(client, h, order["id"])
    assert float(payment["amount"]) == 200 and payment["provider"] == "mock"

    r = pay(client, payment)
    assert r.status_code == 200
    assert r.json() == {"result": "paid", "order_id": order["id"], "order_status": "paid"}

    paid = client.get(f"/orders/{order['id']}", headers=h).json()
    assert paid["status"] == "paid" and paid["paid_at"] is not None
    tickets = client.get(f"/orders/{order['id']}/tickets", headers=h).json()
    assert len(tickets) == 2
    assert all(t["status"] == "sold" and t["qr_code"] for t in tickets)
    assert len({t["qr_code"] for t in tickets}) == 2
    assert deps.ticket_codes.verify(tickets[0]["qr_code"]) == tickets[0]["id"]
    assert deps.ticket_codes.verify(tickets[0]["qr_code"][:-2] + "xx") is None

    qr = client.get(f"/orders/{order['id']}/tickets/{tickets[0]['id']}/qr", headers=h)
    assert qr.status_code == 200 and qr.headers["content-type"].startswith("image/svg+xml")

    # Kho không đổi khi chốt: vé đã bị trừ từ lúc giữ chỗ.
    assert remaining(client) == 1
    # Đơn đã thanh toán là đơn hoàn thành: không hủy qua API giữ chỗ.
    r = client.delete(f"/orders/{order['id']}", headers=h)
    assert r.status_code == 409 and r.json()["detail"] == "order already paid"
    assert remaining(client) == 1


def test_failed_payment_returns_tickets(client):
    h = auth_header(client)
    order = hold(client, h, quantity=2)
    r = pay(client, start_payment(client, h, order["id"]), result="failed")
    assert r.json() == {"result": "failed", "order_id": order["id"], "order_status": "failed"}
    assert remaining(client) == 3
    assert ticket_rows("available") == 3


def test_duplicate_webhook_is_applied_once(client):
    h = auth_header(client)
    order = hold(client, h, quantity=1)
    payment = start_payment(client, h, order["id"])
    assert pay(client, payment).json()["result"] == "paid"
    assert pay(client, payment).json()["result"] == "duplicate"
    with database.SessionLocal() as db:
        events = db.execute(select(Transaction.type, Transaction.status)
                            .where(Transaction.order_id == order["id"]).order_by(Transaction.id)).all()
    assert events == [("payment", "pending"), ("payment", "succeeded")]
    assert ticket_rows("sold") == 1


def test_pay_twice_reuses_pending_payment(client):
    h = auth_header(client)
    order = hold(client, h, quantity=1)
    first = start_payment(client, h, order["id"])
    assert start_payment(client, h, order["id"]) == first


def test_webhook_with_bad_signature_is_rejected(client):
    h = auth_header(client)
    order = hold(client, h, quantity=1)
    payment = start_payment(client, h, order["id"])
    body = f'{{"payment_id": "{payment["payment_id"]}", "amount": "100", "status": "succeeded"}}'
    r = client.post("/payments/webhook", content=body, headers={"X-Mock-Signature": "forged"})
    assert r.status_code == 401 and r.json()["error"] == "InvalidWebhookSignature"
    assert client.get(f"/orders/{order['id']}", headers=h).json()["status"] == "held"


def test_webhook_through_real_endpoint(client):
    h = auth_header(client)
    order = hold(client, h, quantity=1)
    payment = start_payment(client, h, order["id"])
    headers, body = deps.payment_gateway.build_webhook(payment["payment_id"], 100, True)
    r = client.post("/payments/webhook", content=body, headers=headers)
    assert r.status_code == 200 and r.json()["result"] == "paid"


def test_expired_hold_returns_tickets_and_cannot_be_paid(client):
    h = auth_header(client)
    order = hold(client, h, quantity=2)
    expire_now(order["id"])

    r = client.post(f"/orders/{order['id']}/pay", headers=h)
    assert r.status_code == 409 and r.json()["error"] == "HoldExpired"

    assert deps.get_order_service().expire_holds() == 1
    assert deps.get_order_service().expire_holds() == 0  # chạy lại không hoàn thêm
    assert client.get(f"/orders/{order['id']}", headers=h).json()["status"] == "expired"
    assert remaining(client) == 3 and ticket_rows("available") == 3


def test_payment_arriving_after_expiry_is_refunded_not_issued(client):
    h = auth_header(client)
    order = hold(client, h, quantity=2)
    payment = start_payment(client, h, order["id"])
    expire_now(order["id"])  # khách trả tiền đúng lúc hết 12 phút

    r = pay(client, payment)
    assert r.json()["result"] == "refund_pending"
    assert ticket_rows("sold") == 0
    with database.SessionLocal() as db:
        events = db.execute(select(Transaction.type, Transaction.status)
                            .where(Transaction.order_id == order["id"]).order_by(Transaction.id)).all()
    assert events == [("payment", "pending"), ("payment", "succeeded"),
                      ("refund", "pending"), ("refund", "succeeded")]

    assert deps.get_order_service().expire_holds() == 1
    assert remaining(client) == 3


def test_wrong_amount_is_refunded_not_issued(client):
    h = auth_header(client)
    order = hold(client, h, quantity=2)
    payment = start_payment(client, h, order["id"])
    headers, body = deps.payment_gateway.build_webhook(payment["payment_id"], 1, True)
    assert client.post("/payments/webhook", content=body, headers=headers).json()["result"] == "refund_pending"
    assert client.get(f"/orders/{order['id']}", headers=h).json()["status"] == "held"


def test_cancel_hold_returns_tickets(client):
    h = auth_header(client)
    order = hold(client, h, quantity=3)
    assert client.delete(f"/orders/{order['id']}", headers=h).json()["status"] == "cancelled"
    assert remaining(client) == 3 and ticket_rows("available") == 3
    assert client.post(f"/orders/{order['id']}/pay", headers=h).status_code == 409


def test_hold_limit_per_user(client):
    with database.SessionLocal() as db:
        add_ticket_type(db, id=2, concert_id=1, name="Big", price=10, total_quantity=50)
        db.commit()
    h = auth_header(client)
    hold(client, h, quantity=8, ticket_type_id=2)
    r = client.post("/orders", json={"ticket_type_id": 2, "quantity": 3}, headers=h)
    assert r.status_code == 409 and r.json()["error"] == "HoldLimitExceeded"
    hold(client, h, quantity=2, ticket_type_id=2)  # vừa đủ 10

    # Người khác không bị ảnh hưởng.
    hold(client, auth_header(client, "other@example.com"), quantity=10, ticket_type_id=2)


def test_paid_and_expired_holds_do_not_count_toward_limit(client):
    with database.SessionLocal() as db:
        add_ticket_type(db, id=2, concert_id=1, name="Big", price=10, total_quantity=50)
        db.commit()
    h = auth_header(client)
    paid = hold(client, h, quantity=10, ticket_type_id=2)
    pay(client, start_payment(client, h, paid["id"]))
    stale = hold(client, h, quantity=10, ticket_type_id=2)
    expire_now(stale["id"])
    hold(client, h, quantity=10, ticket_type_id=2)


def test_cannot_book_after_concert_started(client):
    h = auth_header(client)
    with database.SessionLocal() as db:
        db.get(Concert, 1).start_time = now() - timedelta(minutes=1)
        db.commit()
    r = client.post("/orders", json={"ticket_type_id": 1, "quantity": 1}, headers=h)
    assert r.status_code == 400 and r.json()["error"] == "SaleClosed"
    assert remaining(client) == 3


def test_cannot_pay_or_view_other_users_order(client):
    owner = auth_header(client, "owner@example.com")
    other = auth_header(client, "other@example.com")
    order = hold(client, owner, quantity=1)
    assert client.post(f"/orders/{order['id']}/pay", headers=other).status_code == 403
    assert client.get(f"/orders/{order['id']}/tickets", headers=other).status_code == 403


def test_inventory_invariant_after_mixed_flow(client):
    """Mọi hạng vé: remaining + vé đang giữ/đã bán = total; vé trong bảng tickets khớp bộ đếm."""
    users = [auth_header(client, f"u{i}@example.com") for i in range(3)]
    paid = hold(client, users[0], quantity=1)
    pay(client, start_payment(client, users[0], paid["id"]))
    cancelled = hold(client, users[1], quantity=1)
    client.delete(f"/orders/{cancelled['id']}", headers=users[1])
    held = hold(client, users[2], quantity=1)
    expire_now(held["id"])
    deps.get_order_service().expire_holds()

    with database.SessionLocal() as db:
        ticket_type = db.get(TicketType, 1)
        active = db.scalar(select(func.coalesce(func.sum(Order.quantity), 0))
                           .where(Order.status.in_(["held", "paid"])))
        assert ticket_type.remaining + active == ticket_type.total_quantity
        assert ticket_rows("available") == ticket_type.remaining
        assert ticket_rows("sold") == 1 and ticket_rows("held") == 0
