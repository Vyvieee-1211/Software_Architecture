"""Adapter tới các hệ thống bên ngoài (cổng thanh toán)."""
from app import config
from app.integrations.mock_payment import MockPaymentGateway
from app.services.payment_gateway import PaymentGateway


def build_payment_gateway() -> PaymentGateway:
    """Chọn adapter theo PAYMENT_PROVIDER. Thêm cổng thật = thêm một nhánh ở đây."""
    if config.PAYMENT_PROVIDER == "mock":
        return MockPaymentGateway(config.PAYMENT_WEBHOOK_SECRET, config.PUBLIC_BASE_URL)
    raise ValueError(f"Unsupported PAYMENT_PROVIDER: {config.PAYMENT_PROVIDER!r}")
