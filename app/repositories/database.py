"""Kết nối DB: engine + SessionLocal. Chỉ tầng API (deps.py) và scripts mới dùng file này."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import DATABASE_URL
from app.repositories.models import Base

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    """Tạo bảng nếu chưa có. Pha 1 dùng create_all cho đơn giản (chưa cần Alembic)."""
    Base.metadata.create_all(bind=engine)
