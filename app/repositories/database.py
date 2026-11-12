from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.engine import make_url

from app import config
from app.repositories.models import Base

def build_engine(url):
    """Configure bounded pool/SQLite lock waits; preserve in-memory test support."""
    parsed = make_url(url)
    options = {"pool_pre_ping": True}
    sqlite = parsed.get_backend_name() == "sqlite"
    if sqlite:
        options["connect_args"] = {
            "check_same_thread": False,
            "timeout": config.DB_SQLITE_BUSY_TIMEOUT_SECONDS,
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


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
