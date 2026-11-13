from sqlalchemy import select, update

from app.repositories.models import Concert, Order, TicketType


class ConcertManagementRepository:
    def __init__(self, db):
        self.db = db

    def get_concert(self, concert_id):
        # Acquire the database write lock before inspecting orders/inventory.
        self.db.execute(update(Concert).where(Concert.id == concert_id).values(name=Concert.name))
        return self.db.get(Concert, concert_id)

    def create_concert(self, fields, ticket_types):
        concert = Concert(**fields)
        self.db.add(concert)
        self.db.flush()
        for fields in ticket_types:
            self.create_ticket(concert.id, fields)
        return concert

    def create_ticket(self, concert_id, fields):
        ticket = TicketType(concert_id=concert_id, remaining=fields["total_quantity"], **fields)
        self.db.add(ticket)
        self.db.flush()
        return ticket

    def get_ticket(self, concert_id, ticket_id):
        ticket = self.db.get(TicketType, ticket_id)
        return ticket if ticket and ticket.concert_id == concert_id else None

    def has_orders(self, concert_id, ticket_id=None):
        query = select(Order.id).join(TicketType, Order.ticket_type_id == TicketType.id).where(TicketType.concert_id == concert_id)
        if ticket_id is not None:
            query = query.where(TicketType.id == ticket_id)
        return self.db.scalar(query.limit(1)) is not None

    def update_concert(self, concert, fields):
        for name, value in fields.items():
            setattr(concert, name, value)
        self.db.flush()
        return concert

    def update_ticket(self, ticket, fields):
        result = self.db.execute(update(TicketType).where(
            TicketType.id == ticket.id,
            TicketType.total_quantity - TicketType.remaining <= fields["total_quantity"],
        ).values(
            name=fields["name"], price=fields["price"],
            remaining=TicketType.remaining + fields["total_quantity"] - TicketType.total_quantity,
            total_quantity=fields["total_quantity"],
        ))
        self.db.refresh(ticket)
        return ticket if result.rowcount == 1 else None

    def delete_ticket(self, ticket):
        self.db.delete(ticket)
        self.db.flush()

    def delete_concert(self, concert):
        for ticket in list(concert.ticket_types):
            self.db.delete(ticket)
        self.db.flush()
        self.db.delete(concert)
        self.db.flush()
