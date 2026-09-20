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
    """Ví dụ: huỷ một đơn đã huỷ rồi."""
