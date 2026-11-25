# Đưa web lên Render (miễn phí)

Mục tiêu: có một địa chỉ công khai cố định, dạng `https://hit-the-vibe.onrender.com`, để
xem web từ mọi máy và đăng ký cổng thanh toán sandbox (VNPay bắt khai website và IPN URL).

Cấu hình nằm sẵn trong repo:

- `render.yaml`: tạo web service (build từ `Dockerfile`) và PostgreSQL, khu vực Singapore.
- `Dockerfile`: chạy theo cổng `PORT` của Render và tự nạp dữ liệu mẫu lần đầu (`SEED_ON_START`).
- `app/config.py`: tự đọc địa chỉ công khai từ `RENDER_EXTERNAL_URL` và tự đổi chuỗi
  `postgresql://` của Render sang driver psycopg 3, nên không phải khai `PUBLIC_BASE_URL` hay
  sửa `DATABASE_URL`.

## 1. Đẩy code lên GitHub

Render lấy code từ GitHub. Blueprint dùng nhánh mặc định (`main`), nên merge thay đổi vào `main`
và push trước khi tạo (hoặc chọn nhánh khác ở bước 2).

## 2. Tạo Blueprint trên Render

1. Đăng ký <https://render.com> bằng tài khoản GitHub.
2. **New → Blueprint**, chọn repo `Software_Architecture`. Render đọc `render.yaml` và liệt kê:
   web service `hit-the-vibe` và database `hit-the-vibe-db`.
3. Render hỏi các biến `VNPAY_*`: **để trống** nếu chưa có key (web chạy bằng cổng
   giả lập). Bấm **Apply**.
4. Chờ build xong (vài phút). Địa chỉ web hiện ở đầu trang của service `hit-the-vibe`.

Mở địa chỉ đó, đăng nhập `demo@example.com` / `password123`.

Tên `hit-the-vibe` đã có người dùng thì Render thêm hậu tố ngẫu nhiên vào địa chỉ; dùng đúng
địa chỉ Render hiển thị cho các bước sau.

## 3. Đăng ký VNPay sandbox

Tại <https://sandbox.vnpayment.vn/devreg/>, với `<địa-chỉ>` là địa chỉ ở bước 2:

| Ô trong form | Giá trị |
|---|---|
| Website | `https://<địa-chỉ>` |
| IPN URL | `https://<địa-chỉ>/payments/webhook` |

VNPay gửi email có `vnp_TmnCode`, `vnp_HashSecret`, tài khoản trang quản trị merchant và thẻ
test.

## 4. Bật cổng thật

Service `hit-the-vibe` → **Environment**:

| Biến | Giá trị |
|---|---|
| `PAYMENT_PROVIDER` | `vnpay` (hoặc `vnpay,mock` để giữ cổng giả lập khi demo; cổng đầu là mặc định) |
| `VNPAY_TMN_CODE`, `VNPAY_HASH_SECRET` | từ email VNPay |

**Save Changes**: Render tự deploy lại. Thiếu key của cổng nào đã bật thì service không khởi
động được, log ghi rõ biến còn thiếu (vd. `VNPAY_TMN_CODE is required`).

Key chỉ khai ở Render (và `.env` trên máy), không đưa vào file nào trong repo.

## Giới hạn của gói miễn phí

- **Web ngủ** sau khoảng 15 phút không có truy cập; lần mở kế tiếp chờ chừng nửa phút. IPN tới
  lúc đang ngủ có thể bị lỡ, nhưng đơn vẫn được chốt khi khách quay về từ cổng thanh toán.
- **Database miễn phí có hạn dùng** (xem điều khoản hiện tại của Render). Hết hạn thì tạo
  database mới; dữ liệu mẫu tự nạp lại khi khởi động.
- **Một tiến trình, RAM nhỏ**: đủ để demo, không dùng để đo tải. Số liệu load test vẫn chạy trên
  máy hoặc Kaggle như cũ.

## Đổi database / reset dữ liệu

Bảng chỉ được tạo bằng `create_all`, không có migration. Sau khi đổi `app/repositories/models.py`,
xoá và tạo lại database `hit-the-vibe-db` trên Render (dữ liệu cũ mất), rồi deploy lại service để
dữ liệu mẫu được nạp lại.
