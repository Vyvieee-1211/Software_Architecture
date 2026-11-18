"""Tạo concert/hạng vé riêng cho mỗi lượt đo, không xóa dữ liệu sẵn có."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid
import json


def prepare(url, capacity, users=0, accounts_path=None):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.repositories.models import Base, Concert, Ticket, TicketType, User
    engine = create_engine(url)
    try:
        Base.metadata.create_all(engine)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with Session(engine) as db:
            concert = Concert(name=f"Locust {uuid.uuid4().hex[:10]}", artist="Load test",
                              venue="Test", start_time=now + timedelta(days=30),
                              sale_open_time=now - timedelta(hours=1))
            db.add(concert)
            db.flush()
            ticket_type = TicketType(concert_id=concert.id, name="Loadtest", price=Decimal("100000"),
                                     total_quantity=capacity, remaining=capacity)
            db.add(ticket_type)
            db.flush()
            db.add_all(Ticket(ticket_type_id=ticket_type.id, seat_no=str(i), status="available")
                       for i in range(1, capacity + 1))
            emails = []
            if users:
                import bcrypt
                # Tài khoản thử cùng mật khẩu; hash chuẩn được tạo trước khi đo.
                password_hash = bcrypt.hashpw(b"secret123", bcrypt.gensalt()).decode()
                prefix = uuid.uuid4().hex[:12]
                emails = [f"load-{prefix}-{i}@example.com" for i in range(users)]
                db.add_all(User(email=email, password_hash=password_hash) for email in emails)
            db.commit()
            result = {"CONCERT_ID": str(concert.id), "TICKET_TYPE_ID": str(ticket_type.id)}
            if accounts_path:
                accounts_path.write_text(json.dumps(emails), encoding="utf-8")
                result["AUTH_ACCOUNTS_FILE"] = str(accounts_path)
            return result
    finally:
        engine.dispose()
