"""Bật nhiều cổng cùng lúc (PAYMENT_PROVIDER=vnpay,mock): khách chọn cổng khi thanh toán.

Mỗi giao dịch nhớ cổng của nó (transactions.provider): webhook, trang quay về và hoàn tiền
đều đi đúng cổng đã thu tiền.
"""
import json

import httpx
import pytest

from app import config
from app.api import deps
from app.integrations import build_payment_gateways
from app.integrations.mock_payment import MockPaymentGateway
from app.integrations.vnpay import VNPayGateway
from tests import test_gateway_vnpay as vnpay_t
from tests.test_api import auth_header, client, place_order  # noqa: F401  (fixture dùng chung)
from tests.test_booking_flow import hold, pay, remaining


@pytest.fixture
def vnpay_calls():
    """Lời gọi API ra VNPay (chỉ có hoàn tiền; tạo giao dịch không gọi API)."""
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"vnp_ResponseCode": "00", "vnp_Message": "ok"})

    return sent, httpx.MockTransport(handler)


@pytest.fixture
def both(client, vnpay_calls, monkeypatch):
    vnpay = VNPayGateway(vnpay_t.TMN, vnpay_t.SECRET, "https://pay.test/vpcpay.html",
                         "https://api.test/transaction", "http://localhost:8000/payments/return/vnpay",
                         transport=vnpay_calls[1], clock=lambda: vnpay_t.FIXED_NOW)
    mock = MockPaymentGateway(config.PAYMENT_WEBHOOK_SECRET, config.PUBLIC_BASE_URL)
    refunds = []
    original_refund = mock.refund

    def recording_refund(*args, **kwargs):
        refunds.append(args[0])
        return original_refund(*args, **kwargs)

    monkeypatch.setattr(mock, "refund", recording_refund)
    monkeypatch.setattr(deps, "payment_gateway", vnpay)
    monkeypatch.setattr(deps, "payment_gateways", {"vnpay": vnpay, "mock": mock})
    return vnpay, mock, refunds


def test_build_payment_gateways_reads_a_list(monkeypatch):
    monkeypatch.setattr(config, "PAYMENT_PROVIDER", " vnpay , mock ")
    monkeypatch.setattr(config, "VNPAY_TMN_CODE", "x")
    monkeypatch.setattr(config, "VNPAY_HASH_SECRET", "y")
    gateways = build_payment_gateways()
    assert list(gateways) == ["vnpay", "mock"]
    assert gateways["vnpay"]._return_url.endswith("/payments/return/vnpay")


def test_customer_picks_gateway_and_each_settles_on_its_own(client, both, vnpay_calls):
    _, _, mock_refunds = both
    h = auth_header(client)
    assert client.get("/payments/providers").json() == {"providers": ["vnpay", "mock"]}

    r = client.post("/orders/999/pay", headers=h, json={"provider": "momo"})
    assert r.status_code == 400 and r.json()["error"] == "UnsupportedPaymentProvider"

    # Đơn 1 trả bằng cổng giả lập (không phải cổng mặc định).
    first = hold(client, h, quantity=1)
    r = client.post(f"/orders/{first['id']}/pay", headers=h, json={"provider": "mock"})
    assert r.status_code == 200 and r.json()["provider"] == "mock"
    assert pay(client, r.json()).status_code == 200
    assert client.get(f"/orders/{first['id']}", headers=h).json()["status"] == "paid"

    # Đơn 2 trả bằng cổng mặc định (VNPay), chốt qua trang quay về của VNPay.
    second = hold(client, h, quantity=1)
    vnpay_payment = client.post(f"/orders/{second['id']}/pay", headers=h).json()
    assert vnpay_payment["provider"] == "vnpay"
    r = client.get("/payments/return/vnpay?" + vnpay_t.ipn_query(vnpay_payment["payment_id"], 100),
                   follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith(f"/#/orders/{second['id']}")
    assert client.get(f"/orders/{second['id']}", headers=h).json()["status"] == "paid"

    # Hủy đơn trả bằng cổng giả lập: hoàn tiền đi tới cổng đó, không phải VNPay (mặc định).
    before = remaining(client)
    assert client.delete(f"/orders/{first['id']}", headers=h).json()["status"] == "refunded"
    assert remaining(client) == before + 1
    assert len(mock_refunds) == 1 and vnpay_calls[0] == []


def test_switching_gateway_creates_new_payment_and_late_double_payment_is_refunded(client, both, vnpay_calls):
    h = auth_header(client)
    order = hold(client, h, quantity=1)
    via_vnpay = client.post(f"/orders/{order['id']}/pay", headers=h, json={"provider": "vnpay"}).json()
    again = client.post(f"/orders/{order['id']}/pay", headers=h, json={"provider": "vnpay"}).json()
    assert again["payment_id"] == via_vnpay["payment_id"]  # bấm lại cùng cổng: cùng giao dịch
    via_mock = client.post(f"/orders/{order['id']}/pay", headers=h, json={"provider": "mock"}).json()
    assert via_mock["payment_id"] != via_vnpay["payment_id"]

    # Khách lỡ trả ở cả hai cổng: lần đầu chốt đơn, lần sau được hoàn tiền đúng cổng đó.
    assert pay(client, via_mock).status_code == 200
    r = client.get("/payments/webhook?" + vnpay_t.ipn_query(via_vnpay["payment_id"], 100))
    assert r.json()["RspCode"] == "00"
    assert client.get(f"/orders/{order['id']}", headers=h).json()["status"] == "paid"
    (refund,) = vnpay_calls[0]
    assert refund["vnp_Command"] == "refund" and refund["vnp_TxnRef"] == via_vnpay["payment_id"]
