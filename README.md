# Concert Ticketing — Pha 1

Backend REST đặt vé concert, kiến trúc phân tầng **API → Nghiệp vụ → Truy cập dữ liệu**.
Python 3.12 · FastAPI · SQLAlchemy 2.0 · SQLite · JWT · Docker.

## Chạy nhanh bằng Docker

```bash
docker compose up --build            # API tại http://localhost:8000, Swagger tại /docs
docker compose exec api python -m scripts.seed   # tạo 2 concert, 6 hạng vé, user demo
```

Tài khoản demo: `demo@example.com` / `password123`.

## Chạy local (không Docker) — chỉ cần Python

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
python -m scripts.seed
```

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
pytest -q            # 12 test, SQLite in-memory
```

## Kiểm thử tải (Kaggle CPU)

Trong một notebook Kaggle (Internet: On):

```bash
!git clone https://github.com/<nhom>/concert-ticketing && cd concert-ticketing
!pip install -q -r requirements.txt locust
# Kaggle không có Docker → chạy uvicorn trực tiếp
!nohup uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 > api.log 2>&1 &
!sleep 3 && python -m scripts.seed
!locust -f loadtest/locustfile.py --host http://127.0.0.1:8000 --headless -u 200 -r 20 -t 2m --csv loadtest/result
```

Ghi lại vào báo cáo: RPS, p50/p95/p99 latency, tỉ lệ lỗi, số vé bán được so với tổng
(`SELECT total_quantity - remaining FROM ticket_types` so với `SUM(quantity)` của orders confirmed → phải bằng nhau).
Đây là số liệu "trước" để Pha 2 so sánh.

## Biến môi trường

| Tên | Mặc định | Ý nghĩa |
|---|---|---|
| DATABASE_URL | sqlite:///./concert.db | chuỗi kết nối SQLAlchemy (Docker: sqlite:////data/concert.db) |
| JWT_SECRET | (dev) | ≥ 32 ký tự khi chạy thật |
| JWT_EXPIRE_MINUTES | 60 | hạn token |
