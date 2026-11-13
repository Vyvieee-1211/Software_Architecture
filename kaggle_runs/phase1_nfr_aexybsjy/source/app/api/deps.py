"""Dependency của FastAPI: mở session DB, lắp ráp service, xác thực người dùng.

Đây là NƠI DUY NHẤT biết cả FastAPI lẫn SQLAlchemy. Xác thực nằm ở get_current_user
và được gắn cho cả router (xem routers/orders.py) — không viết lặp trong từng endpoint.
"""
from collections.abc import Generator

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import JWT_EXPIRE_MINUTES, JWT_SECRET
from app.repositories.concert_repo import ConcertRepository
from app.repositories.database import SessionLocal
from app.repositories.models import User
from app.repositories.order_repo import OrderRepository
from app.repositories.ticket_type_repo import TicketTypeRepository
from app.repositories.user_repo import UserRepository
from app.services.auth_service import AuthService
from app.services.concert_service import ConcertService
from app.services.errors import InvalidToken
from app.services.order_service import OrderService


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_auth_service(db: Session = Depends(get_db)) -> AuthService:
    return AuthService(UserRepository(db), JWT_SECRET, JWT_EXPIRE_MINUTES)


def get_concert_service(db: Session = Depends(get_db)) -> ConcertService:
    return ConcertService(ConcertRepository(db), TicketTypeRepository(db))


def get_order_service(db: Session = Depends(get_db)) -> OrderService:
    return OrderService(OrderRepository(db), TicketTypeRepository(db), ConcertRepository(db))


_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
) -> User:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    try:
        user_id = auth.verify_token(credentials.credentials)
    except InvalidToken:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    user = UserRepository(db).get_by_id(user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User no longer exists")
    return user
