from fastapi import APIRouter, Depends, status

from app.api.deps import (
    get_concert_service,
    get_current_user,
)
from app.api.schemas import (
    ConcertResponse,
    CreateConcertRequest,
    TicketTypeResponse,
)
from app.repositories.models import User
from app.services.concert_service import ConcertService

router = APIRouter(
    prefix="/concerts",
    tags=["concerts"],
)


@router.get("", response_model=list[ConcertResponse])
def list_concerts(
    svc: ConcertService = Depends(get_concert_service),
):
    return svc.list_concerts()


@router.post(
    "",
    response_model=ConcertResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_concert(
    body: CreateConcertRequest,
    user: User = Depends(get_current_user),
    svc: ConcertService = Depends(get_concert_service),
):
    return svc.create_concert(
        name=body.name,
        artist=body.artist,
        venue=body.venue,
        start_time=body.start_time,
        sale_open_time=body.sale_open_time,
    )


@router.get(
    "/{concert_id}/ticket-types",
    response_model=list[TicketTypeResponse],
)
def list_ticket_types(
    concert_id: int,
    svc: ConcertService = Depends(get_concert_service),
):
    return svc.list_ticket_types(concert_id)


@router.delete(
    "/{concert_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_concert(
    concert_id: int,
    user: User = Depends(get_current_user),
    svc: ConcertService = Depends(get_concert_service),
):
    svc.delete_concert(concert_id)
