"""Adapter tới các hệ thống bên ngoài (cổng thanh toán)."""
from app import config
from app.integrations.mock_payment import MockPaymentGateway
from app.integrations.vnpay import VNPayGateway
from app.services.payment_gateway import PaymentGateway


def _required(name: str) -> str:
    value = getattr(config, name)
    if not value:
        raise ValueError(f"{name} is required when PAYMENT_PROVIDER={config.PAYMENT_PROVIDER!r}")
    return value


def payment_providers() -> list[str]:
    """Tên các cổng trong PAYMENT_PROVIDER (vd. "vnpay" hoặc "vnpay,mock"), theo đúng thứ tự đó."""
    names = [name.strip().lower() for name in config.PAYMENT_PROVIDER.split(",") if name.strip()]
    return list(dict.fromkeys(names)) or ["mock"]


def build_payment_gateways() -> dict[str, PaymentGateway]:
    """Mọi cổng liệt kê trong PAYMENT_PROVIDER, cổng đầu tiên là mặc định."""
    return {name: build_payment_gateway(name) for name in payment_providers()}


def build_payment_gateway(provider: str | None = None) -> PaymentGateway:
    """Dựng adapter của một cổng (mặc định: cổng đầu tiên). Thêm cổng mới = thêm một nhánh ở đây."""
    provider = provider or payment_providers()[0]
    if provider == "mock":
        return MockPaymentGateway(config.PAYMENT_WEBHOOK_SECRET, config.PUBLIC_BASE_URL)

    # Trả tiền xong, VNPay đưa khách về trang quay về (vnp_ReturnUrl, gửi kèm từng giao dịch).
    # IPN báo kết quả (máy-máy) là GET /payments/webhook, khai một lần trong trang quản trị
    # merchant của VNPay.
    return_url = f"{config.PUBLIC_BASE_URL}/payments/return/{provider}"
    if provider == "vnpay":
        return VNPayGateway(
            tmn_code=_required("VNPAY_TMN_CODE"), hash_secret=_required("VNPAY_HASH_SECRET"),
            pay_url=config.VNPAY_PAY_URL, api_url=config.VNPAY_API_URL, return_url=return_url,
            timeout=config.PAYMENT_GATEWAY_TIMEOUT_SECONDS,
        )
    raise ValueError(f"Unsupported PAYMENT_PROVIDER: {provider!r}")
