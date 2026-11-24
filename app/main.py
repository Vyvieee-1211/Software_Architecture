"""Điểm vào của ứng dụng: tạo FastAPI app, gắn router, dịch lỗi nghiệp vụ → HTTP."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import config
from app.api.deps import get_order_request_service, get_order_service
from app.api.load_shedding import LoadShedder
from app.api.rate_limit import RateLimitExceeded
from app.api.routers import auth, concerts, orders, concert_management, mock_gateway, payments, waiting_room
from app.order_worker import OrderRequestWorker
from app.repositories.database import init_db
from app.services import errors
from app.services.infrastructure_errors import DatabaseUnavailable
from app.sweeper import HoldSweeper


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    background = []
    if config.HOLD_SWEEP_INTERVAL_SECONDS > 0:
        background.append(HoldSweeper(lambda: get_order_service().expire_holds(),
                                      config.HOLD_SWEEP_INTERVAL_SECONDS))
    if config.ORDER_WORKERS > 0:
        background.append(OrderRequestWorker(lambda: get_order_request_service().process_next(),
                                             config.ORDER_WORKERS, config.ORDER_WORKER_POLL_SECONDS))
    for task in background:
        task.start()
    yield
    for task in background:
        task.stop()


app = FastAPI(
    title="Concert Ticketing API",
    version="0.2.0",
    description="Backend đặt vé concert: giữ chỗ có hạn, thanh toán qua cổng bên thứ ba, xuất vé QR.",
    lifespan=lifespan,
)

app.include_router(auth.router)
app.include_router(concerts.router)
app.include_router(waiting_room.router)
app.include_router(orders.router)
app.include_router(concert_management.router)
app.include_router(payments.router)
if config.PAYMENT_PROVIDER == "mock":
    app.include_router(mock_gateway.router)


ERROR_STATUS = {
    errors.NotFound: 404,
    errors.EmailAlreadyExists: 409,
    errors.InvalidCredentials: 401,
    errors.InvalidToken: 401,
    errors.SaleNotOpen: 400,
    errors.SoldOut: 409,
    errors.Forbidden: 403,
    errors.InvalidState: 409,
    errors.IdempotencyKeyReused: 409,
    errors.SaleClosed: 400,
    errors.HoldLimitExceeded: 409,
    errors.HoldExpired: 409,
    errors.InvalidWebhookSignature: 401,
    errors.PaymentGatewayError: 502,
    errors.RefundWindowClosed: 409,
    errors.WaitingRoomRequired: 403,
}


@app.exception_handler(errors.BusinessError)
def business_error_handler(request: Request, exc: errors.BusinessError):
    status_code = ERROR_STATUS.get(type(exc), 400)
    return JSONResponse(
        status_code=status_code,
        content={"error": type(exc).__name__, "detail": str(exc)},
    )


@app.get("/health", tags=["ops"])
def health():
    return {"status": "ok"}


@app.exception_handler(RateLimitExceeded)
def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"error": "RateLimited", "detail": "Too many requests, please retry later"},
        headers={"Retry-After": str(exc.retry_after)},
    )


@app.exception_handler(DatabaseUnavailable)
def database_error_handler(request: Request, exc: DatabaseUnavailable):
    return JSONResponse(
        status_code=503,
        content={"error": "DatabaseUnavailable", "detail": str(exc)},
        headers={"Retry-After": str(exc.retry_after)},
    )


# Load shedding bọc ngoài cùng: từ chối request thừa trước khi nó tốn tài nguyên nào khác.
app.add_middleware(LoadShedder, max_in_flight=config.MAX_IN_FLIGHT_REQUESTS,
                   retry_after=config.OVERLOAD_RETRY_AFTER_SECONDS)

# Mount frontend sau cùng để không che các route API.
app.mount(
    "/",
    StaticFiles(directory=Path(__file__).resolve().parent.parent / "frontend", html=True),
    name="frontend",
)
