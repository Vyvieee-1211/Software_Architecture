"""Cổng thanh toán nhìn từ phía nghiệp vụ — không phụ thuộc nhà cung cấp cụ thể.

Mỗi nhà cung cấp (giả lập, VNPay, MoMo…) là một adapter trong `app/integrations/`
hiện thực giao diện này. Tầng nghiệp vụ chỉ biết ba thao tác: tạo giao dịch, đọc
webhook đã được xác thực, và hoàn tiền.
"""
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class PaymentIntent:
    """Kết quả tạo giao dịch: khách được chuyển tới `payment_url` để trả tiền."""
    payment_id: str
    payment_url: str
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PaymentEvent:
    """Phản hồi của cổng thanh toán, ĐÃ được kiểm tra chữ ký."""
    payment_id: str
    succeeded: bool
    amount: Decimal
    raw: str  # nguyên văn nội dung webhook, lưu vào transactions.raw_log


@dataclass(frozen=True)
class RefundResult:
    succeeded: bool
    raw: dict = field(default_factory=dict)


class PaymentGateway(Protocol):
    name: str

    def create_payment(self, order_id: int, amount: Decimal, description: str) -> PaymentIntent:
        """Ném PaymentGatewayError nếu cổng từ chối hoặc không phản hồi."""
        ...

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> PaymentEvent:
        """Kiểm tra chữ ký rồi đọc webhook. `headers` có tên viết thường.

        Ném InvalidWebhookSignature nếu chữ ký sai hoặc nội dung không đọc được.
        """
        ...

    def refund(self, payment_id: str, amount: Decimal, reason: str) -> RefundResult:
        """Ném PaymentGatewayError nếu cổng không phản hồi."""
        ...
