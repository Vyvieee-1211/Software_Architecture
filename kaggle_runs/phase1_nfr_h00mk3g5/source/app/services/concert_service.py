"""Tầng nghiệp vụ: chỉ đọc"""
from datetime import datetime, timezone

from app.services.errors import NotFound


class ConcertService:
    def __init__(self, concert_repo, ticket_type_repo):
        self.concert_repo = concert_repo
        self.ticket_type_repo = ticket_type_repo

    def list_concerts(self, on_sale: bool = False):
        if on_sale:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            return self.concert_repo.list_on_sale(now)
        return self.concert_repo.list_all()

    def get_concert(self, concert_id: int):
        concert = self.concert_repo.get_by_id(concert_id)
        if concert is None:
            raise NotFound(f"concert {concert_id}")
        return concert

    def list_ticket_types(self, concert_id: int):
        self.get_concert(concert_id)
        return self.ticket_type_repo.list_by_concert(concert_id)
