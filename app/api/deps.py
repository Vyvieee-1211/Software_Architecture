"""Dependency của FastAPI: mở session DB, lắp ráp service, xác thực người dùng.

Đây là NƠI DUY NHẤT biết cả FastAPI lẫn SQLAlchemy. Xác thực nằm ở get_current_user
và được gắn cho cả router (xem routers/orders.py) — không viết lặp trong từng endpoint.
"""
from collections.abc import Generator

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import event
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import QueuePool

from app import config
from app.config import ADMIN_EMAILS, JWT_EXPIRE_MINUTES, JWT_SECRET
from app.api.rate_limit import RateLimiter
from app.integrations import build_payment_gateway
from app.observability import log_domain_event
from app.order_worker import wakeup as order_queue_wakeup
from app.repositories.database import SessionLocal, check_database
from app.repositories.models import User
from app.repositories.unit_of_work import SqlAlchemyUnitOfWork
from app.repositories.resilience import database_policy
from app.repositories.user_repo import UserRepository
from app.services.auth_service import AuthService
from app.services.concert_service import ConcertService
from app.services.domain_events import DomainEvents
from app.services.errors import InvalidToken
from app.services.infrastructure_errors import DatabaseUnavailable
from app.services.order_request_service import OrderRequestService
from app.services.order_service import OrderService
from app.services.payment_gateway import PaymentGateway
from app.services.payment_service import PaymentService
from app.services.read_cache import ReadThroughCache
from app.services.ticket_codes import TicketCodes
from app.services.waiting_room_service import WaitingRoomService

domain_events = DomainEvents(log_domain_event)


def check_readiness() -> None:
    try:
        with database_policy.protect():
            check_database(SessionLocal)
    except DBAPIError as exc:
        raise DatabaseUnavailable() from exc


def get_database_metrics() -> dict:
    pool = SessionLocal.kw["bind"].pool
    metrics = {
        "db_pool_checked_out": pool.checkedout() if isinstance(pool, QueuePool) else None,
        "order_queue_pending": None,
    }
    # Việc lấy mẫu không chiếm lượt thử của circuit breaker và không tính là lỗi nghiệp vụ.
    if database_policy.breaker.is_closed():
        try:
            with _uow() as uow:
                metrics["order_queue_pending"] = uow.order_requests.count_pending()
        except (DatabaseUnavailable, SQLAlchemyError):
            pass
    return metrics


def get_db() -> Generator[Session, None, None]:
    """Session cho tài khoản và quản lý concert."""
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


def _uow() -> SqlAlchemyUnitOfWork:
    return SqlAlchemyUnitOfWork(SessionLocal)


# Cache danh sách concert/hạng vé dùng chung cho mọi request của tiến trình (Cache-Aside).
catalog_cache = ReadThroughCache(config.CATALOG_CACHE_TTL_SECONDS)


def get_concert_service() -> ConcertService:
    return ConcertService(_uow, protect=database_policy.protect, cache=catalog_cache)


def get_catalog_admin_db(db: Session = Depends(get_db, scope="function")) -> Session:
    """Session cho quản lý concert: xóa cache danh sách SAU KHI thay đổi đã commit.

    Xóa trước commit thì một request đọc chen giữa có thể nạp lại dữ liệu cũ vào cache.
    """
    event.listen(db, "after_commit", lambda _session: catalog_cache.invalidate())
    return db


def get_order_service() -> OrderService:
    return OrderService(
        _uow, execute=database_policy.run,
        hold_minutes=config.HOLD_MINUTES,
        max_per_order=config.MAX_TICKETS_PER_ORDER,
        max_held_per_user=config.MAX_HELD_TICKETS_PER_USER,
        events=domain_events,
    )


def get_waiting_room_service() -> WaitingRoomService:
    return WaitingRoomService(
        _uow, config.WAITING_ROOM_SECRET, execute=database_policy.run,
        window_minutes=config.WAITING_ROOM_MINUTES,
        lead_minutes=config.WAITING_ROOM_LEAD_MINUTES,
        initial_batch=config.WAITING_ROOM_INITIAL_BATCH,
        admit_per_minute=config.WAITING_ROOM_ADMIT_PER_MINUTE,
        pass_minutes=config.WAITING_ROOM_PASS_MINUTES,
    )


def get_order_request_service() -> OrderRequestService:
    return OrderRequestService(
        _uow, get_order_service(), execute=database_policy.run,
        max_attempts=config.ORDER_REQUEST_MAX_ATTEMPTS,
        on_submitted=order_queue_wakeup.set,
        waiting_room=get_waiting_room_service(),
    )


# Adapter cổng thanh toán và bộ ký mã QR không có trạng thái theo request: tạo một lần.
payment_gateway = build_payment_gateway()
ticket_codes = TicketCodes(config.TICKET_QR_SECRET)


def get_payment_gateway() -> PaymentGateway:
    return payment_gateway


def get_payment_service(gateway: PaymentGateway = Depends(get_payment_gateway)) -> PaymentService:
    return PaymentService(_uow, gateway, ticket_codes.issue, execute=database_policy.run,
                          refund_cutoff_hours=config.REFUND_CUTOFF_HOURS, events=domain_events)


_bearer = HTTPBearer(auto_error=False)


def current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> int:
    """Chỉ kiểm tra JWT, không chạm DB (rate limit theo user dùng cái này)."""
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    try:
        return AuthService(None, JWT_SECRET, JWT_EXPIRE_MINUTES).verify_token(credentials.credentials)
    except InvalidToken:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")


def get_current_user(user_id: int = Depends(current_user_id)) -> User:
    # JWT đã được kiểm tra trước khi chạm DB; token sai vẫn trả 401.

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


# --- Rate limit (token bucket trong bộ nhớ, mỗi tiến trình một bộ) --------------
rate_limiters = {
    "auth": RateLimiter(config.RATE_LIMIT_AUTH_PER_MINUTE),
    "orders": RateLimiter(config.RATE_LIMIT_ORDERS_PER_MINUTE),
    "order_status": RateLimiter(config.RATE_LIMIT_ORDER_STATUS_PER_MINUTE),
    "waiting_room": RateLimiter(config.RATE_LIMIT_WAITING_ROOM_PER_MINUTE),
}


def limit_per_user(name: str):
    """Dependency: mỗi user tối đa N request/phút cho nhóm endpoint `name`."""
    def dependency(user_id: int = Depends(current_user_id)) -> None:
        rate_limiters[name].hit(f"user:{user_id}")
    return dependency


def limit_per_ip(name: str):
    """Dependency: mỗi IP tối đa N request/phút (endpoint chưa đăng nhập: login, register)."""
    def dependency(request: Request) -> None:
        rate_limiters[name].hit(f"ip:{request.client.host if request.client else 'unknown'}")
    return dependency
