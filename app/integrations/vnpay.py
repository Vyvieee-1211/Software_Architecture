"""Cổng VNPay (Thanh toán trực tuyến, API v2.1.0) — https://sandbox.vnpayment.vn/apis/

Cách VNPay làm việc, và adapter này bám theo:
- Tạo giao dịch KHÔNG gọi API: hệ thống tự dựng URL trang thanh toán với các tham số đã
  ký HMAC-SHA512 (`vnp_SecureHash`) rồi chuyển khách tới đó.
- Trả tiền xong, VNPay gọi IPN bằng GET, kết quả nằm trong query string (cũng ký
  HMAC-SHA512). Router đưa nguyên query string vào `parse_webhook` làm `body`. Khách
  đồng thời được đưa về `vnp_ReturnUrl`; trang đó chỉ hiển thị, không quyết định đơn.
- Hoàn tiền gọi API JSON `merchant_webapi/api/transaction` (command=refund). API này
  cần `vnp_TransactionNo` và `vnp_PayDate` mà VNPay đã gửi trong IPN, nên adapter đọc
  lại từ `payment_raw` (nguyên văn IPN lưu ở transactions.raw_log).

Địa chỉ IPN không gửi theo từng giao dịch mà khai một lần trong trang quản trị merchant
của VNPay: `{PUBLIC_BASE_URL}/payments/webhook`.
"""
import hashlib
import hmac
import json
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl, quote_plus, urlencode

import httpx

from app.services.errors import InvalidWebhookSignature, PaymentGatewayError
from app.services.payment_gateway import PaymentEvent, PaymentIntent, RefundResult

VN_TZ = timezone(timedelta(hours=7))  # VNPay dùng giờ Việt Nam cho mọi mốc thời gian
TIME_FORMAT = "%Y%m%d%H%M%S"
SUCCESS = "00"
VERSION = "2.1.0"
# Thứ tự ghép chuỗi ký của API hoàn tiền (nối bằng "|"), đúng theo tài liệu VNPay.
REFUND_SIGN_FIELDS = (
    "vnp_RequestId", "vnp_Version", "vnp_Command", "vnp_TmnCode", "vnp_TransactionType",
    "vnp_TxnRef", "vnp_Amount", "vnp_TransactionNo", "vnp_TransactionDate", "vnp_CreateBy",
    "vnp_CreateDate", "vnp_IpAddr", "vnp_OrderInfo",
)
# Mã trả lời IPN mà VNPay chờ. Kết quả đã được ghi nhận (kể cả thất bại hay phải hoàn
# tiền) đều là "00": VNPay chỉ cần biết hệ thống đã nhận và xử lý xong thông báo.
IPN_REPLY_BY_RESULT = {"duplicate": ("02", "Order already confirmed")}
IPN_REPLY_SIGNATURE = ("97", "Invalid signature")
IPN_REPLY_NOT_FOUND = ("01", "Order not found")


def ipn_reply(code: str, message: str) -> dict:
    return {"RspCode": code, "Message": message}


def ipn_reply_for(result: str) -> dict:
    return ipn_reply(*IPN_REPLY_BY_RESULT.get(result, (SUCCESS, "Confirm Success")))


def to_vnpay_amount(amount: Decimal) -> int:
    """VNPay nhận số tiền VND nhân 100, kiểu nguyên."""
    return int((Decimal(amount) * 100).to_integral_value())


class VNPayGateway:
    name = "vnpay"

    def __init__(self, tmn_code: str, hash_secret: str, pay_url: str, api_url: str,
                 return_url: str, *, timeout: float = 10.0,
                 transport: httpx.BaseTransport | None = None,
                 clock: Callable[[], datetime] | None = None):
        self._tmn_code = tmn_code
        self._secret = hash_secret.encode()
        self._pay_url = pay_url
        self._api_url = api_url
        self._return_url = return_url
        self._http = httpx.Client(timeout=timeout, transport=transport)
        self._clock = clock or (lambda: datetime.now(VN_TZ))

    # --- Chữ ký -----------------------------------------------------------

    def _sign(self, data: str) -> str:
        return hmac.new(self._secret, data.encode(), hashlib.sha512).hexdigest()

    def _sign_params(self, params: Mapping[str, object]) -> str:
        """Ký các tham số sắp theo tên, giá trị đã URL-encode (đúng như mẫu của VNPay)."""
        query = "&".join(f"{key}={quote_plus(str(value))}"
                         for key, value in sorted(params.items())
                         if value is not None and value != "")
        return self._sign(query)

    # --- PaymentGateway ---------------------------------------------------

    def create_payment(self, order_id: int, amount: Decimal, description: str) -> PaymentIntent:
        payment_id = f"o{order_id}-{uuid.uuid4().hex}"  # vnp_TxnRef: duy nhất tại merchant
        params = {
            "vnp_Version": VERSION,
            "vnp_Command": "pay",
            "vnp_TmnCode": self._tmn_code,
            "vnp_Amount": to_vnpay_amount(amount),
            "vnp_CurrCode": "VND",
            "vnp_TxnRef": payment_id,
            "vnp_OrderInfo": description,
            "vnp_OrderType": "other",
            "vnp_Locale": "vn",
            "vnp_ReturnUrl": self._return_url,
            "vnp_IpAddr": "127.0.0.1",
            "vnp_CreateDate": self._clock().strftime(TIME_FORMAT),
        }
        params["vnp_SecureHash"] = self._sign_params(params)
        return PaymentIntent(
            payment_id=payment_id,
            payment_url=f"{self._pay_url}?{urlencode(params)}",
            raw={"order_id": order_id, "amount": str(amount), "vnp_TxnRef": payment_id},
        )

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> PaymentEvent:
        """`body` là query string của IPN (router lấy từ URL vì VNPay gọi bằng GET)."""
        params = dict(parse_qsl(body.decode(errors="replace"), keep_blank_values=True))
        signature = params.pop("vnp_SecureHash", "")
        params.pop("vnp_SecureHashType", None)
        if not params or not hmac.compare_digest(signature.lower().encode(),
                                                 self._sign_params(params).encode()):
            raise InvalidWebhookSignature("invalid VNPay signature")
        try:
            succeeded = (params["vnp_ResponseCode"] == SUCCESS
                         and params.get("vnp_TransactionStatus", SUCCESS) == SUCCESS)
            return PaymentEvent(
                payment_id=params["vnp_TxnRef"],
                succeeded=succeeded,
                amount=Decimal(params["vnp_Amount"]) / 100,
                # Lưu dạng JSON để hoàn tiền đọc lại vnp_TransactionNo / vnp_PayDate.
                raw=json.dumps(params, ensure_ascii=False),
            )
        except (KeyError, InvalidOperation) as exc:
            raise InvalidWebhookSignature("malformed VNPay IPN") from exc

    def parse_return(self, query: str) -> PaymentEvent:
        """Khách được đưa về vnp_ReturnUrl với đúng bộ tham số đã ký như IPN."""
        return self.parse_webhook({}, query.encode())

    def refund(self, payment_id: str, amount: Decimal, reason: str,
               payment_raw: str = "") -> RefundResult:
        try:
            paid = json.loads(payment_raw) if payment_raw else {}
        except ValueError:
            paid = {}
        transaction_no = paid.get("vnp_TransactionNo")
        pay_date = paid.get("vnp_PayDate")
        if not transaction_no or not pay_date:
            raise PaymentGatewayError("VNPay refund needs vnp_TransactionNo and vnp_PayDate from the IPN")

        fields = {
            "vnp_RequestId": uuid.uuid4().hex,
            "vnp_Version": VERSION,
            "vnp_Command": "refund",
            "vnp_TmnCode": self._tmn_code,
            "vnp_TransactionType": "02",  # 02 = hoàn toàn bộ
            "vnp_TxnRef": payment_id,
            "vnp_Amount": to_vnpay_amount(amount),
            "vnp_OrderInfo": reason,
            "vnp_TransactionNo": transaction_no,
            "vnp_TransactionDate": pay_date,
            "vnp_CreateBy": "system",
            "vnp_CreateDate": self._clock().strftime(TIME_FORMAT),
            "vnp_IpAddr": "127.0.0.1",
        }
        fields["vnp_SecureHash"] = self._sign("|".join(str(fields[key]) for key in REFUND_SIGN_FIELDS))
        result = self._post(self._api_url, fields)
        return RefundResult(result.get("vnp_ResponseCode") == SUCCESS, result)

    # --- HTTP -------------------------------------------------------------

    def _post(self, url: str, payload: dict) -> dict:
        try:
            response = self._http.post(url, json=payload)
            response.raise_for_status()
            result = response.json()
        except httpx.HTTPError as exc:
            raise PaymentGatewayError(f"VNPay request failed: {exc}") from exc
        except ValueError as exc:
            raise PaymentGatewayError("VNPay returned a non-JSON response") from exc
        if not isinstance(result, dict):
            raise PaymentGatewayError("VNPay returned an unexpected response")
        return result
