from datetime import datetime, timedelta, timezone

import bcrypt

from app.repositories.database import SessionLocal, init_db
from app.repositories.models import Concert, TicketType, User
from app.repositories.ticket_repo import TicketRepository


def seed() -> None:
    init_db()
    db = SessionLocal()
    try:
        if db.query(Concert).count() > 0:
            print("Đã có dữ liệu, bỏ qua seed.")
            return

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        concerts = [
            Concert(
                name="Sơn Tùng M-TP – Sky Tour",
                artist="Sơn Tùng M-TP",
                venue="SVĐ Mỹ Đình, Hà Nội",
                start_time=now + timedelta(days=30),
                sale_open_time=now - timedelta(hours=1),
            ),
            Concert(
                name="Hà Anh Tuấn – Chân trời rực rỡ",
                artist="Hà Anh Tuấn",
                venue="Nhà hát Hòa Bình, TP.HCM",
                start_time=now + timedelta(days=60),
                sale_open_time=now + timedelta(days=7),
            ),
        ]
        db.add_all(concerts)
        db.flush()

        tickets = TicketRepository(db)
        for c in concerts:
            for name, price, quantity in (("VIP", 3_000_000, 100), ("Standard", 1_500_000, 1000), ("GA", 800_000, 5000)):
                ticket_type = TicketType(concert_id=c.id, name=name, price=price,
                                         total_quantity=quantity, remaining=quantity)
                db.add(ticket_type)
                db.flush()
                # Mỗi vé là một dòng trong bảng tickets, sinh sẵn theo số lượng của hạng vé.
                tickets.generate(ticket_type.id, quantity)

        db.add(
            User(
                email="demo@example.com",
                password_hash=bcrypt.hashpw(b"password123", bcrypt.gensalt()).decode(),
                full_name="Demo User",
            )
        )
        db.commit()
        print("Seed xong: 2 concert, 6 hạng vé, 12.200 vé, user demo@example.com / password123")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
