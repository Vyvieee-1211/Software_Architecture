"""Lỗi nghiệp vụ — exception Python thuần, KHÔNG biết gì về HTTP.

Tầng API sẽ dịch từng lỗi này sang status code (xem app/main.py).
"""


class BusinessError(Exception):
    """Lớp cha cho mọi lỗi nghiệp vụ.""" 

class NotFound(BusinessError):
    pass


class EmailAlreadyExists(BusinessError):
    pass


class InvalidCredentials(BusinessError):
    pass


class InvalidToken(BusinessError):
    pass


class SaleNotOpen(BusinessError):
    pass


class SoldOut(BusinessError):
    pass


class Forbidden(BusinessError):
    pass


class InvalidState(BusinessError):
    pass


class IdempotencyKeyReused(BusinessError):
    """Cùng Idempotency-Key nhưng nội dung đơn khác với lần gửi đầu tiên."""


class SaleClosed(BusinessError):
    """Concert đã diễn ra: không còn nhận đặt vé."""


class HoldLimitExceeded(BusinessError):
    """Vượt quá số vé một người được giữ tạm cùng lúc."""


class HoldExpired(BusinessError):
    """Đơn giữ chỗ đã quá hạn thanh toán."""


class InvalidWebhookSignature(BusinessError):
    """Webhook thanh toán sai chữ ký hoặc sai định dạng."""


class PaymentGatewayError(BusinessError):
    """Cổng thanh toán từ chối hoặc không phản hồi."""