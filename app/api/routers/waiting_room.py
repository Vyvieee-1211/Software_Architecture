from dataclasses import asdict

from fastapi import APIRouter, Depends, Response

from app.api.deps import get_current_user, get_waiting_room_service, limit_per_user
from app.api.schemas import WaitingRoomResponse
from app.repositories.models import User
from app.services.waiting_room_service import WaitingRoomService, WaitingRoomStatus

router = APIRouter(prefix="/concerts", tags=["waiting room"],
                   dependencies=[Depends(limit_per_user("waiting_room"))])


def _respond(response: Response, result: WaitingRoomStatus) -> WaitingRoomResponse:
    if result.retry_after_seconds is not None:
        # Gợi ý client chờ bao lâu rồi mới hỏi lại vị trí.
        response.headers["Retry-After"] = str(result.retry_after_seconds)
    data = asdict(result)
    data.pop("retry_after_seconds")
    return WaitingRoomResponse(**data)


@router.post("/{concert_id}/waiting-room", response_model=WaitingRoomResponse,
             summary="Vào hàng chờ mua vé của concert (gọi lại vẫn giữ chỗ cũ)")
def join_waiting_room(concert_id: int, response: Response, user: User = Depends(get_current_user),
                      svc: WaitingRoomService = Depends(get_waiting_room_service)):
    return _respond(response, svc.join(user.id, concert_id))


@router.get("/{concert_id}/waiting-room", response_model=WaitingRoomResponse,
            summary="Vị trí hiện tại trong hàng chờ; tới lượt thì kèm pass để đặt mua")
def get_waiting_room(concert_id: int, response: Response, user: User = Depends(get_current_user),
                     svc: WaitingRoomService = Depends(get_waiting_room_service)):
    return _respond(response, svc.status(user.id, concert_id))
