"""Adapter tới các hệ thống bên ngoài (cổng thanh toán)."""
from app import config
from app.integrations.mock_payment import MockPaymentGateway
from app.integrations.momo import MoMoGateway
from app.integrations.vnpay import VNPayGateway
from app.services.payment_gateway import PaymentGateway


def _required(name: str) -> str:
    value = getattr(config, name)
    if not value:
        raise ValueError(f"{name} is required when PAYMENT_PROVIDER={config.PAYMENT_PROVIDER!r}")
    return value


def build_payment_gateway() -> PaymentGateway:
    """Chọn adapter theo PAYMENT_PROVIDER. Thêm cổng mới = thêm một nhánh ở đây."""
    provider = config.PAYMENT_PROVIDER
    if provider == "mock":
        return MockPaymentGateway(config.PAYMENT_WEBHOOK_SECRET, config.PUBLIC_BASE_URL)

    # Cổng thật gọi về hai địa chỉ: IPN báo kết quả (máy-máy) và trang đưa khách quay lại.
    ipn_url = f"{config.PUBLIC_BASE_URL}/payments/webhook"
    return_url = f"{config.PUBLIC_BASE_URL}/payments/return"
    if provider == "vnpay":
        return VNPayGateway(
            tmn_code=_required("VNPAY_TMN_CODE"), hash_secret=_required("VNPAY_HASH_SECRET"),
            pay_url=config.VNPAY_PAY_URL, api_url=config.VNPAY_API_URL, return_url=return_url,
            timeout=config.PAYMENT_GATEWAY_TIMEOUT_SECONDS,
        )
    if provider == "momo":
        return MoMoGateway(
            partner_code=_required("MOMO_PARTNER_CODE"), access_key=_required("MOMO_ACCESS_KEY"),
            secret_key=_required("MOMO_SECRET_KEY"), api_url=config.MOMO_API_URL,
            redirect_url=return_url, ipn_url=ipn_url,
            timeout=config.PAYMENT_GATEWAY_TIMEOUT_SECONDS,
        )
    raise ValueError(f"Unsupported PAYMENT_PROVIDER: {provider!r}")
