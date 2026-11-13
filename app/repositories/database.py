from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.engine import make_url

from app import config
from app.repositories.models import Base

def build_engine(url):
    """Configure bounded pool and lock waits; preserve SQLite in-memory test support."""
    parsed = make_url(url)
    options = {"pool_pre_ping": True}
    sqlite = parsed.get_backend_name() == "sqlite"
    if sqlite:
        options["connect_args"] = {
            "check_same_thread": False,
            "timeout": config.DB_SQLITE_BUSY_TIMEOUT_SECONDS,
        }
    elif parsed.get_backend_name() == "postgresql":
        # Giới hạn chờ khóa dòng và thời gian chạy một câu lệnh, để request không treo vô hạn.
        settings = {"lock_timeout": config.DB_PG_LOCK_TIMEOUT_MS,
                    "statement_timeout": config.DB_PG_STATEMENT_TIMEOUT_MS}
        options["connect_args"] = {
            "options": " ".join(f"-c {name}={value}" for name, value in settings.items() if value)
        }
    # SQLite memory databases use SingletonThreadPool, which has no pool_timeout.
    if not sqlite or (parsed.database and parsed.database != ":memory:"):
        options.update(
            pool_size=config.DB_POOL_SIZE,
            max_overflow=config.DB_MAX_OVERFLOW,
            pool_timeout=config.DB_POOL_TIMEOUT_SECONDS,
        )
    return create_engine(url, **options)


engine = build_engine(config.DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db(bind=None) -> None:
    """Tạo các bảng còn thiếu. create_all KHÔNG sửa bảng đã có: DB tạo từ lược đồ cũ
    (trước khi có giữ chỗ/thanh toán) phải được tạo lại từ đầu."""
    Base.metadata.create_all(bind=bind or engine)
