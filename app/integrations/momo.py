"""Cổng MoMo (API v2 "Thanh toán bằng ví MoMo") — https://developers.momo.vn/v3/

- Tạo giao dịch: POST JSON tới `/v2/gateway/api/create`, ký HMAC-SHA256; MoMo trả
  `payUrl` để chuyển khách tới.
- Kết quả: MoMo gọi IPN (POST JSON, ký HMAC-SHA256) tới `ipnUrl`; `resultCode` 0 là
  thành công. MoMo coi IPN đã nhận khi được trả HTTP 204 (router lo việc này). Khách
  đồng thời được đưa về `redirectUrl`; trang đó chỉ hiển thị, không quyết định đơn.
- Hoàn tiền: POST JSON tới `/v2/gateway/api/refund` với `transId` MoMo đã gửi trong IPN,
  nên adapter đọc lại từ `payment_raw` (nguyên văn IPN lưu ở transactions.raw_log).

Chuỗi ký của MoMo là các cặp `key=value` nối bằng `&` theo đúng thứ tự họ quy định cho
từng loại thông điệp (các hằng *_SIGN_FIELDS bên dưới), không phải sắp theo tên.
"""
import hashlib
import hmac
import json
import uuid
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl

import httpx

from app.services.errors import InvalidWebhookSignature, PaymentGatewayError
from app.services.payment_gateway import PaymentEvent, PaymentIntent, RefundResult

RESULT_SUCCESS = 0
CREATE_SIGN_FIELDS = ("accessKey", "amount", "extraData", "ipnUrl", "orderId", "orderInfo",
                      "partnerCode", "redirectUrl", "requestId", "requestType")
IPN_SIGN_FIELDS = ("accessKey", "amount", "extraData", "message", "orderId", "orderInfo",
                   "orderType", "partnerCode", "payType", "requestId", "responseTime",
                   "resultCode", "transId")
REFUND_SIGN_FIELDS = ("accessKey", "amount", "description", "orderId", "partnerCode",
                      "requestId", "transId")


def to_momo_amount(amount: Decimal) -> int:
    """MoMo nhận số tiền VND kiểu nguyên."""
    return int(Decimal(amount).to_integral_value())


class MoMoGateway:
    name = "momo"

    def __init__(self, partner_code: str, access_key: str, secret_key: str, api_url: str,
                 redirect_url: str, ipn_url: str, *, timeout: float = 10.0,
                 transport: httpx.BaseTransport | None = None,
                 request_type: str = "captureWallet"):
        self._partner_code = partner_code
        self._access_key = access_key
        self._secret = secret_key.encode()
        self._api_url = api_url.rstrip("/")
        self._redirect_url = redirect_url
        self._ipn_url = ipn_url
        self._request_type = request_type
        self._http = httpx.Client(timeout=timeout, transport=transport)

    # --- Chữ ký -----------------------------------------------------------

    def _sign(self, fields: Mapping[str, object], order: tuple[str, ...]) -> str:
        raw = "&".join(f"{key}={fields[key]}" for key in order)
        return hmac.new(self._secret, raw.encode(), hashlib.sha256).hexdigest()

    # --- PaymentGateway ---------------------------------------------------

    def create_payment(self, order_id: int, amount: Decimal, description: str) -> PaymentIntent:
        payment_id = f"o{order_id}-{uuid.uuid4().hex}"  # orderId của MoMo, tối đa 50 ký tự
        fields = {
            "partnerCode": self._partner_code,
            "requestId": payment_id,
            "orderId": payment_id,
            "amount": to_momo_amount(amount),
            "orderInfo": description,
            "redirectUrl": self._redirect_url,
            "ipnUrl": self._ipn_url,
            "requestType": self._request_type,
            "extraData": "",
            "lang": "vi",
        }
        # accessKey chỉ nằm trong chuỗi ký, không gửi trong thân JSON.
        fields["signature"] = self._sign({**fields, "accessKey": self._access_key}, CREATE_SIGN_FIELDS)
        result = self._post("/create", fields)
        if result.get("resultCode") != RESULT_SUCCESS or not result.get("payUrl"):
            raise PaymentGatewayError(
                f"MoMo rejected the payment: {result.get('resultCode')} {result.get('message', '')}".strip()
            )
        return PaymentIntent(payment_id=payment_id, payment_url=str(result["payUrl"]), raw=result)

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> PaymentEvent:
        try:
            data = json.loads(body)
            signature = str(data.get("signature", ""))
            expected = self._sign({**data, "accessKey": self._access_key}, IPN_SIGN_FIELDS)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise InvalidWebhookSignature("malformed MoMo IPN") from exc
        if not hmac.compare_digest(signature.encode(), expected.encode()):
            raise InvalidWebhookSignature("invalid MoMo signature")
        try:
            return PaymentEvent(
                payment_id=str(data["orderId"]),
                succeeded=int(data["resultCode"]) == RESULT_SUCCESS,
                amount=Decimal(str(data["amount"])),
                raw=body.decode(),
            )
        except (ValueError, TypeError, InvalidOperation) as exc:
            raise InvalidWebhookSignature("malformed MoMo IPN") from exc

    def parse_return(self, query: str) -> PaymentEvent:
        """redirectUrl nhận cùng các trường (và chữ ký) như IPN, nhưng nằm trong query string."""
        params = dict(parse_qsl(query, keep_blank_values=True))
        return self.parse_webhook({}, json.dumps(params, ensure_ascii=False).encode())

    def refund(self, payment_id: str, amount: Decimal, reason: str,
               payment_raw: str = "") -> RefundResult:
        try:
            trans_id = json.loads(payment_raw)["transId"] if payment_raw else None
        except (ValueError, KeyError, TypeError):
            trans_id = None
        if trans_id is None:
            raise PaymentGatewayError("MoMo refund needs transId from the IPN")
        if isinstance(trans_id, str) and trans_id.isdigit():
            trans_id = int(trans_id)  # chốt qua redirectUrl thì transId là chuỗi trong query

        refund_id = f"rf-{uuid.uuid4().hex}"
        fields = {
            "partnerCode": self._partner_code,
            "orderId": refund_id,
            "requestId": refund_id,
            "amount": to_momo_amount(amount),
            "transId": trans_id,
            "lang": "vi",
            "description": reason,
        }
        fields["signature"] = self._sign({**fields, "accessKey": self._access_key}, REFUND_SIGN_FIELDS)
        result = self._post("/refund", fields)
        return RefundResult(result.get("resultCode") == RESULT_SUCCESS, result)

    # --- HTTP -------------------------------------------------------------

    def _post(self, path: str, payload: dict) -> dict:
        try:
            response = self._http.post(f"{self._api_url}{path}", json=payload)
            response.raise_for_status()
            result = response.json()
        except httpx.HTTPError as exc:
            raise PaymentGatewayError(f"MoMo request failed: {exc}") from exc
        except ValueError as exc:
            raise PaymentGatewayError("MoMo returned a non-JSON response") from exc
        if not isinstance(result, dict):
            raise PaymentGatewayError("MoMo returned an unexpected response")
        return result
