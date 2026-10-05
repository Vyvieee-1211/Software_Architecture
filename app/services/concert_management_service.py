from app.services.errors import InvalidState, NotFound


class ConcertManagementService:
    def __init__(self, repository):
        self.repo = repository

    def concert(self, concert_id):
        concert = self.repo.get_concert(concert_id)
        if concert is None:
            raise NotFound(f"concert {concert_id}")
        return concert

    def create(self, fields, tickets):
        return self.repo.create_concert(fields, tickets)

    def update(self, concert_id, fields):
        return self.repo.update_concert(self.concert(concert_id), fields)

    def delete(self, concert_id):
        concert = self.concert(concert_id)
        if self.repo.has_orders(concert_id):
            raise InvalidState("Cannot delete a concert with order history")
        self.repo.delete_concert(concert)

    def create_ticket(self, concert_id, fields):
        self.concert(concert_id)
        return self.repo.create_ticket(concert_id, fields)

    def ticket(self, concert_id, ticket_id):
        self.concert(concert_id)
        ticket = self.repo.get_ticket(concert_id, ticket_id)
        if ticket is None:
            raise NotFound(f"ticket type {ticket_id}")
        return ticket

    def update_ticket(self, concert_id, ticket_id, fields):
        ticket = self.ticket(concert_id, ticket_id)
        if ticket.price != fields["price"] and self.repo.has_orders(concert_id, ticket_id):
            raise InvalidState("Cannot change the price of tickets with order history")
        ticket = self.repo.update_ticket(ticket, fields)
        if ticket is None:
            raise InvalidState("Total quantity cannot be lower than reserved tickets")
        return ticket

    def delete_ticket(self, concert_id, ticket_id):
        ticket = self.ticket(concert_id, ticket_id)
        if self.repo.has_orders(concert_id, ticket_id):
            raise InvalidState("Cannot delete a ticket type with order history")
        self.repo.delete_ticket(ticket)
