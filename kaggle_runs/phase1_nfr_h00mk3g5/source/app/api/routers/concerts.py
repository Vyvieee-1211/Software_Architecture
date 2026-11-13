from fastapi import APIRouter, Depends

from app.api.deps import get_concert_service
from app.api.schemas import ConcertResponse, TicketTypeResponse
from app.services.concert_service import ConcertService

router = APIRouter(prefix="/concerts", tags=["concerts"])

"""Không cần đăng nhập để xem danh sách concert hiện có"""

@router.get("", response_model=list[ConcertResponse])
def list_concerts(on_sale: bool = False, svc: ConcertService = Depends(get_concert_service)):
    return svc.list_concerts(on_sale=on_sale)


@router.get("/{concert_id}", response_model=ConcertResponse)
def get_concert(concert_id: int, svc: ConcertService = Depends(get_concert_service)):
    return svc.get_concert(concert_id)


@router.get("/{concert_id}/ticket-types", response_model=list[TicketTypeResponse])
def list_ticket_types(concert_id: int, svc: ConcertService = Depends(get_concert_service)):
    return svc.list_ticket_types(concert_id)
