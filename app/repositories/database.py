from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.config import DATABASE_URL
from app.repositories.models import Base, Order

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db(bind=None) -> None:
    bind = bind or engine
    Base.metadata.create_all(bind=bind)
    _migrate_order_idempotency_key(bind)


def _migrate_order_idempotency_key(bind) -> None:
    """Nâng cấp DB tạo từ phiên bản cũ: create_all không thêm cột vào bảng đã có."""
    columns = {column["name"] for column in inspect(bind).get_columns("orders")}
    if "idempotency_key" in columns:
        return
    with bind.begin() as connection:
        connection.execute(text("ALTER TABLE orders ADD COLUMN idempotency_key VARCHAR(64)"))
        for index in Order.__table__.indexes:
            index.create(connection, checkfirst=True)
