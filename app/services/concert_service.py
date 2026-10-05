"""Tầng nghiệp vụ: chỉ đọc"""
from app.services.errors import NotFound


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
