"""Load shedding: lớp chắn cuối cùng khi API quá tải (middleware ASGI).

Đếm số request đang xử lý trong tiến trình. Vượt `max_in_flight` thì trả NGAY 503 kèm
Retry-After thay vì nhận thêm vào hàng chờ của threadpool, nơi request chờ tới khi client
hết kiên nhẫn (S2 ngày 18/11: hơn một nửa request mất phản hồi). Client nhận lỗi rõ ràng,
biết lúc nào thử lại, còn những request đã nhận vẫn được xử lý đủ nhanh.

Bộ đếm chạy trong vòng lặp sự kiện (một luồng) nên không cần khóa. Webhook/cổng thanh toán
và /health được miễn: xác nhận thanh toán không bao giờ bị bỏ vì quá tải.
"""
import json


class LoadShedder:
    def __init__(self, app, max_in_flight: int, retry_after: int = 1,
                 exempt_prefixes: tuple[str, ...] = ("/health", "/payments", "/mock-gateway")):
        self.app = app
        self.max_in_flight = max_in_flight
        self.retry_after = retry_after
        self.exempt_prefixes = exempt_prefixes
        self.in_flight = 0
        self.shed = 0  # số request đã bị từ chối, để quan sát

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or self.max_in_flight <= 0
                or scope["path"].startswith(self.exempt_prefixes)):
            await self.app(scope, receive, send)
            return
        if self.in_flight >= self.max_in_flight:
            self.shed += 1
            await self._reject(send)
            return
        self.in_flight += 1
        try:
            await self.app(scope, receive, send)
        finally:
            self.in_flight -= 1

    async def _reject(self, send):
        body = json.dumps({"error": "ServerBusy",
                           "detail": "Server is overloaded, please retry later"}).encode()
        await send({"type": "http.response.start", "status": 503, "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"retry-after", str(self.retry_after).encode()),
        ]})
        await send({"type": "http.response.body", "body": body})
