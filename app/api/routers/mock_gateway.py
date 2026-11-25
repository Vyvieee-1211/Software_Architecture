"""Trang thanh toán của cổng GIẢ LẬP. Chỉ được gắn khi PAYMENT_PROVIDER có "mock".

Đóng vai bên thứ ba: khách mở `payment_url`, chọn kết quả, rồi "cổng" gửi webhook có
chữ ký về hệ thống — đi qua đúng đường xử lý của webhook thật (PaymentService.handle_webhook).
"""
from html import escape

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse

from app.api.deps import get_payment_gateways, get_payment_service
from app.api.routers.payments import webhook_response
from app.api.schemas import MockPayRequest, WebhookResponse
from app.integrations.mock_payment import MockPaymentGateway
from app.services.payment_service import PaymentService

router = APIRouter(prefix="/mock-gateway", tags=["mock payment gateway"])


def get_mock_gateway(gateways=Depends(get_payment_gateways)) -> MockPaymentGateway:
    gateway = next((gw for gw in gateways.values() if isinstance(gw, MockPaymentGateway)), None)
    if gateway is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return gateway


@router.get("/checkout", response_class=HTMLResponse, include_in_schema=False)
def checkout(token: str, order_id: int | None = None,
             gateway: MockPaymentGateway = Depends(get_mock_gateway)):
    payment_id, amount = gateway.read_checkout_token(token)
    # Trang đơn để quay về: chỉ là đường dẫn nội bộ dựng từ số nguyên, không nhận URL tuỳ ý
    # (tránh open redirect).
    back = f"/#/orders/{order_id}" if order_id is not None else "/#/my-orders"
    return f"""<!doctype html>
<html lang="vi"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cổng thanh toán giả lập</title>
<body style="font-family:sans-serif;max-width:28rem;margin:3rem auto;padding:0 1rem">
<h1>Cổng thanh toán giả lập</h1>
<p>Giao dịch <code>{escape(payment_id)}</code></p>
<p>Số tiền: <strong>{escape(str(amount))} VND</strong></p>
<button onclick="pay('success')">Thanh toán thành công</button>
<button onclick="pay('failed')">Thanh toán thất bại</button>
<p><a href="{back}">← Quay lại đơn hàng (chưa thanh toán)</a></p>
<p id="out" role="status"></p>
<script>
async function pay(result) {{
  document.querySelectorAll('button').forEach((b) => {{ b.disabled = true; }});
  const out = document.getElementById('out');
  try {{
    const response = await fetch('/mock-gateway/pay', {{
      method: 'POST', headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{token: new URLSearchParams(location.search).get('token'), result}}),
    }});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || response.status);
    // Như cổng thật: báo kết quả rồi đưa khách về trang đơn của hệ thống bán vé.
    out.textContent = 'Đã ghi nhận kết quả. Đang quay về trang đơn hàng…';
    setTimeout(() => location.assign('/#/orders/' + data.order_id), 1200);
  }} catch (error) {{
    out.textContent = 'Lỗi: ' + error.message;
    document.querySelectorAll('button').forEach((b) => {{ b.disabled = false; }});
  }}
}}
</script></body></html>"""


@router.post("/pay", response_model=WebhookResponse, summary="Giả lập khách trả tiền xong (hoặc thất bại)")
def pay(
    body: MockPayRequest,
    gateway: MockPaymentGateway = Depends(get_mock_gateway),
    svc: PaymentService = Depends(get_payment_service),
):
    payment_id, amount = gateway.read_checkout_token(body.token)
    headers, webhook_body = gateway.build_webhook(payment_id, amount, body.result == "success")
    return webhook_response(svc.handle_webhook(headers, webhook_body, provider=gateway.name))
