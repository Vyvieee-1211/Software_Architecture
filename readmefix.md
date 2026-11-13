## 1. Mục 2 — Chống hủy cùng một đơn nhiều lần đồng thời

**Vấn đề:** Hai request có thể cùng đọc đơn `confirmed` và cùng hoàn kho, làm số vé còn lại vượt tổng vé.

- Thêm `OrderRepository.cancel_if_confirmed()`: chỉ đổi trạng thái khi đơn vẫn là `confirmed` và đúng chủ sở hữu.
- `OrderService` chỉ hoàn kho khi UPDATE thành công đúng một dòng. Đổi trạng thái và hoàn kho nằm trong cùng transaction.
- Hủy thành công trả **200**; hủy lặp hoặc thua cập nhật đồng thời trả **409**. Lỗi hạ tầng được xử lý riêng ở mục 4.

**File chính:** [order_repo.py](app/repositories/order_repo.py), [order_service.py](app/services/order_service.py).

Giữ UPDATE có điều kiện khi sửa luồng hủy. “Hoàn kho” chỉ là trả số vé, chưa bao gồm hoàn tiền thanh toán.

## 2. Mục 6 — Đưa ranh giới transaction về nghiệp vụ bằng Unit of Work

**Vấn đề:** Trước đây `get_db()` ở tầng API quyết định commit/rollback; gọi service ngoài HTTP phải tự quản lý transaction.

- `OrderService` nhận UoW factory thay cho ba repository riêng lẻ. Mỗi lần đặt/hủy/xem đơn dùng một session riêng.
- Service gọi `uow.commit()` sau khi hoàn tất nghiệp vụ. UoW rollback phần chưa commit khi thoát và luôn đóng session; repository không tự commit.
- Trả `OrderData` là bản chụp dữ liệu độc lập với session. Xác thực người dùng đóng session trước khi nghiệp vụ bắt đầu.

**Luồng mới:** `API → service mở UoW → thao tác các repository → commit → đóng session → trả kết quả`.

**File chính:** [giao diện UoW](app/services/unit_of_work.py), [triển khai SQLAlchemy UoW](app/repositories/unit_of_work.py), [order_service.py](app/services/order_service.py), [order_data.py](app/services/order_data.py), [deps.py](app/api/deps.py).

Thao tác mới liên quan đơn hàng phải dùng chung UoW của nghiệp vụ. `get_db()` vẫn quản lý transaction cho tài khoản/tra cứu concert. Định dạng API giữ nguyên; đối tượng service trả về là snapshot, không tự cập nhật khi DB thay đổi.

## 3. Mục 4 — Giới hạn chờ DB, retry và circuit breaker

**Vấn đề:** Request chờ connection hoặc khóa DB quá lâu; thử lại không kiểm soát có thể tăng tải và tạo đơn trùng.

- Mặc định chờ connection tối đa **2 giây**, chờ khóa SQLite **1 giây mỗi thao tác**.
- Retry khi SQLite `BUSY/LOCKED` hoặc pool timeout, tối đa **3 lượt tính cả lần đầu**, có thời gian chờ tăng dần và ngẫu nhiên. Nghiệp vụ đơn hàng được chạy lại bằng UoW/session mới sau rollback và đóng session cũ.
- Circuit breaker: **5 thao tác lỗi trong 30 giây** thì ngừng gọi DB **10 giây**, sau đó cho một request thăm dò. Trạng thái dùng chung trong từng tiến trình.
- Lỗi DB được nhận diện trả **503** kèm `Retry-After`; có log retry và chuyển trạng thái mạch. Lỗi nghiệp vụ, constraint hoặc SQL không được tự retry.

**File chính:** [resilience.py](app/repositories/resilience.py), [database.py](app/repositories/database.py), [config.py](app/config.py), [deps.py](app/api/deps.py), [main.py](app/main.py). Các giá trị cấu hình nằm trong [.env.example](.env.example); cần truyền qua biến môi trường, ứng dụng không tự đọc file `.env`.

- Retry áp dụng cho đặt/hủy/xem đơn và tra cứu người dùng khi xác thực. Tài khoản/tra cứu concert được circuit breaker bảo vệ nhưng không tự chạy lại endpoint.
- Chưa có idempotency của mục 5: không tự retry mất kết nối có kết quả commit chưa xác định; `Retry-After` không bảo đảm gửi lại POST là an toàn.
- Giới hạn hiện tại chỉ áp dụng cho chờ pool/khóa SQLite, chưa ngắt mọi truy vấn chậm hoặc I/O treo. Khi đổi DB cần bổ sung timeout và phân loại lỗi tương ứng.
