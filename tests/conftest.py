"""Cấu hình chung cho test. Phải đặt biến môi trường TRƯỚC khi import `app`."""
import os

# Test chức năng chạy trên SQLite trong bộ nhớ; test đồng thời trên PostgreSQL thật
# nằm ở tests/test_postgres_concurrency.py và tự bỏ qua khi không có TEST_DATABASE_URL.
os.environ["DATABASE_URL"] = "sqlite://"
# Không chạy luồng quét nền trong test: test tự gọi expire_holds() khi cần.
os.environ["HOLD_SWEEP_INTERVAL_SECONDS"] = "0"

from app.repositories.models import TicketType  # noqa: E402
from app.repositories.ticket_repo import TicketRepository  # noqa: E402


def add_ticket_type(db, **fields) -> TicketType:
    """Tạo hạng vé kèm các dòng vé sinh sẵn, như hệ thống thật làm khi tạo hạng vé."""
    ticket_type = TicketType(remaining=fields["total_quantity"], **fields)
    db.add(ticket_type)
    db.flush()
    TicketRepository(db).generate(ticket_type.id, ticket_type.total_quantity)
    return ticket_type
