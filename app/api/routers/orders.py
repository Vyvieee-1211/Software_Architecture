from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response, status

from app.api.deps import get_current_user, get_order_service
from app.api.schemas import CreateOrderRequest, OrderResponse
from app.repositories.models import User
from app.services.order_service import OrderService

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
    responses={
        200: {"model": OrderResponse, "description": "Retry cùng Idempotency-Key: trả lại đơn đã tạo."},
        409: {"description": "Hết vé, hoặc Idempotency-Key đã dùng cho một yêu cầu khác."},
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


@router.delete("/{order_id}", response_model=OrderResponse)
def cancel_order(
    order_id: int,
    user: User = Depends(get_current_user),
    svc: OrderService = Depends(get_order_service),
):
    return svc.cancel_order(user.id, order_id)
