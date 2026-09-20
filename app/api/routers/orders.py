"""Router cần đăng nhập. Xác thực gắn MỘT LẦN ở dependencies của router:
mọi endpoint trong file này tự động yêu cầu Bearer token."""
from fastapi import APIRouter, Depends, status

from app.api.deps import get_current_user, get_order_service
from app.api.schemas import CreateOrderRequest, OrderResponse
from app.repositories.models import User
from app.services.order_service import OrderService

router = APIRouter(
    prefix="/orders",
    tags=["orders"],
    dependencies=[Depends(get_current_user)],
)


@router.post("", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
def create_order(
    body: CreateOrderRequest,
    user: User = Depends(get_current_user),
    svc: OrderService = Depends(get_order_service),
):
    return svc.create_order(user.id, body.ticket_type_id, body.quantity)


@router.get("/me", response_model=list[OrderResponse])
def my_orders(user: User = Depends(get_current_user), svc: OrderService = Depends(get_order_service)):
    return svc.list_my_orders(user.id)


@router.delete("/{order_id}", response_model=OrderResponse)
def cancel_order(
    order_id: int,
    user: User = Depends(get_current_user),
    svc: OrderService = Depends(get_order_service),
):
    return svc.cancel_order(user.id, order_id)
