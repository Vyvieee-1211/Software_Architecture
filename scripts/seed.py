from datetime import datetime, timedelta

import bcrypt

from app.repositories.database import SessionLocal, init_db
from app.repositories.models import Concert, TicketType, User


def seed() -> None:
    init_db()
    db = SessionLocal()
    try:
        if db.query(Concert).count() > 0:
            print("Đã có dữ liệu, bỏ qua seed.")
            return

        now = datetime.utcnow()
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

        for c in concerts:
            db.add_all(
                [
                    TicketType(concert_id=c.id, name="VIP", price=3_000_000, total_quantity=100, remaining=100),
                    TicketType(concert_id=c.id, name="Standard", price=1_500_000, total_quantity=1000, remaining=1000),
                    TicketType(concert_id=c.id, name="GA", price=800_000, total_quantity=5000, remaining=5000),
                ]
            )

        db.add(
            User(
                email="demo@example.com",
                password_hash=bcrypt.hashpw(b"password123", bcrypt.gensalt()).decode(),
                full_name="Demo User",
            )
        )
        db.commit()
        print("Seed xong: 2 concert, 6 hạng vé, user demo@example.com / password123")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
