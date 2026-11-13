"""Cổng thanh toán GIẢ LẬP — đóng vai bên thứ ba khi phát triển, kiểm thử và đo tải.

Hành xử giống một cổng thật ở những điểm hệ thống phụ thuộc vào: cấp mã giao dịch,
đưa khách tới trang thanh toán, rồi gọi webhook có chữ ký HMAC-SHA256. Khác biệt duy
nhất là "trang thanh toán" do chính ứng dụng phục vụ (xem routers/mock_gateway.py).

Thay bằng cổng thật: viết một adapter khác hiện thực `PaymentGateway`
(app/services/payment_gateway.py) và đăng ký trong `build_payment_gateway`.
"""
import base64
import hashlib
import hmac
import json
import uuid
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

from app.services.errors import InvalidWebhookSignature
from app.services.payment_gateway import PaymentEvent, PaymentIntent, RefundResult

SIGNATURE_HEADER = "x-mock-signature"


class MockPaymentGateway:
    name = "mock"

    def __init__(self, secret: str, base_url: str):
        self._secret = secret.encode()
        self._base_url = base_url.rstrip("/")

    def _sign(self, data: bytes) -> str:
        return hmac.new(self._secret, data, hashlib.sha256).hexdigest()

    # --- Phía hệ thống gọi cổng (PaymentGateway) ---------------------------

    def create_payment(self, order_id: int, amount: Decimal, description: str) -> PaymentIntent:
        payment_id = f"mock_{uuid.uuid4().hex}"
        token = self.checkout_token(payment_id, amount)
        return PaymentIntent(
            payment_id=payment_id,
            payment_url=f"{self._base_url}/mock-gateway/checkout?token={token}",
            raw={"order_id": order_id, "amount": str(amount), "description": description},
        )

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> PaymentEvent:
        signature = headers.get(SIGNATURE_HEADER, "")
        if not hmac.compare_digest(signature, self._sign(body)):
            raise InvalidWebhookSignature("invalid webhook signature")
        try:
            data = json.loads(body)
            return PaymentEvent(
                payment_id=str(data["payment_id"]),
                succeeded=data["status"] == "succeeded",
                amount=Decimal(str(data["amount"])),
                raw=body.decode(),
            )
        except (ValueError, KeyError, TypeError, InvalidOperation) as exc:
            raise InvalidWebhookSignature("malformed webhook body") from exc

    def refund(self, payment_id: str, amount: Decimal, reason: str) -> RefundResult:
        return RefundResult(True, {"refund_id": f"mock_rf_{uuid.uuid4().hex}",
                                   "payment_id": payment_id, "amount": str(amount), "reason": reason})

    # --- Phía "bên thứ ba" (chỉ cổng giả lập mới có) -------------------------

    def checkout_token(self, payment_id: str, amount: Decimal) -> str:
        """Token của trang thanh toán: mang mã giao dịch và số tiền, có chữ ký."""
        payload = base64.urlsafe_b64encode(
            json.dumps({"payment_id": payment_id, "amount": str(amount)}).encode()
        ).decode()
        return f"{payload}.{self._sign(payload.encode())}"

    def read_checkout_token(self, token: str) -> tuple[str, Decimal]:
        payload, _, signature = token.partition(".")
        if not hmac.compare_digest(signature, self._sign(payload.encode())):
            raise InvalidWebhookSignature("invalid checkout token")
        data = json.loads(base64.urlsafe_b64decode(payload))
        return data["payment_id"], Decimal(data["amount"])

    def build_webhook(self, payment_id: str, amount: Decimal, succeeded: bool) -> tuple[dict, bytes]:
        """Tạo (headers, body) của webhook y như cổng sẽ gửi về hệ thống."""
        body = json.dumps({
            "payment_id": payment_id,
            "amount": str(amount),
            "status": "succeeded" if succeeded else "failed",
        }).encode()
        return {SIGNATURE_HEADER: self._sign(body)}, body
