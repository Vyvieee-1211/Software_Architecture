"""Điểm vào của ứng dụng: tạo FastAPI app, gắn router, dịch lỗi nghiệp vụ → HTTP."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routers import auth, concerts, orders
from app.repositories.database import init_db
from app.services import errors


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Concert Ticketing API",
    version="0.1.0",
    description="Pha 1 — backend đặt vé concert, kiến trúc phân tầng.",
    lifespan=lifespan,
)

app.include_router(auth.router)
app.include_router(concerts.router)
app.include_router(orders.router)


ERROR_STATUS = {
    errors.NotFound: 404,
    errors.EmailAlreadyExists: 409,
    errors.InvalidCredentials: 401,
    errors.InvalidToken: 401,
    errors.SaleNotOpen: 400,
    errors.SoldOut: 409,
    errors.Forbidden: 403,
    errors.InvalidState: 409,
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


app.mount(
    "/",
    StaticFiles(directory=Path(__file__).resolve().parent.parent / "frontend", html=True),
    name="frontend",
)
