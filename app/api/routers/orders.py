import io
from dataclasses import asdict
from typing import Annotated

import segno
from fastapi import APIRouter, Depends, Header, HTTPException, Response, status

from app import config
from app.api.deps import (
    get_current_user, get_order_request_service, get_order_service, get_payment_service, limit_per_user,
)
from app.api.schemas import (
    CreateOrderRequest, OrderRequestResponse, OrderResponse, PayRequest, PaymentResponse, TicketResponse,
)
from app.repositories.models import User
from app.services.order_request_service import OrderRequestData, OrderRequestService
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
            "retry sau timeout/lỗi mạng: server trả lại ĐÚNG yêu cầu đã xếp hàng (header "
            "Idempotent-Replayed: true) thay vì xếp thêm yêu cầu mới."
        ),
    ),
]


WaitingRoomPass = Annotated[
    str | None,
    Header(
        alias="X-Waiting-Room-Pass",
        description="Thẻ vào cửa lấy từ `GET /concerts/{id}/waiting-room` (status admitted). Bắt buộc "
                    "trong đợt mở bán có waiting room; ngoài đợt đó thì bỏ qua.",
    ),
]


def request_status_url(request_id: int) -> str:
    return f"/orders/requests/{request_id}"


def _request_response(response: Response, request: OrderRequestData, order=None) -> OrderRequestResponse:
    response.headers["Location"] = request_status_url(request.id)
    if request.status == "pending":
        # Gợi ý client chờ bao lâu trước khi hỏi lại trạng thái.
        response.headers["Retry-After"] = str(config.ORDER_STATUS_RETRY_AFTER_SECONDS)
    return OrderRequestResponse(**asdict(request),
                                order=None if order is None else OrderResponse.model_validate(order))


@router.post(
    "",
    response_model=OrderRequestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Gửi yêu cầu giữ chỗ (bất đồng bộ): xếp hàng rồi hỏi trạng thái ở header `Location`",
    responses={
        202: {"description": "Đã nhận yêu cầu. Hỏi `GET /orders/requests/{id}` (header Location) "
                             "sau `Retry-After` giây cho đến khi status là succeeded/failed."},
        400: {"description": "Chưa mở bán (SaleNotOpen) hoặc concert đã diễn ra (SaleClosed)."},
        403: {"description": "Đang trong đợt mở bán có waiting room mà thiếu/sai pass (WaitingRoomRequired)."},
        409: {"description": "Đã hết vé, hoặc Idempotency-Key đã dùng cho yêu cầu khác."},
        429: {"description": "Gửi quá nhiều yêu cầu đặt mua (RateLimited), xem Retry-After."},
    },
    dependencies=[Depends(limit_per_user("orders"))],
)
def create_order(
    body: CreateOrderRequest,
    response: Response,
    idempotency_key: IdempotencyKey = None,
    waiting_room_pass: WaitingRoomPass = None,
    user: User = Depends(get_current_user),
    svc: OrderRequestService = Depends(get_order_request_service),
):
    request, created = svc.submit(user.id, body.ticket_type_id, body.quantity, idempotency_key,
                                  waiting_room_pass)
    if not created:
        response.headers["Idempotent-Replayed"] = "true"
    return _request_response(response, request)


@router.get(
    "/requests/{request_id}",
    response_model=OrderRequestResponse,
    summary="Trạng thái yêu cầu giữ chỗ: pending → succeeded (kèm đơn) | failed (kèm lỗi)",
    dependencies=[Depends(limit_per_user("order_status"))],
)
def get_order_request(
    request_id: int,
    response: Response,
    user: User = Depends(get_current_user),
    svc: OrderRequestService = Depends(get_order_request_service),
):
    request, order = svc.get(user.id, request_id)
    return _request_response(response, request, order)


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


@router.delete(
    "/{order_id}",
    response_model=OrderResponse,
    summary="Hủy đơn: đơn đang giữ chỗ trả vé về kho; đơn đã thanh toán hoàn tiền",
    responses={409: {"description": "Đơn đã hủy/hết hạn (InvalidState), hoặc đơn đã thanh toán "
                                    "nhưng quá sát giờ diễn để hoàn tiền (RefundWindowClosed)."}},
)
def cancel_order(
    order_id: int,
    user: User = Depends(get_current_user),
    orders: OrderService = Depends(get_order_service),
    payments: PaymentService = Depends(get_payment_service),
):
    # Trạng thái đọc ở đây chỉ để chọn nhánh; mỗi service tự chuyển trạng thái bằng
    # UPDATE có điều kiện nên đọc cũ chỉ dẫn tới InvalidState, không bao giờ hoàn kho hai lần.
    if orders.get_order(user.id, order_id).status == "paid":
        return payments.refund_order(user.id, order_id)
    return orders.cancel_order(user.id, order_id)


@router.post(
    "/{order_id}/pay",
    response_model=PaymentResponse,
    summary="Tạo giao dịch ở cổng thanh toán cho đơn đang giữ chỗ",
    responses={409: {"description": "Đơn không còn ở trạng thái giữ chỗ, hoặc đã quá hạn (HoldExpired)."}},
)
def pay_order(
    order_id: int,
    body: PayRequest | None = None,
    user: User = Depends(get_current_user),
    svc: PaymentService = Depends(get_payment_service),
):
    return svc.start_payment(user.id, order_id, body.provider if body else None)


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
    # Phải là file SVG độc lập (có xmlns): svg_inline() bỏ xmlns nên <img> không vẽ được.
    buffer = io.BytesIO()
    segno.make(ticket.qr_code, error="m").save(buffer, kind="svg", scale=6, border=2, xmldecl=False)
    return Response(content=buffer.getvalue(), media_type="image/svg+xml",
                    headers={"Cache-Control": "private, no-store"})
