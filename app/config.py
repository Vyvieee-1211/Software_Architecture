import os
import math


def _number(name, default, convert, minimum):
    value = convert(os.getenv(name, str(default)))
    if not math.isfinite(value) or value < minimum:
        raise ValueError(f"{name} must be finite and >= {minimum}")
    return value

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+psycopg://concert:concert@localhost:5432/concert"
)
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me-please-use-32-bytes-or-more")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))

# Waiting limits, not an end-to-end HTTP request deadline.
DB_POOL_SIZE = _number("DB_POOL_SIZE", 5, int, 1)
DB_MAX_OVERFLOW = _number("DB_MAX_OVERFLOW", 10, int, 0)
DB_POOL_TIMEOUT_SECONDS = _number("DB_POOL_TIMEOUT_SECONDS", 2, float, 0.01)
DB_SQLITE_BUSY_TIMEOUT_SECONDS = _number("DB_SQLITE_BUSY_TIMEOUT_SECONDS", 1, float, 0.01)
DB_MAX_ATTEMPTS = _number("DB_MAX_ATTEMPTS", 3, int, 1)
DB_RETRY_BASE_SECONDS = _number("DB_RETRY_BASE_SECONDS", 0.05, float, 0)
DB_RETRY_MAX_SECONDS = _number("DB_RETRY_MAX_SECONDS", 0.5, float, 0)
DB_BREAKER_FAILURE_THRESHOLD = _number("DB_BREAKER_FAILURE_THRESHOLD", 5, int, 1)
DB_BREAKER_WINDOW_SECONDS = _number("DB_BREAKER_WINDOW_SECONDS", 30, float, 0.01)
DB_BREAKER_RECOVERY_SECONDS = _number("DB_BREAKER_RECOVERY_SECONDS", 10, float, 0.01)

ADMIN_EMAILS = frozenset(email.strip().lower() for email in os.getenv("ADMIN_EMAILS", "demo@example.com").split(",") if email.strip())

# --- Giữ chỗ / thanh toán -------------------------------------------------
# Thời gian giữ vé chờ thanh toán; quá hạn thì đơn hết hạn và vé được trả về kho.
HOLD_MINUTES = _number("HOLD_MINUTES", 12, float, 0.01)
MAX_TICKETS_PER_ORDER = _number("MAX_TICKETS_PER_ORDER", 10, int, 1)
# Tổng số vé một người được giữ tạm (chưa thanh toán) cùng lúc.
MAX_HELD_TICKETS_PER_USER = _number("MAX_HELD_TICKETS_PER_USER", 10, int, 1)
# Chu kỳ quét đơn giữ chỗ quá hạn; 0 = tắt (vd. khi chạy test hoặc có worker riêng).
HOLD_SWEEP_INTERVAL_SECONDS = _number("HOLD_SWEEP_INTERVAL_SECONDS", 5, float, 0)

PAYMENT_PROVIDER = os.getenv("PAYMENT_PROVIDER", "mock")
# Khóa bí mật dùng chung với cổng thanh toán để ký/kiểm tra webhook (HMAC-SHA256).
PAYMENT_WEBHOOK_SECRET = os.getenv("PAYMENT_WEBHOOK_SECRET", "dev-payment-secret-change-me")
# Khóa ký mã QR của vé.
TICKET_QR_SECRET = os.getenv("TICKET_QR_SECRET", "dev-ticket-qr-secret-change-me")
# URL công khai của hệ thống, dùng để tạo đường dẫn thanh toán giả lập.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")

# PostgreSQL: giới hạn chờ khóa và thời gian chạy một câu lệnh (mili giây); 0 = không giới hạn.
DB_PG_LOCK_TIMEOUT_MS = _number("DB_PG_LOCK_TIMEOUT_MS", 3000, int, 0)
DB_PG_STATEMENT_TIMEOUT_MS = _number("DB_PG_STATEMENT_TIMEOUT_MS", 15000, int, 0)
