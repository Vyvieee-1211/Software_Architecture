"""Cache-Aside có TTL ngắn kèm single-flight cho dữ liệu đọc nhiều, ghi ít.

- Cache-Aside: service hỏi cache trước; trống hoặc hết hạn thì tự đọc DB rồi lưu lại.
- Single-flight: khi một khóa hết hạn, chỉ MỘT luồng đọc DB; các luồng cùng hỏi khóa đó
  chờ và dùng chung kết quả, nên lúc cache hết hạn giữa đợt tải cao DB không bị dồn
  hàng trăm truy vấn giống hệt nhau (cache stampede).

Cache nằm trong bộ nhớ của từng tiến trình API. Chạy nhiều tiến trình thì mỗi tiến trình
một bản; dữ liệu lệch giữa các tiến trình tối đa bằng TTL. Chỉ dùng cho dữ liệu hiển thị:
số vé còn lại có thể cũ tối đa TTL giây, còn bước đặt mua luôn kiểm tra lại trong DB.
"""
import threading
import time
from collections.abc import Callable, Hashable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _Flight:
    """Một lần đọc DB đang diễn ra cho một khóa; các luồng đến sau chờ trên `done`."""
    done: threading.Event = field(default_factory=threading.Event)
    value: Any = None
    error: BaseException | None = None


class ReadThroughCache:
    def __init__(self, ttl_seconds: float, clock: Callable[[], float] = time.monotonic):
        self.ttl = ttl_seconds
        self.clock = clock
        self._lock = threading.Lock()
        self._entries: dict[Hashable, tuple[float, Any]] = {}
        self._flights: dict[Hashable, _Flight] = {}
        # Tăng mỗi lần vô hiệu hóa: lần đọc DB bắt đầu trước đó không được ghi đè dữ liệu cũ.
        self._generation = 0
        self.hits = self.misses = self.shared = 0

    @property
    def enabled(self) -> bool:
        return self.ttl > 0

    def get_or_load(self, key: Hashable, loader: Callable[[], Any]) -> Any:
        """Trả giá trị còn hạn trong cache, nếu không thì gọi `loader()` (một luồng cho mỗi khóa).

        Lỗi của `loader` (vd. NotFound, DatabaseUnavailable) không được lưu vào cache, chỉ
        được trả cho các luồng đang chờ cùng lượt đó.
        """
        if not self.enabled:
            return loader()
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry[0] > self.clock():
                self.hits += 1
                return entry[1]
            flight = self._flights.get(key)
            leader = flight is None
            if leader:
                flight = self._flights[key] = _Flight()
                generation = self._generation
                self.misses += 1
            else:
                self.shared += 1
        if not leader:
            flight.done.wait()
            if flight.error is not None:
                raise flight.error
            return flight.value
        try:
            flight.value = loader()
        except BaseException as exc:
            flight.error = exc
            raise
        else:
            with self._lock:
                if generation == self._generation:
                    self._entries[key] = (self.clock() + self.ttl, flight.value)
            return flight.value
        finally:
            with self._lock:
                if self._flights.get(key) is flight:
                    del self._flights[key]
            flight.done.set()

    def invalidate(self) -> None:
        """Xóa toàn bộ cache, dùng sau khi dữ liệu nguồn đã commit thay đổi.

        Các lần đọc DB đang dở cũng bị tách ra: request đến sau sẽ đọc lại thay vì chờ
        và nhận kết quả có thể đã cũ.
        """
        with self._lock:
            self._generation += 1
            self._entries.clear()
            self._flights.clear()
