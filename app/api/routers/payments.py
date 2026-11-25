"""Webhook / IPN của cổng thanh toán. Không dùng JWT: cổng tự chứng minh bằng chữ ký trên nội dung.

Mỗi cổng "gọi về" một kiểu khác nhau; router chỉ đưa nó về dạng (headers, body) chung
và trả lời đúng định dạng cổng đó chờ. Việc chốt đơn luôn đi qua một đường duy nhất:
PaymentService.handle_webhook.

- Mock: POST, kết quả trong thân JSON. Có thể bật nhiều cổng cùng lúc, nên mỗi cổng có
  đường riêng `/payments/webhook/{cổng}` (`/payments/webhook` không kèm tên là cổng mặc định).
- VNPay: GET /payments/webhook, kết quả trong query string; VNPay chờ JSON
  {"RspCode", "Message"} với mã "00" nghĩa là hệ thống đã ghi nhận.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse

from app import config
from app.api.deps import get_payment_service
from app.api.schemas import PaymentProvidersResponse, WebhookResponse
from app.integrations import vnpay
from app.services.errors import BusinessError, InvalidWebhookSignature, NotFound
from app.services.payment_service import PaymentService, WebhookOutcome

router = APIRouter(prefix="/payments", tags=["payments"])


def webhook_response(outcome: WebhookOutcome) -> WebhookResponse:
    return WebhookResponse(result=outcome.result, order_id=outcome.order.id,
                           order_status=outcome.order.status)


@router.get("/providers", response_model=PaymentProvidersResponse,
            summary="Các cổng thanh toán khách được chọn")
def payment_providers(svc: PaymentService = Depends(get_payment_service)):
    return PaymentProvidersResponse(providers=svc.providers)


@router.post(
    "/webhook/{provider}",
    response_model=WebhookResponse,
    summary="Cổng `provider` báo kết quả (POST)",
    responses={401: {"description": "Sai chữ ký."}},
)
async def provider_webhook(provider: str, request: Request,
                           svc: PaymentService = Depends(get_payment_service)):
    if provider not in svc.gateways:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return await _post_webhook(request, svc, provider)


@router.post(
    "/webhook",
    response_model=WebhookResponse,
    summary="Cổng thanh toán báo kết quả (POST): chốt đơn và xuất vé, hoặc hoàn kho",
    responses={
        401: {"description": "Sai chữ ký (InvalidWebhookSignature)."},
        404: {"description": "Không có giao dịch này."},
    },
)
async def payment_webhook(request: Request, svc: PaymentService = Depends(get_payment_service)):
    return await _post_webhook(request, svc, svc.gateway.name)


async def _post_webhook(request: Request, svc: PaymentService, provider: str):
    # Chữ ký được tính trên đúng từng byte cổng gửi, nên phải đọc body thô.
    body = await request.body()
    headers = {name.lower(): value for name, value in request.headers.items()}
    outcome = await run_in_threadpool(svc.handle_webhook, headers, body, provider)
    return webhook_response(outcome)


@router.get(
    "/webhook",
    summary="IPN của VNPay (GET, kết quả trong query string)",
    responses={
        200: {"description": 'VNPay chờ {"RspCode": "00"} khi đã ghi nhận; "02" đã xử lý trước đó, '
                             '"97" sai chữ ký, "01" không có giao dịch.'},
        404: {"description": "Chỉ có khi PAYMENT_PROVIDER=vnpay."},
    },
)
async def vnpay_ipn(request: Request, svc: PaymentService = Depends(get_payment_service)) -> dict:
    provider = next((name for name, gw in svc.gateways.items()
                     if isinstance(gw, vnpay.VNPayGateway)), None)
    if provider is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    # Chữ ký VNPay tính trên các tham số của query string, nên đưa nguyên query vào làm body.
    body = request.url.query.encode()
    try:
        outcome = await run_in_threadpool(svc.handle_webhook, {}, body, provider)
    except InvalidWebhookSignature:
        return vnpay.ipn_reply(*vnpay.IPN_REPLY_SIGNATURE)
    except NotFound:
        return vnpay.ipn_reply(*vnpay.IPN_REPLY_NOT_FOUND)
    return vnpay.ipn_reply_for(outcome.result)


@router.get("/return", include_in_schema=False)
@router.get("/return/{provider}", include_in_schema=False)
async def payment_return(request: Request, provider: str | None = None,
                         svc: PaymentService = Depends(get_payment_service)) -> RedirectResponse:
    """Cổng đưa khách về đây sau khi trả tiền xong, kèm kết quả đã ký.

    Nếu chữ ký hợp lệ thì chốt đơn luôn (giống IPN, trùng lặp là vô hại) rồi đưa khách
    tới trang đơn đó; nhờ vậy sandbox chạy được trên localhost dù IPN không gọi tới.
    Không đọc được kết quả thì chỉ chuyển về danh sách đơn.
    """
    target = config.PAYMENT_RETURN_URL
    try:
        outcome = await run_in_threadpool(svc.handle_return, request.url.query, provider)
    except BusinessError:
        outcome = None
    if outcome is not None:
        target = f"{target.rstrip('/')}/{outcome.order.id}"
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)
