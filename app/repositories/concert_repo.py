from sqlalchemy import select
from sqlalchemy.orm import Session

from app.repositories.models import Concert


class ConcertRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_all(self) -> list[Concert]:
        return list(self.db.scalars(select(Concert).order_by(Concert.start_time)))

    def get_by_id(self, concert_id: int) -> Concert | None:
        return self.db.get(Concert, concert_id)

    def create(
        self,
        name: str,
        artist: str | None,
        venue: str | None,
        start_time,
        sale_open_time,
    ) -> Concert:
        concert = Concert(
            name=name,
            artist=artist,
            venue=venue,
            start_time=start_time,
            sale_open_time=sale_open_time,
        )

        self.db.add(concert)
        self.db.flush()
        self.db.refresh(concert)

        return concert

    def delete(self, concert: Concert) -> None:
        self.db.delete(concert)
        self.db.flush()