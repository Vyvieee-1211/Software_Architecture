"""DB failure handling outside business logic. Never replay uncertain commits."""
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
import logging
import math
import random
import sqlite3
from threading import Lock
import time

from sqlalchemy.exc import DBAPIError, TimeoutError
from sqlalchemy.orm.exc import StaleDataError

from app import config
from app.services.errors import BusinessError
from app.services.infrastructure_errors import CircuitOpen, DatabaseUnavailable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Failure:
    kind: str
    retryable: bool = False


def classify_failure(exc):
    if isinstance(exc, TimeoutError):
        return Failure("pool_timeout", retryable=True)
    if isinstance(exc, DatabaseUnavailable):
        return Failure("cleanup_failure")
    if isinstance(exc, StaleDataError):
        # Khóa lạc quan (tickets.version): dòng đã bị giao dịch khác sửa. Chưa có gì
        # được commit nên chạy lại cả nghiệp vụ là an toàn.
        return Failure("stale_version", retryable=True)
    if not isinstance(exc, DBAPIError):
        return None
    if exc.connection_invalidated:
        return Failure("disconnect")
    if isinstance(exc.orig, sqlite3.Error):
        # Include extended SQLite error codes; never match free-form messages.
        code = getattr(exc.orig, "sqlite_errorcode", 0) & 0xFF
        if code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            return Failure("sqlite_lock", retryable=True)
        if code in (sqlite3.SQLITE_CANTOPEN, sqlite3.SQLITE_IOERR,
                    sqlite3.SQLITE_FULL, sqlite3.SQLITE_READONLY):
            return Failure("sqlite_storage")
    sqlstate = getattr(exc.orig, "sqlstate", None)  # PostgreSQL (psycopg)
    if sqlstate in _PG_RETRYABLE:
        return Failure("pg_contention", retryable=True)
    if sqlstate is not None and (sqlstate in _PG_UNAVAILABLE or sqlstate[:2] in ("08", "53")):
        return Failure("pg_unavailable")
    return None  # SQL errors/constraints/programming errors are not transient.


# serialization_failure, deadlock_detected, lock_not_available (lock_timeout): giao dịch
# đã bị hủy trọn vẹn, chạy lại là an toàn.
_PG_RETRYABLE = {"40001", "40P01", "55P03"}
# query_canceled (statement_timeout), admin/crash shutdown, cannot_connect_now;
# cùng với lớp 08 (kết nối) và 53 (thiếu tài nguyên) ở trên.
_PG_UNAVAILABLE = {"57014", "57P01", "57P02", "57P03"}


class CircuitBreaker:
    """Failures in a sliding window; one probe after cooldown, per process.

    Successes during CLOSED do not erase failures: otherwise a successful auth
    read could mask every failed order write. Generation tokens ignore stale
    completions from requests admitted before a transition.
    """

    def __init__(self, threshold=5, window=30, recovery=10, clock=time.monotonic):
        if threshold < 1 or window <= 0 or recovery <= 0:
            raise ValueError("Invalid circuit breaker settings")
        self.threshold, self.window, self.recovery = threshold, window, recovery
        self.clock = clock
        self._lock = Lock()
        self._failures = deque()
        self._opened_at = None
        self._probe = False
        self._generation = 0

    def acquire(self):
        with self._lock:
            if self._opened_at is not None:
                remaining = self.recovery - (self.clock() - self._opened_at)
                if remaining > 0 or self._probe:
                    raise CircuitOpen(max(1, math.ceil(remaining)))
                self._probe = True
                logger.info("db_circuit state=half_open")
                return self._generation, True
            return self._generation, False

    def success(self, token):
        with self._lock:
            if token == (self._generation, True):
                self._opened_at = None
                self._probe = False
                self._failures.clear()
                self._generation += 1
                logger.info("db_circuit state=closed")

    def neutral(self, token):
        with self._lock:
            if token == (self._generation, True):
                self._probe = False

    def failure(self, token):
        with self._lock:
            if token[0] != self._generation:
                return
            now = self.clock()
            while self._failures and self._failures[0] <= now - self.window:
                self._failures.popleft()
            self._failures.append(now)
            if token[1] or len(self._failures) >= self.threshold:
                self._opened_at = now
                self._probe = False
                self._generation += 1
                logger.warning("db_circuit state=open")


class DatabasePolicy:
    def __init__(self, breaker, max_attempts=3, base_delay=0.05, max_delay=0.5,
                 sleep=time.sleep, jitter=random.uniform, clock=time.monotonic):
        if max_attempts < 1 or base_delay < 0 or max_delay < 0:
            raise ValueError("Invalid database retry settings")
        self.breaker = breaker
        self.max_attempts = max_attempts
        self.base_delay, self.max_delay = base_delay, max_delay
        self.sleep, self.jitter, self.clock = sleep, jitter, clock

    @contextmanager
    def protect(self):
        """Protect non-replayable work such as get_db's yielded transaction."""
        token = self.breaker.acquire()
        started = self.clock()
        try:
            yield
        except BaseException as exc:
            failure = classify_failure(exc)
            if failure:
                self.breaker.failure(token)
                logger.warning("db_unavailable kind=%s elapsed_ms=%.1f",
                               failure.kind, (self.clock() - started) * 1000)
                raise DatabaseUnavailable() from exc
            if isinstance(exc, BusinessError):
                self.breaker.success(token)
            else:
                self.breaker.neutral(token)
            raise
        else:
            self.breaker.success(token)

    def run(self, operation):
        """Operation must fully rollback/close before raising; recreate on retry."""
        with self.protect():
            for attempt in range(1, self.max_attempts + 1):
                try:
                    return operation()
                except Exception as exc:
                    failure = classify_failure(exc)
                    if not failure or not failure.retryable or attempt == self.max_attempts:
                        raise
                    ceiling = min(self.max_delay, self.base_delay * (2 ** (attempt - 1)))
                    delay = self.jitter(0, ceiling)
                    logger.warning("db_retry kind=%s attempt=%d delay_ms=%.1f",
                                   failure.kind, attempt, delay * 1000)
                    self.sleep(delay)


database_policy = DatabasePolicy(
    CircuitBreaker(config.DB_BREAKER_FAILURE_THRESHOLD,
                   config.DB_BREAKER_WINDOW_SECONDS, config.DB_BREAKER_RECOVERY_SECONDS),
    max_attempts=config.DB_MAX_ATTEMPTS,
    base_delay=config.DB_RETRY_BASE_SECONDS,
    max_delay=config.DB_RETRY_MAX_SECONDS,
)
