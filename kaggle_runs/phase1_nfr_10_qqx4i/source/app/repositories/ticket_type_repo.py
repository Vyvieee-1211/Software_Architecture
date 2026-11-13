from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.repositories.models import TicketType


class TicketTypeRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, ticket_type_id: int) -> TicketType | None:
        return self.db.get(TicketType, ticket_type_id)

    def list_by_concert(self, concert_id: int) -> list[TicketType]:
        return list(
            self.db.scalars(select(TicketType).where(TicketType.concert_id == concert_id))
        )

    def reserve(self, ticket_type_id: int, quantity: int) -> bool:
        result = self.db.execute(
            update(TicketType)
            .where(TicketType.id == ticket_type_id, TicketType.remaining >= quantity)
            .values(remaining=TicketType.remaining - quantity)
        )
        return result.rowcount == 1

    def release(self, ticket_type_id: int, quantity: int) -> None:
        """Trả lại vé khi huỷ đơn."""
        self.db.execute(
            update(TicketType)
            .where(TicketType.id == ticket_type_id)
            .values(remaining=TicketType.remaining + quantity)
        )
