"""Webhook / IPN của cổng thanh toán. Không dùng JWT: cổng tự chứng minh bằng chữ ký trên nội dung.

Mỗi cổng "gọi về" một kiểu khác nhau; router chỉ đưa nó về dạng (headers, body) chung
và trả lời đúng định dạng cổng đó chờ. Việc chốt đơn luôn đi qua một đường duy nhất:
PaymentService.handle_webhook.

- Mock / MoMo: POST, kết quả trong thân JSON. MoMo chờ HTTP 204, ngược lại sẽ gửi lại.
- VNPay: GET, kết quả trong query string; VNPay chờ JSON {"RspCode", "Message"} với mã
  "00" nghĩa là hệ thống đã ghi nhận.
"""
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse

from app import config
from app.api.deps import get_payment_service
from app.api.schemas import WebhookResponse
from app.integrations import momo, vnpay
from app.services.errors import InvalidWebhookSignature, NotFound
from app.services.payment_service import PaymentService, WebhookOutcome

router = APIRouter(prefix="/payments", tags=["payments"])


class WebhookTimingMiddleware:
    """Ghi nhận thời điểm webhook vào ứng dụng trước khi FastAPI xử lý dependencies."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] == "/payments/webhook":
            scope.setdefault("state", {})["webhook_received_at"] = time.monotonic()
        await self.app(scope, receive, send)


def webhook_response(outcome: WebhookOutcome) -> WebhookResponse:
    return WebhookResponse(result=outcome.result, order_id=outcome.order.id,
                           order_status=outcome.order.status)


@router.post(
    "/webhook",
    response_model=WebhookResponse,
    summary="Cổng thanh toán báo kết quả (POST): chốt đơn và xuất vé, hoặc hoàn kho",
    responses={
        204: {"description": "Khi PAYMENT_PROVIDER=momo: MoMo chỉ cần HTTP 204 để biết IPN đã được nhận."},
        401: {"description": "Sai chữ ký (InvalidWebhookSignature)."},
        404: {"description": "Không có giao dịch này."},
    },
)
async def payment_webhook(request: Request, svc: PaymentService = Depends(get_payment_service)):
    # Chữ ký được tính trên đúng từng byte cổng gửi, nên phải đọc body thô.
    body = await request.body()
    headers = {name.lower(): value for name, value in request.headers.items()}
    outcome = await run_in_threadpool(
        svc.handle_webhook, headers, body, getattr(request.state, "webhook_received_at", None),
    )
    if isinstance(svc.gateway, momo.MoMoGateway):
        return Response(status_code=status.HTTP_204_NO_CONTENT)
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
    if not isinstance(svc.gateway, vnpay.VNPayGateway):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    # Chữ ký VNPay tính trên các tham số của query string, nên đưa nguyên query vào làm body.
    body = request.url.query.encode()
    try:
        outcome = await run_in_threadpool(
            svc.handle_webhook, {}, body, getattr(request.state, "webhook_received_at", None),
        )
    except InvalidWebhookSignature:
        return vnpay.ipn_reply(*vnpay.IPN_REPLY_SIGNATURE)
    except NotFound:
        return vnpay.ipn_reply(*vnpay.IPN_REPLY_NOT_FOUND)
    return vnpay.ipn_reply_for(outcome.result)


@router.get("/return", include_in_schema=False)
def payment_return() -> RedirectResponse:
    """Cổng đưa khách về đây sau khi trả tiền xong. Chỉ chuyển tiếp về giao diện; trạng
    thái đơn do IPN quyết định, giao diện hỏi lại GET /orders/{id} để hiển thị."""
    return RedirectResponse(config.PAYMENT_RETURN_URL, status_code=status.HTTP_303_SEE_OTHER)
