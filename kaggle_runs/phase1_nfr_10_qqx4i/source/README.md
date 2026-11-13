# Concert Ticketing — Pha 1

Ứng dụng đặt vé concert với giao diện web đơn giản bằng HTML, CSS, JavaScript thuần.
Backend REST dùng kiến trúc phân tầng **API → Nghiệp vụ → Truy cập dữ liệu**.
Python 3.12 · FastAPI · SQLAlchemy 2.0 · SQLite · JWT · Docker.

Giao diện và API chạy chung tại **http://localhost:8000**. Không cần Node.js, npm hay bước build frontend.
Swagger API vẫn ở http://localhost:8000/docs.

## Chạy nhanh trên Windows (PowerShell)

Chạy các lệnh sau trong thư mục dự án:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m scripts.seed
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Mở **http://localhost:8000**. Đăng nhập bằng `demo@example.com` / `password123`,
hoặc tạo tài khoản mới. Lệnh seed tạo 2 concert, 6 hạng vé và tài khoản demo nếu chưa có concert;
nếu cơ sở dữ liệu đã có concert, lệnh sẽ bỏ qua và giữ nguyên dữ liệu.

## Chạy nhanh bằng Docker

```bash
docker compose up --build            # giao diện tại http://localhost:8000, Swagger tại /docs
docker compose exec api python -m scripts.seed   # tạo 2 concert, 6 hạng vé, user demo
```

Tài khoản demo: `demo@example.com` / `password123`.

## Chạy local trên Linux/macOS — chỉ cần Python

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m scripts.seed
python -m uvicorn app.main:app --reload
```

## Sử dụng giao diện

- Xem danh sách concert, tìm kiếm và xem các hạng vé.
- Đăng ký, đăng nhập, chọn hạng vé và số lượng để xác nhận đặt vé.
- Mở **Đơn vé của tôi** để xem hoặc huỷ đơn đã xác nhận.

Giá hiển thị bằng đồng Việt Nam, ngày giờ theo múi giờ Việt Nam.
Các thao tác gọi trực tiếp API hiện có; backend kiểm tra thời điểm mở bán và tồn kho.
Đây là xác nhận đặt vé, chưa có thanh toán trực tuyến hay chọn ghế.

JWT được giữ trong `sessionStorage` của tab trình duyệt và được xoá khi đăng xuất.
Các trang dùng đường dẫn hash (ví dụ `/#/login`) nên có thể tải lại mà không cần cấu hình chuyển hướng.
Hãy mở giao diện qua địa chỉ của server, không mở trực tiếp tệp `frontend/index.html`.

## Thử API bằng curl

```bash
# đăng nhập, lấy token
TOKEN=$(curl -s -X POST localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"demo@example.com","password":"password123"}' | python -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

# xem hạng vé của concert 1
curl -s localhost:8000/concerts/1/ticket-types

# đặt 2 vé hạng 1 (cần token)
curl -s -X POST localhost:8000/orders -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"ticket_type_id":1,"quantity":2}'

# huỷ đơn 1
curl -s -X DELETE localhost:8000/orders/1 -H "Authorization: Bearer $TOKEN"
```

## Cấu trúc

```
app/api/            tầng API      — FastAPI router, Pydantic schema, xác thực (deps.py)
app/services/       tầng nghiệp vụ — Python thuần, KHÔNG import fastapi/sqlalchemy
app/repositories/   tầng dữ liệu  — SQLAlchemy models + repository
frontend/           giao diện HTML/CSS/JavaScript được FastAPI phục vụ
  index.html        khung trang
  app.js            trang, biểu mẫu và điều hướng
  api.js            gọi API và quản lý phiên đăng nhập
  styles.css        giao diện responsive
tests/              pytest (service với repo giả; API với SQLite in-memory)
loadtest/           Locust
scripts/seed.py     dữ liệu mẫu
```

Kiểm tra ràng buộc phân tầng: `grep -ri "fastapi\|sqlalchemy" app/services/` phải trả về rỗng.

## Endpoint

| Method | Path | Auth |
|---|---|---|
| POST | /auth/register | – |
| POST | /auth/login | – |
| GET | /concerts | – |
| GET | /concerts/{id}/ticket-types | – |
| POST | /orders | Bearer |
| GET | /orders/me | Bearer |
| DELETE | /orders/{id} | Bearer |

## Kiểm thử

```bash
python -m pytest -q  # nghiệp vụ, API và phục vụ frontend; SQLite in-memory
```

Nếu có Node.js 20 trở lên, có thể chạy thêm kiểm thử API client (không cần npm):

```bash
node --test tests/frontend_api.test.mjs
```

Trên Windows: `.\.venv\Scripts\python.exe -m pytest -q`.

## Kiểm thử tải (Kaggle CPU)

Dùng [notebook Kaggle](loadtest/kaggle_baseline.ipynb) và
[hướng dẫn săn vé](loadtest/README.md). Notebook chứa sẵn mã nguồn, không cần push GitHub.

Kịch bản mới dùng 200 tài khoản chuẩn bị trước, đăng nhập một lần/user rồi xem concert,
xem hạng vé, đặt 1–2 vé, xem đơn và một số user hủy vé. Không đăng ký trong thời gian đo.
Mỗi lượt tạo database riêng, không sửa `concert.db` gốc.

```bash
python -m pip install -r loadtest/requirements.txt
python -m loadtest.run --users 200 --spawn-rate 20 --seconds 120 --tickets 150
```

Để tạo lại notebook sau khi thay đổi source:

```bash
python -m scripts.build_kaggle_notebook
```

Lưu ZIP kết quả gồm CSV, HTML, log, database thử, đối chiếu tồn kho và bản mã đã chạy.
Giữ cùng kịch bản/dữ liệu cho Phase 1 và Phase 2; không so trực tiếp với bài cũ có đăng ký.

## Biến môi trường

| Tên | Mặc định | Ý nghĩa |
|---|---|---|
| DATABASE_URL | sqlite:///./concert.db | chuỗi kết nối SQLAlchemy (Docker: sqlite:////data/concert.db) |
| JWT_SECRET | (dev) | ≥ 32 ký tự khi chạy thật |
| JWT_EXPIRE_MINUTES | 60 | hạn token |
