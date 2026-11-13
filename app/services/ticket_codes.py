"""Mã QR của vé: chuỗi có chữ ký HMAC nên không thể tự bịa ra mã hợp lệ.

Dạng: T<ticket_id>.<nonce>.<chữ ký>. Nội dung này được in thành mã QR; cổng soát vé
dùng `verify` để kiểm tra chữ ký trước khi tra vé trong DB.
"""
import base64
import hashlib
import hmac
import secrets


class TicketCodes:
    def __init__(self, secret: str):
        self._secret = secret.encode()

    def _sign(self, payload: str) -> str:
        digest = hmac.new(self._secret, payload.encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest[:18]).decode()

    def issue(self, ticket_id: int) -> str:
        payload = f"T{ticket_id}.{secrets.token_urlsafe(9)}"
        return f"{payload}.{self._sign(payload)}"

    def verify(self, code: str) -> int | None:
        """Trả về ticket_id nếu chữ ký hợp lệ, ngược lại None."""
        payload, _, signature = code.rpartition(".")
        if not payload.startswith("T") or not hmac.compare_digest(signature, self._sign(payload)):
            return None
        ticket_id = payload[1:].split(".", 1)[0]
        return int(ticket_id) if ticket_id.isdigit() else None
