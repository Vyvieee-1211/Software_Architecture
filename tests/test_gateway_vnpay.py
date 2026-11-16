"""Adapter VNPay: chữ ký URL thanh toán, IPN (GET), hoàn tiền; và luồng đầy đủ qua API.

Chữ ký trong test tính bằng công thức tham chiếu viết riêng (không gọi lại adapter) để
bắt được lỗi ghép chuỗi ký. HTTP ra ngoài được giả bằng httpx.MockTransport.
"""
import hashlib
import hmac
import json
from datetime import datetime
from decimal import Decimal
from urllib.parse import parse_qsl, quote_plus, urlencode, urlparse

import httpx
import pytest

from app.api import deps
from app.integrations import build_payment_gateway
from app.integrations.vnpay import VN_TZ, VNPayGateway
from app.services.errors import InvalidWebhookSignature, PaymentGatewayError
from tests.test_api import auth_header, client, place_order  # noqa: F401  (fixture dùng chung)
from tests.test_booking_flow import hold, remaining, start_payment

SECRET = "VNPAYSECRET1234567890"
TMN = "TMNTEST1"
FIXED_NOW = datetime(2026, 11, 16, 12, 0, 0, tzinfo=VN_TZ)


def reference_sign(params: dict) -> str:
    data = "&".join(f"{k}={quote_plus(str(v))}" for k, v in sorted(params.items()))
    return hmac.new(SECRET.encode(), data.encode(), hashlib.sha512).hexdigest()


def ipn_query(txn_ref: str, amount_vnd: int, *, response_code="00", transaction_no="14000001",
              pay_date="20261116120500", secret_ok=True) -> str:
    params = {
        "vnp_TmnCode": TMN, "vnp_Amount": str(amount_vnd * 100), "vnp_BankCode": "NCB",
        "vnp_OrderInfo": "Thanh toan don ve", "vnp_PayDate": pay_date,
        "vnp_ResponseCode": response_code, "vnp_TransactionNo": transaction_no,
        "vnp_TransactionStatus": response_code, "vnp_TxnRef": txn_ref,
    }
    params["vnp_SecureHash"] = reference_sign(params) if secret_ok else "0" * 128
    return urlencode(params)


@pytest.fixture
def api_calls():
    """Ghi lại các lời gọi API JSON ra VNPay và trả về phản hồi do test đặt trước."""
    calls = {"requests": [], "reply": {"vnp_ResponseCode": "00", "vnp_Message": "Refund success"}}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["requests"].append(json.loads(request.content))
        reply = calls["reply"]
        if isinstance(reply, Exception):
            raise reply
        return httpx.Response(200, json=reply)

    calls["transport"] = httpx.MockTransport(handler)
    return calls


@pytest.fixture
def gateway(api_calls):
    return VNPayGateway(TMN, SECRET, "https://pay.test/vpcpay.html", "https://api.test/transaction",
                        "http://localhost:8000/payments/return",
                        transport=api_calls["transport"], clock=lambda: FIXED_NOW)


# --- Đơn vị ----------------------------------------------------------------

def test_payment_url_carries_signed_params(gateway):
    intent = gateway.create_payment(7, Decimal("150000"), "Thanh toan don ve #7")

    url = urlparse(intent.payment_url)
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://pay.test/vpcpay.html"
    params = dict(parse_qsl(url.query))
    assert params["vnp_TmnCode"] == TMN and params["vnp_Command"] == "pay"
    assert params["vnp_Amount"] == "15000000"  # VND × 100
    assert params["vnp_TxnRef"] == intent.payment_id and intent.payment_id.startswith("o7-")
    assert params["vnp_CreateDate"] == "20261116120000"
    assert params["vnp_ReturnUrl"] == "http://localhost:8000/payments/return"
    assert params.pop("vnp_SecureHash") == reference_sign(params)


def test_ipn_is_parsed_after_signature_check(gateway):
    event = gateway.parse_webhook({}, ipn_query("o7-abc", 150000).encode())
    assert event.payment_id == "o7-abc" and event.succeeded and event.amount == Decimal("150000")
    assert json.loads(event.raw)["vnp_TransactionNo"] == "14000001"

    failed = gateway.parse_webhook({}, ipn_query("o7-abc", 150000, response_code="24").encode())
    assert not failed.succeeded


def test_ipn_rejects_bad_or_missing_signature(gateway):
    with pytest.raises(InvalidWebhookSignature):
        gateway.parse_webhook({}, ipn_query("o7-abc", 150000, secret_ok=False).encode())
    with pytest.raises(InvalidWebhookSignature):
        gateway.parse_webhook({}, b"vnp_TxnRef=o7-abc&vnp_Amount=100")
    with pytest.raises(InvalidWebhookSignature):
        gateway.parse_webhook({}, b"")


def test_refund_posts_request_signed_with_pipe_formula(gateway, api_calls):
    event = gateway.parse_webhook({}, ipn_query("o7-abc", 150000).encode())
    result = gateway.refund("o7-abc", Decimal("150000"), "cancelled by customer", payment_raw=event.raw)

    assert result.succeeded and result.raw["vnp_ResponseCode"] == "00"
    (sent,) = api_calls["requests"]
    assert sent["vnp_Command"] == "refund" and sent["vnp_TransactionType"] == "02"
    assert sent["vnp_TxnRef"] == "o7-abc" and sent["vnp_Amount"] == 15000000
    assert sent["vnp_TransactionNo"] == "14000001" and sent["vnp_TransactionDate"] == "20261116120500"
    data = "|".join(str(sent[k]) for k in (
        "vnp_RequestId", "vnp_Version", "vnp_Command", "vnp_TmnCode", "vnp_TransactionType",
        "vnp_TxnRef", "vnp_Amount", "vnp_TransactionNo", "vnp_TransactionDate", "vnp_CreateBy",
        "vnp_CreateDate", "vnp_IpAddr", "vnp_OrderInfo"))
    assert sent["vnp_SecureHash"] == hmac.new(SECRET.encode(), data.encode(), hashlib.sha512).hexdigest()


def test_refund_reports_gateway_rejection_and_failures(gateway, api_calls):
    raw = gateway.parse_webhook({}, ipn_query("o7-abc", 150000).encode()).raw

    api_calls["reply"] = {"vnp_ResponseCode": "91", "vnp_Message": "Transaction not found"}
    assert not gateway.refund("o7-abc", Decimal("150000"), "x", payment_raw=raw).succeeded

    api_calls["reply"] = httpx.ConnectError("gateway down")
    with pytest.raises(PaymentGatewayError):
        gateway.refund("o7-abc", Decimal("150000"), "x", payment_raw=raw)

    with pytest.raises(PaymentGatewayError, match="vnp_TransactionNo"):
        gateway.refund("o7-abc", Decimal("150000"), "x", payment_raw="")
    assert len(api_calls["requests"]) == 2  # thiếu dữ liệu IPN thì không gọi ra ngoài


def test_build_payment_gateway_requires_credentials(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "PAYMENT_PROVIDER", "vnpay")
    monkeypatch.setattr(config, "VNPAY_TMN_CODE", "")
    with pytest.raises(ValueError, match="VNPAY_TMN_CODE"):
        build_payment_gateway()
    monkeypatch.setattr(config, "VNPAY_TMN_CODE", TMN)
    monkeypatch.setattr(config, "VNPAY_HASH_SECRET", SECRET)
    assert isinstance(build_payment_gateway(), VNPayGateway)


# --- Qua API: giữ chỗ → URL VNPay → IPN → vé → hoàn tiền ---------------------

def test_vnpay_flow_through_api(client, gateway, api_calls, monkeypatch):
    monkeypatch.setattr(deps, "payment_gateway", gateway)
    h = auth_header(client)
    order = hold(client, h, quantity=1)

    payment = start_payment(client, h, order["id"])
    assert payment["provider"] == "vnpay"
    params = dict(parse_qsl(urlparse(payment["payment_url"]).query))
    assert params["vnp_Amount"] == "10000" and params["vnp_TxnRef"] == payment["payment_id"]

    # IPN sai chữ ký: VNPay chờ mã 97, không phải HTTP 401; đơn không đổi.
    r = client.get("/payments/webhook?" + ipn_query(payment["payment_id"], 100, secret_ok=False))
    assert r.status_code == 200 and r.json()["RspCode"] == "97"
    assert client.get(f"/orders/{order['id']}", headers=h).json()["status"] == "held"

    # IPN không rõ giao dịch → 01.
    assert client.get("/payments/webhook?" + ipn_query("o0-unknown", 100)).json()["RspCode"] == "01"

    # IPN thành công → đơn paid, vé có QR; gửi lặp → 02.
    query = ipn_query(payment["payment_id"], 100)
    assert client.get("/payments/webhook?" + query).json() == {"RspCode": "00", "Message": "Confirm Success"}
    assert client.get(f"/orders/{order['id']}", headers=h).json()["status"] == "paid"
    tickets = client.get(f"/orders/{order['id']}/tickets", headers=h).json()
    assert len(tickets) == 1 and tickets[0]["qr_code"]
    assert client.get("/payments/webhook?" + query).json()["RspCode"] == "02"

    # Trang quay về chỉ chuyển tiếp tới giao diện.
    r = client.get("/payments/return?vnp_ResponseCode=00", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/#/orders")

    # Hủy đơn đã trả: API hoàn tiền nhận đúng mã giao dịch VNPay đã gửi trong IPN.
    r = client.delete(f"/orders/{order['id']}", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "refunded"
    assert remaining(client) == 3
    (sent,) = api_calls["requests"]
    assert sent["vnp_Command"] == "refund" and sent["vnp_TransactionNo"] == "14000001"
    assert sent["vnp_TxnRef"] == payment["payment_id"] and sent["vnp_Amount"] == 10000


def test_post_webhook_and_get_ipn_are_provider_specific(client, gateway, monkeypatch):
    # Cổng giả lập (mặc định trong test) không có IPN GET.
    assert client.get("/payments/webhook?x=1").status_code == 404
    # Với VNPay, POST vẫn tồn tại nhưng body rỗng không qua được chữ ký.
    monkeypatch.setattr(deps, "payment_gateway", gateway)
    r = client.post("/payments/webhook", content=b"")
    assert r.status_code == 401 and r.json()["error"] == "InvalidWebhookSignature"
