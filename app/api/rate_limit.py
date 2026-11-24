"""Rate limit theo từng người dùng / IP: token bucket trong bộ nhớ của tiến trình.

Mỗi khóa (vd. "user:42", "ip:1.2.3.4") có một "xô" chứa tối đa `per_minute` token, được
bơm lại đều `per_minute/60` token mỗi giây; mỗi request lấy một token. Hết token thì từ chối
với 429 kèm Retry-After (số giây tới khi có token kế tiếp). Cho phép dồn một nhịp ngắn
(tối đa `per_minute` request liền) nhưng không vượt tốc độ trung bình.

Lưu trong RAM nên không thêm tải DB; đổi lại mỗi tiến trình đếm riêng.
"""
import math
import threading
import time
from collections.abc import Callable


class RateLimitExceeded(Exception):
    def __init__(self, retry_after: int):
        super().__init__(f"rate limit exceeded, retry after {retry_after}s")
        self.retry_after = retry_after


class RateLimiter:
    def __init__(self, per_minute: float, clock: Callable[[], float] = time.monotonic,
                 max_keys: int = 100_000):
        self.capacity = per_minute
        self.refill_per_second = per_minute / 60
        self.clock = clock
        self.max_keys = max_keys
        self._buckets: dict[str, tuple[float, float]] = {}  # khóa → (số token, mốc cập nhật)
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.capacity > 0

    def hit(self, key: str) -> None:
        """Lấy một token cho `key`; hết token thì ném RateLimitExceeded."""
        if not self.enabled:
            return
        now = self.clock()
        with self._lock:
            tokens, updated = self._buckets.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - updated) * self.refill_per_second)
            if tokens < 1:
                self._buckets[key] = (tokens, now)
                raise RateLimitExceeded(max(1, math.ceil((1 - tokens) / self.refill_per_second)))
            self._buckets[key] = (tokens - 1, now)
            if len(self._buckets) > self.max_keys:
                self._evict_idle(now)

    def _evict_idle(self, now: float) -> None:
        """Bỏ các xô đã đầy lại (khóa lâu không dùng): xóa đi cũng như còn nguyên."""
        full_after = self.capacity / self.refill_per_second
        for key in [k for k, (_, updated) in self._buckets.items() if now - updated >= full_after]:
            del self._buckets[key]
