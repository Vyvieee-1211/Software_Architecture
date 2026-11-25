import os
import math
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Nạp `.env` ở thư mục gốc (nếu có) vào biến môi trường, KHÔNG ghi đè biến đã đặt.

    Nhờ vậy biến đặt trong terminal hay docker-compose luôn thắng file; test đặt
    DATABASE_URL trước khi import `app` nên không bị .env của máy dev ảnh hưởng.
    Cú pháp: KEY=VALUE mỗi dòng, bỏ qua dòng trống và dòng bắt đầu bằng #,
    giá trị có thể bọc trong dấu nháy đơn/kép.
    """
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _number(name, default, convert, minimum):
    value = convert(os.getenv(name, str(default)))
    if not math.isfinite(value) or value < minimum:
        raise ValueError(f"{name} must be finite and >= {minimum}")
    return value

def _sqlalchemy_url(url: str) -> str:
    """Render/Heroku cấp chuỗi `postgres://` hoặc `postgresql://`; SQLAlchemy cần chỉ rõ driver
    psycopg 3 (`postgresql+psycopg://`), nếu không sẽ tìm psycopg2 vốn không được cài."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


DATABASE_URL = _sqlalchemy_url(os.getenv(
    "DATABASE_URL", "postgresql+psycopg://concert:concert@localhost:5432/concert"
))
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me-please-use-32-bytes-or-more")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))

# Waiting limits, not an end-to-end HTTP request deadline.
DB_POOL_SIZE = _number("DB_POOL_SIZE", 20, int, 1)
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

# Cache-Aside cho danh sách concert/hạng vé (giây). Số vé còn lại hiển thị có thể cũ tối đa
# chừng này; đặt mua vẫn kiểm tra kho trong DB. 0 = tắt cache, mọi lần đọc vào DB.
CATALOG_CACHE_TTL_SECONDS = _number("CATALOG_CACHE_TTL_SECONDS", 2, float, 0)

# --- Giữ chỗ / thanh toán -------------------------------------------------
# Thời gian giữ vé chờ thanh toán; quá hạn thì đơn hết hạn và vé được trả về kho.
HOLD_MINUTES = _number("HOLD_MINUTES", 12, float, 0.01)
MAX_TICKETS_PER_ORDER = _number("MAX_TICKETS_PER_ORDER", 10, int, 1)
# Tổng số vé một người được giữ tạm (chưa thanh toán) cùng lúc.
MAX_HELD_TICKETS_PER_USER = _number("MAX_HELD_TICKETS_PER_USER", 10, int, 1)
# Chu kỳ quét đơn giữ chỗ quá hạn; 0 = tắt (vd. khi chạy test hoặc có worker riêng).
HOLD_SWEEP_INTERVAL_SECONDS = _number("HOLD_SWEEP_INTERVAL_SECONDS", 5, float, 0)
# --- Hàng đợi yêu cầu đặt vé (Asynchronous Request-Reply + Queue-Based Load Leveling) ---
# Số luồng worker xử lý hàng đợi trong mỗi tiến trình API; 0 = không chạy (test tự gọi drain()).
ORDER_WORKERS = _number("ORDER_WORKERS", 2, int, 0)
# Worker ngủ tối đa chừng này giây khi hàng đợi rỗng (yêu cầu mới trong cùng tiến trình đánh thức ngay).
ORDER_WORKER_POLL_SECONDS = _number("ORDER_WORKER_POLL_SECONDS", 0.5, float, 0.01)
# Một yêu cầu làm worker lỗi bất ngờ chừng này lần thì bị đánh dấu failed (poison message).
ORDER_REQUEST_MAX_ATTEMPTS = _number("ORDER_REQUEST_MAX_ATTEMPTS", 3, int, 1)
# Giá trị header Retry-After gợi ý client chờ trước khi hỏi lại trạng thái yêu cầu.
ORDER_STATUS_RETRY_AFTER_SECONDS = _number("ORDER_STATUS_RETRY_AFTER_SECONDS", 1, int, 1)
# Đơn đã thanh toán chỉ được hủy (hoàn toàn bộ tiền) trước giờ diễn ít nhất chừng này giờ.
REFUND_CUTOFF_HOURS = _number("REFUND_CUTOFF_HOURS", 24, float, 0)

# --- Đợt mở bán: Waiting room → Rate limit → Load shedding (từ ngoài vào trong) ---
# Virtual Waiting Room: bật trong chừng này phút đầu sau giờ mở bán của mỗi concert; 0 = tắt.
WAITING_ROOM_MINUTES = _number("WAITING_ROOM_MINUTES", 30, float, 0)
# Cho phép vào hàng chờ sớm chừng này phút trước giờ mở bán.
WAITING_ROOM_LEAD_MINUTES = _number("WAITING_ROOM_LEAD_MINUTES", 15, float, 0)
# Lúc mở bán cho vào ngay chừng này người, sau đó thêm WAITING_ROOM_ADMIT_PER_MINUTE người mỗi phút.
# Chọn theo sức chịu đo được: số người đồng thời chịu được ÷ thời gian trung bình một lượt mua.
WAITING_ROOM_INITIAL_BATCH = _number("WAITING_ROOM_INITIAL_BATCH", 100, int, 0)
WAITING_ROOM_ADMIT_PER_MINUTE = _number("WAITING_ROOM_ADMIT_PER_MINUTE", 100, float, 0)
# Thẻ vào cửa dùng được chừng này phút kể từ khi tới lượt; hết hạn thì xếp hàng lại.
WAITING_ROOM_PASS_MINUTES = _number("WAITING_ROOM_PASS_MINUTES", 10, float, 0.1)
WAITING_ROOM_SECRET = os.getenv("WAITING_ROOM_SECRET", "dev-waiting-room-secret-change-me-32-bytes")
# Rate limit (token bucket trong bộ nhớ mỗi tiến trình), số request mỗi phút; 0 = tắt.
# Chạy N tiến trình API thì giới hạn thực tế gấp khoảng N lần.
RATE_LIMIT_AUTH_PER_MINUTE = _number("RATE_LIMIT_AUTH_PER_MINUTE", 20, float, 0)            # theo IP
RATE_LIMIT_ORDERS_PER_MINUTE = _number("RATE_LIMIT_ORDERS_PER_MINUTE", 10, float, 0)        # theo user
RATE_LIMIT_ORDER_STATUS_PER_MINUTE = _number("RATE_LIMIT_ORDER_STATUS_PER_MINUTE", 120, float, 0)
RATE_LIMIT_WAITING_ROOM_PER_MINUTE = _number("RATE_LIMIT_WAITING_ROOM_PER_MINUTE", 60, float, 0)
# Load shedding: số request đang xử lý tối đa mỗi tiến trình; vượt thì trả 503 ngay. 0 = tắt.
MAX_IN_FLIGHT_REQUESTS = _number("MAX_IN_FLIGHT_REQUESTS", 200, int, 0)
OVERLOAD_RETRY_AFTER_SECONDS = _number("OVERLOAD_RETRY_AFTER_SECONDS", 1, int, 1)

# Một hoặc nhiều cổng (mock | vnpay), cách nhau bằng dấu phẩy (vd. "vnpay,mock"): khách chọn cổng khi thanh
# toán, cổng đầu tiên là mặc định.
PAYMENT_PROVIDER = os.getenv("PAYMENT_PROVIDER", "mock")
# Khóa bí mật dùng chung với cổng thanh toán để ký/kiểm tra webhook (HMAC-SHA256).
PAYMENT_WEBHOOK_SECRET = os.getenv("PAYMENT_WEBHOOK_SECRET", "dev-payment-secret-change-me")
# Khóa ký mã QR của vé.
TICKET_QR_SECRET = os.getenv("TICKET_QR_SECRET", "dev-ticket-qr-secret-change-me")
# URL công khai của hệ thống, dùng để tạo đường dẫn thanh toán giả lập.
# Trên Render, RENDER_EXTERNAL_URL (vd. https://hit-the-vibe.onrender.com) được đặt sẵn.
PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL")
                   or "http://localhost:8000").rstrip("/")
# Trả tiền xong, VNPay đưa khách quay về /payments/return/vnpay, rồi được chuyển
# tiếp tới địa chỉ này. Trạng thái đơn vẫn do IPN quyết định; trang này chỉ hiển thị.
# Không cần khai: mặc định là trang "Vé của tôi" của PUBLIC_BASE_URL. Nhiều trình nạp .env
# (VS Code `python.terminal.useEnvFile`, docker) coi `#` là bắt đầu chú thích và cắt mất
# "#/orders", nên địa chỉ không có `#` được hiểu là gốc web và tự nối thêm "/#/orders".
def payment_return_url(raw: str | None, public_base_url: str) -> str:
    url = raw or public_base_url
    return url if "#" in url else url.rstrip("/") + "/#/orders"


PAYMENT_RETURN_URL = payment_return_url(os.getenv("PAYMENT_RETURN_URL"), PUBLIC_BASE_URL)
# Thời gian chờ tối đa một lời gọi HTTP tới cổng thanh toán thật (giây).
PAYMENT_GATEWAY_TIMEOUT_SECONDS = _number("PAYMENT_GATEWAY_TIMEOUT_SECONDS", 10, float, 0.1)

# --- VNPay (PAYMENT_PROVIDER=vnpay): tài khoản sandbox tại https://sandbox.vnpayment.vn ---
# IPN URL khai trong trang quản trị merchant của VNPay: {PUBLIC_BASE_URL}/payments/webhook
VNPAY_TMN_CODE = os.getenv("VNPAY_TMN_CODE", "")
VNPAY_HASH_SECRET = os.getenv("VNPAY_HASH_SECRET", "")
VNPAY_PAY_URL = os.getenv("VNPAY_PAY_URL", "https://sandbox.vnpayment.vn/paymentv2/vpcpay.html")
VNPAY_API_URL = os.getenv("VNPAY_API_URL", "https://sandbox.vnpayment.vn/merchant_webapi/api/transaction")


# PostgreSQL: giới hạn chờ khóa và thời gian chạy một câu lệnh (mili giây); 0 = không giới hạn.
DB_PG_LOCK_TIMEOUT_MS = _number("DB_PG_LOCK_TIMEOUT_MS", 3000, int, 0)
DB_PG_STATEMENT_TIMEOUT_MS = _number("DB_PG_STATEMENT_TIMEOUT_MS", 15000, int, 0)
