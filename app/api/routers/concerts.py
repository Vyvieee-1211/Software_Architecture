from fastapi import APIRouter, Depends

from app.api.deps import get_concert_service
from app.api.schemas import ConcertResponse, TicketTypeResponse
from app.services.concert_service import ConcertService

router = APIRouter(prefix="/concerts", tags=["concerts"])


@router.get("", response_model=list[ConcertResponse])
def list_concerts(svc: ConcertService = Depends(get_concert_service)):
    return svc.list_concerts()


@router.get("/{concert_id}/ticket-types", response_model=list[TicketTypeResponse])
def list_ticket_types(concert_id: int, svc: ConcertService = Depends(get_concert_service)):
    return svc.list_ticket_types(concert_id)
