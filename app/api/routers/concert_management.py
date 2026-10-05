from fastapi import APIRouter, Depends, Response, status

from app.api.deps import get_admin_user, get_db
from app.api.schemas import ConcertInput, ConcertResponse, CreateConcertInput, TicketTypeInput, TicketTypeResponse
from app.repositories.concert_management_repo import ConcertManagementRepository
from app.services.concert_management_service import ConcertManagementService

router = APIRouter(prefix="/concerts", tags=["concert management"], dependencies=[Depends(get_admin_user)])


def get_management_service(db=Depends(get_db)):
    return ConcertManagementService(ConcertManagementRepository(db))


@router.post("", response_model=ConcertResponse, status_code=status.HTTP_201_CREATED)
def create_concert(body: CreateConcertInput, svc=Depends(get_management_service)):
    return svc.create(body.model_dump(exclude={"ticket_types"}), [ticket.model_dump() for ticket in body.ticket_types])


@router.put("/{concert_id}", response_model=ConcertResponse)
def update_concert(concert_id: int, body: ConcertInput, svc=Depends(get_management_service)):
    return svc.update(concert_id, body.model_dump())


@router.delete("/{concert_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_concert(concert_id: int, svc=Depends(get_management_service)):
    svc.delete(concert_id)
    return Response(status_code=204)


@router.post("/{concert_id}/ticket-types", response_model=TicketTypeResponse, status_code=status.HTTP_201_CREATED)
def create_ticket(concert_id: int, body: TicketTypeInput, svc=Depends(get_management_service)):
    return svc.create_ticket(concert_id, body.model_dump())


@router.put("/{concert_id}/ticket-types/{ticket_id}", response_model=TicketTypeResponse)
def update_ticket(concert_id: int, ticket_id: int, body: TicketTypeInput, svc=Depends(get_management_service)):
    return svc.update_ticket(concert_id, ticket_id, body.model_dump())


@router.delete("/{concert_id}/ticket-types/{ticket_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ticket(concert_id: int, ticket_id: int, svc=Depends(get_management_service)):
    svc.delete_ticket(concert_id, ticket_id)
    return Response(status_code=204)
