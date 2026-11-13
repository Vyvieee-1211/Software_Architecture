"""Luồng nền định kỳ trả vé của các đơn giữ chỗ đã quá hạn.

Tính đúng không phụ thuộc vào luồng này: thanh toán chỉ chốt được đơn còn hạn, và mỗi
đơn chỉ chuyển trạng thái được một lần. Luồng này chỉ quyết định vé quá hạn quay lại
kho sớm hay muộn, nên chạy ở nhiều tiến trình cùng lúc cũng an toàn.
"""
import logging
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)


class HoldSweeper:
    def __init__(self, expire_holds: Callable[[], int], interval_seconds: float):
        self._expire_holds = expire_holds
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="hold-sweeper", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                expired = self._expire_holds()
                if expired:
                    logger.info("hold_sweeper expired=%d", expired)
            except Exception:  # DB tạm lỗi: ghi log, lượt sau thử lại
                logger.warning("hold_sweeper failed", exc_info=True)
