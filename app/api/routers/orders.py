from typing import Annotated

import segno
from fastapi import APIRouter, Depends, Header, HTTPException, Response, status

from app.api.deps import get_current_user, get_order_service, get_payment_service
from app.api.schemas import CreateOrderRequest, OrderResponse, PaymentResponse, TicketResponse
from app.repositories.models import User
from app.services.order_service import OrderService
from app.services.payment_service import PaymentService

# chạy xác thực cho từng endpoint
router = APIRouter(
    prefix="/orders",
    tags=["orders"],
    dependencies=[Depends(get_current_user)],
)

IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=64,
        pattern=r"^[A-Za-z0-9_\-:.]+$",
        description=(
            "Khoá duy nhất cho MỘT lần đặt vé (khuyến nghị UUID). Gửi lại cùng khoá khi "
            "retry sau timeout/lỗi mạng: server trả lại đơn đã tạo (200, header "
            "Idempotent-Replayed: true) thay vì tạo đơn mới."
        ),
    ),
]


@router.post(
    "",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Giữ chỗ: tạo đơn `held`, trừ tạm kho và giữ vé đến `expires_at`",
    responses={
        200: {"model": OrderResponse, "description": "Retry cùng Idempotency-Key: trả lại đơn đã tạo."},
        400: {"description": "Chưa mở bán (SaleNotOpen) hoặc concert đã diễn ra (SaleClosed)."},
        409: {"description": "Hết vé, vượt giới hạn vé đang giữ, hoặc Idempotency-Key đã dùng cho yêu cầu khác."},
    },
)
def create_order(
    body: CreateOrderRequest,
    response: Response,
    idempotency_key: IdempotencyKey = None,
    user: User = Depends(get_current_user),
    svc: OrderService = Depends(get_order_service),
):
    order, created = svc.place_order(user.id, body.ticket_type_id, body.quantity, idempotency_key)
    if not created:
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replayed"] = "true"
    return order


@router.get("/me", response_model=list[OrderResponse])
def get_my_orders(user: User = Depends(get_current_user), svc: OrderService = Depends(get_order_service)):
    return svc.list_my_orders(user.id)


@router.get("/{order_id}", response_model=OrderResponse)
def get_order(
    order_id: int,
    user: User = Depends(get_current_user),
    svc: OrderService = Depends(get_order_service),
):
    return svc.get_order(user.id, order_id)


@router.delete("/{order_id}", response_model=OrderResponse, summary="Hủy đơn đang giữ chỗ và trả vé về kho")
def cancel_order(
    order_id: int,
    user: User = Depends(get_current_user),
    svc: OrderService = Depends(get_order_service),
):
    return svc.cancel_order(user.id, order_id)


@router.post(
    "/{order_id}/pay",
    response_model=PaymentResponse,
    summary="Tạo giao dịch ở cổng thanh toán cho đơn đang giữ chỗ",
    responses={409: {"description": "Đơn không còn ở trạng thái giữ chỗ, hoặc đã quá hạn (HoldExpired)."}},
)
def pay_order(
    order_id: int,
    user: User = Depends(get_current_user),
    svc: PaymentService = Depends(get_payment_service),
):
    return svc.start_payment(user.id, order_id)


@router.get("/{order_id}/tickets", response_model=list[TicketResponse])
def get_order_tickets(
    order_id: int,
    user: User = Depends(get_current_user),
    svc: OrderService = Depends(get_order_service),
):
    return svc.list_tickets(user.id, order_id)


@router.get(
    "/{order_id}/tickets/{ticket_id}/qr",
    response_class=Response,
    summary="Ảnh mã QR (SVG) của một vé đã xuất",
    responses={200: {"content": {"image/svg+xml": {}}}, 404: {"description": "Vé chưa được xuất."}},
)
def get_ticket_qr(
    order_id: int,
    ticket_id: int,
    user: User = Depends(get_current_user),
    svc: OrderService = Depends(get_order_service),
):
    ticket = next((t for t in svc.list_tickets(user.id, order_id) if t.id == ticket_id), None)
    if ticket is None or ticket.qr_code is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket has not been issued")
    svg = segno.make(ticket.qr_code, error="m").svg_inline(scale=6, border=2)
    return Response(content=svg, media_type="image/svg+xml")
