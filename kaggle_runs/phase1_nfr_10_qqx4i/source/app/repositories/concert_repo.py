from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.repositories.models import Concert


class ConcertRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_all(self) -> list[Concert]:
        return list(self.db.scalars(select(Concert).order_by(Concert.start_time)))

    def list_on_sale(self, now: datetime) -> list[Concert]:
        statement = select(Concert).where(Concert.sale_open_time <= now).order_by(Concert.start_time)
        return list(self.db.scalars(statement))

    def get_by_id(self, concert_id: int) -> Concert | None:
        return self.db.get(Concert, concert_id)
