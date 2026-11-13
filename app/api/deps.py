"""Dependency của FastAPI: mở session DB, lắp ráp service, xác thực người dùng.

Đây là NƠI DUY NHẤT biết cả FastAPI lẫn SQLAlchemy. Xác thực nằm ở get_current_user
và được gắn cho cả router (xem routers/orders.py) — không viết lặp trong từng endpoint.
"""
from collections.abc import Generator

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import ADMIN_EMAILS, JWT_EXPIRE_MINUTES, JWT_SECRET
from app.repositories.concert_repo import ConcertRepository
from app.repositories.database import SessionLocal
from app.repositories.models import User
from app.repositories.unit_of_work import SqlAlchemyUnitOfWork
from app.repositories.resilience import database_policy
from app.repositories.ticket_type_repo import TicketTypeRepository
from app.repositories.user_repo import UserRepository
from app.services.auth_service import AuthService
from app.services.concert_service import ConcertService
from app.services.errors import InvalidToken
from app.services.order_service import OrderService


def get_db() -> Generator[Session, None, None]:
    """Session cho tài khoản/tra cứu; nghiệp vụ đơn hàng dùng UoW riêng."""
    # Commit/cleanup must complete before sending the response.
    # A yielded endpoint cannot be replayed here, so use protection only.
    with database_policy.protect():
        with SessionLocal() as db:
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise


def get_auth_service(db: Session = Depends(get_db, scope="function")) -> AuthService:
    return AuthService(UserRepository(db), JWT_SECRET, JWT_EXPIRE_MINUTES)


def get_concert_service(db: Session = Depends(get_db, scope="function")) -> ConcertService:
    return ConcertService(ConcertRepository(db), TicketTypeRepository(db))


def get_order_service() -> OrderService:
    return OrderService(lambda: SqlAlchemyUnitOfWork(SessionLocal), execute=database_policy.run)


_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> User:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    # Verify JWT before accessing DB; invalid tokens must still return 401.
    try:
        user_id = AuthService(None, JWT_SECRET, JWT_EXPIRE_MINUTES).verify_token(credentials.credentials)
    except InvalidToken:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")

    def load_user():
        # Kết thúc session xác thực trước khi bắt đầu UoW nghiệp vụ.
        with SessionLocal() as db:
            user = UserRepository(db).get_by_id(user_id)
            if user is None:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User no longer exists")
            return user

    return database_policy.run(load_user)


def get_admin_user(user: User = Depends(get_current_user)) -> User:
    if user.email.lower() not in ADMIN_EMAILS:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator access required")
    return user
