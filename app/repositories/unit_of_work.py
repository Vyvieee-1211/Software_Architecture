"""One session per use case. Writes require an explicit service-level commit."""
from app.repositories.concert_repo import ConcertRepository
from app.repositories.order_repo import OrderRepository
from app.repositories.ticket_type_repo import TicketTypeRepository
from sqlalchemy.exc import SQLAlchemyError

from app.services.infrastructure_errors import DatabaseUnavailable


class SqlAlchemyUnitOfWork:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def __enter__(self):
        self.session = self.session_factory()
        self.orders = OrderRepository(self.session)
        self.ticket_types = TicketTypeRepository(self.session)
        self.concerts = ConcertRepository(self.session)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            try:
                # After a successful commit rollback is a no-op.
                self.rollback()
            finally:
                self.session.close()
        except SQLAlchemyError as cleanup_error:
            # A failed rollback/close must never qualify for a transaction replay.
            raise DatabaseUnavailable() from cleanup_error

    def commit(self):
        self.session.commit()

    def rollback(self):
        self.session.rollback()
