import os
import math


def _number(name, default, convert, minimum):
    value = convert(os.getenv(name, str(default)))
    if not math.isfinite(value) or value < minimum:
        raise ValueError(f"{name} must be finite and >= {minimum}")
    return value

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./concert.db")
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
