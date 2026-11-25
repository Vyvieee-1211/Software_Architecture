"""Bật nhiều cổng cùng lúc (PAYMENT_PROVIDER=vnpay,momo): khách chọn cổng khi thanh toán.

Mỗi giao dịch nhớ cổng của nó (transactions.provider): webhook, trang quay về và hoàn tiền
đều đi đúng cổng đã thu tiền.
"""
import json

import httpx
import pytest

from app import config
from app.api import deps
from app.integrations import build_payment_gateways
from app.integrations.momo import MoMoGateway
from app.integrations.vnpay import VNPayGateway
from tests import test_gateway_momo as momo_t
from tests import test_gateway_vnpay as vnpay_t
from tests.test_api import auth_header, client, place_order  # noqa: F401  (fixture dùng chung)
from tests.test_booking_flow import hold, remaining


@pytest.fixture
def calls():
    """Lời gọi HTTP ra ngoài, gom theo cổng."""
    sent = {"vnpay": [], "momo": []}

    def vnpay_handler(request: httpx.Request) -> httpx.Response:
        sent["vnpay"].append(json.loads(request.content))
        return httpx.Response(200, json={"vnp_ResponseCode": "00", "vnp_Message": "ok"})

    def momo_handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.rsplit("/", 1)[-1]
        sent["momo"].append((path, json.loads(request.content)))
        if path == "create":
            return httpx.Response(200, json={"resultCode": 0, "message": "ok", "payUrl": momo_t.PAY_URL})
        return httpx.Response(200, json={"resultCode": 0, "message": "ok"})

    sent["vnpay_transport"] = httpx.MockTransport(vnpay_handler)
    sent["momo_transport"] = httpx.MockTransport(momo_handler)
    return sent


@pytest.fixture
def both(client, calls, monkeypatch):
    vnpay = VNPayGateway(vnpay_t.TMN, vnpay_t.SECRET, "https://pay.test/vpcpay.html",
                         "https://api.test/transaction", "http://localhost:8000/payments/return/vnpay",
                         transport=calls["vnpay_transport"], clock=lambda: vnpay_t.FIXED_NOW)
    momo = MoMoGateway(momo_t.PARTNER, momo_t.ACCESS_KEY, momo_t.SECRET, "https://momo.test/v2/gateway/api",
                       "http://localhost:8000/payments/return/momo",
                       "http://localhost:8000/payments/webhook/momo", transport=calls["momo_transport"])
    monkeypatch.setattr(deps, "payment_gateway", vnpay)
    monkeypatch.setattr(deps, "payment_gateways", {"vnpay": vnpay, "momo": momo})
    return vnpay, momo


def test_build_payment_gateways_reads_a_list(monkeypatch):
    monkeypatch.setattr(config, "PAYMENT_PROVIDER", " vnpay , momo ")
    for name, value in [("VNPAY_TMN_CODE", "x"), ("VNPAY_HASH_SECRET", "y"), ("MOMO_PARTNER_CODE", "p"),
                        ("MOMO_ACCESS_KEY", "a"), ("MOMO_SECRET_KEY", "s")]:
        monkeypatch.setattr(config, name, value)
    gateways = build_payment_gateways()
    assert list(gateways) == ["vnpay", "momo"]
    assert gateways["momo"]._ipn_url.endswith("/payments/webhook/momo")


def test_customer_picks_gateway_and_each_settles_on_its_own(client, both, calls):
    h = auth_header(client)
    assert client.get("/payments/providers").json() == {"providers": ["vnpay", "momo"]}

    r = client.post("/orders/999/pay", headers=h, json={"provider": "paypal"})
    assert r.status_code == 400 and r.json()["error"] == "UnsupportedPaymentProvider"

    # Đơn 1 trả bằng MoMo qua IPN riêng của MoMo.
    first = hold(client, h, quantity=1)
    r = client.post(f"/orders/{first['id']}/pay", headers=h, json={"provider": "momo"})
    assert r.status_code == 200 and r.json()["provider"] == "momo"
    momo_payment = r.json()
    r = client.post("/payments/webhook/momo", content=momo_t.ipn_body(momo_payment["payment_id"], 100))
    assert r.status_code == 204
    assert client.get(f"/orders/{first['id']}", headers=h).json()["status"] == "paid"

    # Đơn 2 trả bằng cổng mặc định (VNPay), chốt qua trang quay về của VNPay.
    second = hold(client, h, quantity=1)
    vnpay_payment = client.post(f"/orders/{second['id']}/pay", headers=h).json()
    assert vnpay_payment["provider"] == "vnpay"
    r = client.get("/payments/return/vnpay?" + vnpay_t.ipn_query(vnpay_payment["payment_id"], 100),
                   follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith(f"/#/orders/{second['id']}")
    assert client.get(f"/orders/{second['id']}", headers=h).json()["status"] == "paid"

    # Hủy đơn trả bằng MoMo: lệnh hoàn tiền đi tới MoMo, không phải cổng mặc định.
    before = remaining(client)
    assert client.delete(f"/orders/{first['id']}", headers=h).json()["status"] == "refunded"
    assert remaining(client) == before + 1
    assert [path for path, _ in calls["momo"]] == ["create", "refund"] and calls["vnpay"] == []


def test_switching_gateway_creates_new_payment_and_late_double_payment_is_refunded(client, both, calls):
    h = auth_header(client)
    order = hold(client, h, quantity=1)
    via_vnpay = client.post(f"/orders/{order['id']}/pay", headers=h, json={"provider": "vnpay"}).json()
    again = client.post(f"/orders/{order['id']}/pay", headers=h, json={"provider": "vnpay"}).json()
    assert again["payment_id"] == via_vnpay["payment_id"]  # bấm lại cùng cổng: cùng giao dịch
    via_momo = client.post(f"/orders/{order['id']}/pay", headers=h, json={"provider": "momo"}).json()
    assert via_momo["payment_id"] != via_vnpay["payment_id"]

    # Khách lỡ trả ở cả hai cổng: lần đầu chốt đơn, lần sau được hoàn tiền đúng cổng đó.
    assert client.post("/payments/webhook/momo",
                       content=momo_t.ipn_body(via_momo["payment_id"], 100)).status_code == 204
    r = client.get("/payments/webhook?" + vnpay_t.ipn_query(via_vnpay["payment_id"], 100))
    assert r.json()["RspCode"] == "00"
    assert client.get(f"/orders/{order['id']}", headers=h).json()["status"] == "paid"
    (refund,) = calls["vnpay"]
    assert refund["vnp_Command"] == "refund" and refund["vnp_TxnRef"] == via_vnpay["payment_id"]
