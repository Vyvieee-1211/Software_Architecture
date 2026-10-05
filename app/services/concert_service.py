from app.services.errors import NotFound, InvalidState


class ConcertService:
    def __init__(self, concert_repo, ticket_type_repo):
        self.concert_repo = concert_repo
        self.ticket_type_repo = ticket_type_repo

    def list_concerts(self):
        return self.concert_repo.list_all()

    def list_ticket_types(self, concert_id: int):
        if self.concert_repo.get_by_id(concert_id) is None:
            raise NotFound(f"concert {concert_id}")

        return self.ticket_type_repo.list_by_concert(concert_id)

    def create_concert(
        self,
        name,
        artist,
        venue,
        start_time,
        sale_open_time,
    ):
        if sale_open_time >= start_time:
            raise InvalidState(
                "sale_open_time must be before start_time"
            )

        return self.concert_repo.create(
            name=name,
            artist=artist,
            venue=venue,
            start_time=start_time,
            sale_open_time=sale_open_time,
        )

    def delete_concert(self, concert_id: int):
        concert = self.concert_repo.get_by_id(concert_id)

        if concert is None:
            raise NotFound(f"concert {concert_id}")

        self.concert_repo.delete(concert)