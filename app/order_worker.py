"""Các luồng worker xử lý hàng đợi yêu cầu đặt vé (Competing Consumers).

Mỗi tiến trình API chạy ORDER_WORKERS luồng; nhiều tiến trình cùng chạy cũng an toàn vì
worker chia việc bằng SKIP LOCKED và mỗi yêu cầu chỉ sinh tối đa một đơn. Số luồng là
"van" san tải: lúc mở bán, số transaction cùng tranh dòng tồn kho không vượt quá tổng
số worker, bất kể có bao nhiêu request HTTP đến.
"""
import logging
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)

# Được `submit` bật khi có yêu cầu mới để worker trong cùng tiến trình dậy ngay thay vì
# chờ hết chu kỳ hỏi định kỳ. Worker ở tiến trình khác vẫn thấy việc nhờ hỏi định kỳ.
wakeup = threading.Event()


class OrderRequestWorker:
    def __init__(self, process_next: Callable[[], bool], threads: int, poll_seconds: float,
                 signal: threading.Event = wakeup):
        self._process_next = process_next
        self._poll = poll_seconds
        self._signal = signal
        self._stop = threading.Event()
        self._threads = [threading.Thread(target=self._run, name=f"order-worker-{i}", daemon=True)
                         for i in range(threads)]

    def start(self) -> None:
        for thread in self._threads:
            thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._signal.set()  # đánh thức các luồng đang chờ để chúng thấy cờ dừng
        for thread in self._threads:
            thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            # Xóa tín hiệu TRƯỚC khi lấy việc: yêu cầu đến trong lúc đang xử lý sẽ bật lại
            # tín hiệu, nên lần chờ sau trả về ngay, không bỏ sót.
            self._signal.clear()
            try:
                while not self._stop.is_set() and self._process_next():
                    pass
            except Exception:  # DB tạm lỗi: ghi log, nghỉ một chu kỳ rồi thử lại
                logger.warning("order_worker failed", exc_info=True)
                self._stop.wait(self._poll)
                continue
            self._signal.wait(self._poll)
