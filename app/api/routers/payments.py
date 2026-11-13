"""Webhook của cổng thanh toán. Không dùng JWT: cổng tự chứng minh bằng chữ ký trên nội dung."""
from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool

from app.api.deps import get_payment_service
from app.api.schemas import WebhookResponse
from app.services.payment_service import PaymentService, WebhookOutcome

router = APIRouter(prefix="/payments", tags=["payments"])


def webhook_response(outcome: WebhookOutcome) -> WebhookResponse:
    return WebhookResponse(result=outcome.result, order_id=outcome.order.id,
                           order_status=outcome.order.status)


@router.post(
    "/webhook",
    response_model=WebhookResponse,
    summary="Cổng thanh toán báo kết quả: chốt đơn và xuất vé, hoặc hoàn kho",
    responses={
        401: {"description": "Sai chữ ký (InvalidWebhookSignature)."},
        404: {"description": "Không có giao dịch này."},
    },
)
async def payment_webhook(request: Request, svc: PaymentService = Depends(get_payment_service)):
    # Chữ ký được tính trên đúng từng byte cổng gửi, nên phải đọc body thô.
    body = await request.body()
    headers = {name.lower(): value for name, value in request.headers.items()}
    return webhook_response(await run_in_threadpool(svc.handle_webhook, headers, body))
