"""Adapter MoMo: tạo giao dịch qua API, IPN (POST JSON), hoàn tiền; và luồng đầy đủ qua API.

Chữ ký trong test tính bằng công thức tham chiếu viết riêng (không gọi lại adapter).
HTTP ra ngoài được giả bằng httpx.MockTransport.
"""
import hashlib
import hmac
import json
from decimal import Decimal

import httpx
import pytest

from app.api import deps
from app.integrations import build_payment_gateway
from app.integrations.momo import MoMoGateway
from app.services.errors import InvalidWebhookSignature, PaymentGatewayError
from tests.test_api import auth_header, client, place_order  # noqa: F401  (fixture dùng chung)
from tests.test_booking_flow import hold, remaining, start_payment

PARTNER, ACCESS_KEY, SECRET = "MOMOTEST", "accesskey123", "secretkey456"
PAY_URL = "https://test-payment.momo.vn/v2/gateway/pay?t=abc"


def reference_sign(raw: str) -> str:
    return hmac.new(SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()


def ipn_body(order_id: str, amount: int, *, result_code=0, trans_id=2147000001, secret_ok=True) -> bytes:
    data = {
        "partnerCode": PARTNER, "orderId": order_id, "requestId": order_id, "amount": amount,
        "orderInfo": "Thanh toan don ve", "orderType": "momo_wallet", "transId": trans_id,
        "resultCode": result_code, "message": "Successful." if result_code == 0 else "Failed",
        "payType": "qr", "responseTime": 1763280000000, "extraData": "",
    }
    raw = (f"accessKey={ACCESS_KEY}&amount={data['amount']}&extraData=&message={data['message']}"
           f"&orderId={order_id}&orderInfo={data['orderInfo']}&orderType=momo_wallet"
           f"&partnerCode={PARTNER}&payType=qr&requestId={order_id}"
           f"&responseTime={data['responseTime']}&resultCode={result_code}&transId={trans_id}")
    data["signature"] = reference_sign(raw) if secret_ok else "0" * 64
    return json.dumps(data).encode()


@pytest.fixture
def api_calls():
    """Ghi lại các lời gọi API ra MoMo theo đường dẫn; phản hồi do test đặt trước."""
    calls = {
        "requests": [],
        "reply": {"/create": {"resultCode": 0, "message": "Successful.", "payUrl": PAY_URL},
                  "/refund": {"resultCode": 0, "message": "Successful."}},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.rsplit("/", 1)[-1]
        calls["requests"].append((f"/{path}", json.loads(request.content)))
        reply = calls["reply"][f"/{path}"]
        if isinstance(reply, Exception):
            raise reply
        return httpx.Response(200, json=reply)

    calls["transport"] = httpx.MockTransport(handler)
    return calls


@pytest.fixture
def gateway(api_calls):
    return MoMoGateway(PARTNER, ACCESS_KEY, SECRET, "https://momo.test/v2/gateway/api",
                       "http://localhost:8000/payments/return",
                       "http://localhost:8000/payments/webhook", transport=api_calls["transport"])


# --- Đơn vị ----------------------------------------------------------------

def test_create_payment_calls_api_with_signed_body(gateway, api_calls):
    intent = gateway.create_payment(7, Decimal("150000"), "Thanh toan don ve #7")

    assert intent.payment_url == PAY_URL and intent.payment_id.startswith("o7-")
    ((path, sent),) = api_calls["requests"]
    assert path == "/create" and "accessKey" not in sent  # accessKey chỉ nằm trong chuỗi ký
    assert sent["partnerCode"] == PARTNER and sent["amount"] == 150000
    assert sent["orderId"] == sent["requestId"] == intent.payment_id
    assert sent["ipnUrl"] == "http://localhost:8000/payments/webhook"
    raw = (f"accessKey={ACCESS_KEY}&amount=150000&extraData=&ipnUrl={sent['ipnUrl']}"
           f"&orderId={sent['orderId']}&orderInfo=Thanh toan don ve #7&partnerCode={PARTNER}"
           f"&redirectUrl={sent['redirectUrl']}&requestId={sent['requestId']}&requestType=captureWallet")
    assert sent["signature"] == reference_sign(raw)


def test_create_payment_failures_become_gateway_errors(gateway, api_calls):
    api_calls["reply"]["/create"] = {"resultCode": 11, "message": "Access denied"}
    with pytest.raises(PaymentGatewayError, match="Access denied"):
        gateway.create_payment(7, Decimal("150000"), "x")

    api_calls["reply"]["/create"] = httpx.ReadTimeout("slow")
    with pytest.raises(PaymentGatewayError):
        gateway.create_payment(7, Decimal("150000"), "x")


def test_ipn_is_parsed_after_signature_check(gateway):
    event = gateway.parse_webhook({}, ipn_body("o7-abc", 150000))
    assert event.payment_id == "o7-abc" and event.succeeded and event.amount == Decimal("150000")
    assert json.loads(event.raw)["transId"] == 2147000001

    assert not gateway.parse_webhook({}, ipn_body("o7-abc", 150000, result_code=1006)).succeeded


def test_ipn_rejects_bad_signature_or_body(gateway):
    with pytest.raises(InvalidWebhookSignature):
        gateway.parse_webhook({}, ipn_body("o7-abc", 150000, secret_ok=False))
    with pytest.raises(InvalidWebhookSignature):
        gateway.parse_webhook({}, b'{"orderId": "o7-abc"}')
    with pytest.raises(InvalidWebhookSignature):
        gateway.parse_webhook({}, b"not json")


def test_refund_posts_signed_request_with_trans_id(gateway, api_calls):
    raw = gateway.parse_webhook({}, ipn_body("o7-abc", 150000)).raw
    result = gateway.refund("o7-abc", Decimal("150000"), "cancelled by customer", payment_raw=raw)

    assert result.succeeded
    ((path, sent),) = api_calls["requests"]
    assert path == "/refund" and sent["transId"] == 2147000001 and sent["amount"] == 150000
    assert sent["orderId"] == sent["requestId"] and sent["orderId"].startswith("rf-")
    expected = reference_sign(f"accessKey={ACCESS_KEY}&amount=150000&description=cancelled by customer"
                              f"&orderId={sent['orderId']}&partnerCode={PARTNER}"
                              f"&requestId={sent['requestId']}&transId=2147000001")
    assert sent["signature"] == expected

    with pytest.raises(PaymentGatewayError, match="transId"):
        gateway.refund("o7-abc", Decimal("150000"), "x", payment_raw="")


def test_build_payment_gateway_requires_credentials(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "PAYMENT_PROVIDER", "momo")
    for name in ("MOMO_PARTNER_CODE", "MOMO_ACCESS_KEY", "MOMO_SECRET_KEY"):
        monkeypatch.setattr(config, name, "")
    with pytest.raises(ValueError, match="MOMO_PARTNER_CODE"):
        build_payment_gateway()
    for name in ("MOMO_PARTNER_CODE", "MOMO_ACCESS_KEY", "MOMO_SECRET_KEY"):
        monkeypatch.setattr(config, name, "x")
    assert isinstance(build_payment_gateway(), MoMoGateway)


# --- Qua API: giữ chỗ → payUrl MoMo → IPN → vé → hoàn tiền --------------------

def test_momo_flow_through_api(client, gateway, api_calls, monkeypatch):
    monkeypatch.setattr(deps, "payment_gateway", gateway)
    h = auth_header(client)
    order = hold(client, h, quantity=1)

    payment = start_payment(client, h, order["id"])
    assert payment["provider"] == "momo" and payment["payment_url"] == PAY_URL
    # Bấm "Thanh toán" lần nữa: trả lại giao dịch đang chờ, không gọi MoMo thêm.
    assert start_payment(client, h, order["id"])["payment_id"] == payment["payment_id"]
    assert [path for path, _ in api_calls["requests"]] == ["/create"]

    r = client.post("/payments/webhook", content=ipn_body(payment["payment_id"], 100, secret_ok=False),
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 401 and r.json()["error"] == "InvalidWebhookSignature"

    body = ipn_body(payment["payment_id"], 100)
    r = client.post("/payments/webhook", content=body, headers={"Content-Type": "application/json"})
    assert r.status_code == 204 and r.content == b""  # MoMo chỉ cần 204
    assert client.get(f"/orders/{order['id']}", headers=h).json()["status"] == "paid"
    assert client.get(f"/orders/{order['id']}/tickets", headers=h).json()[0]["qr_code"]
    # IPN gửi lặp: vẫn 204, không xử lý lại.
    assert client.post("/payments/webhook", content=body,
                       headers={"Content-Type": "application/json"}).status_code == 204

    r = client.delete(f"/orders/{order['id']}", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "refunded"
    assert remaining(client) == 3
    path, sent = api_calls["requests"][-1]
    assert path == "/refund" and sent["transId"] == 2147000001 and sent["amount"] == 100


def test_momo_ipn_failure_releases_hold(client, gateway, monkeypatch):
    monkeypatch.setattr(deps, "payment_gateway", gateway)
    h = auth_header(client)
    order = hold(client, h, quantity=2)
    payment = start_payment(client, h, order["id"])

    r = client.post("/payments/webhook", content=ipn_body(payment["payment_id"], 200, result_code=1006),
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 204
    assert client.get(f"/orders/{order['id']}", headers=h).json()["status"] == "failed"
    assert remaining(client) == 3
