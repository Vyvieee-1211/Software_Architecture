# 🎟️ Hệ thống săn vé Concert — Concert Ticketing System

> \*\*Phase 1:\*\* Xây dựng backend REST API cho hệ thống đặt vé concert.  
---

## 1\. Thông tin học phần

|Nội dung|Thông tin|
|-|-|
|**Tên môn học**|Kiến trúc phần mềm|
|**Mã lớp học phần**|INT3105 2|
|**Giảng viên**|PGS.TS. Võ Đình Hiếu|
|**Tên dự án**|Hệ thống săn vé Concert (Concert Ticketing System)|

### Thành viên nhóm

|STT|Họ và tên|MSSV|
|-:|-|-|
|1|Phan Thị Hà Vy|24020370|
|2|Thìn Thị Thúy|24020319|
|3|Trần Đình Hiếu|23021555|

\---

## 2\. Giới thiệu dự án

**Concert Ticketing System** là hệ thống cung cấp REST API phục vụ việc xem thông tin concert, tra cứu loại vé, đăng ký/đăng nhập và đặt vé trực tuyến. Client giao tiếp với backend thông qua HTTP và dữ liệu JSON.

Bài toán đặt vé concert có một số yêu cầu nghiệp vụ đáng chú ý: người dùng phải đăng nhập trước khi đặt vé; số lượng vé đặt phải hợp lệ; vé chỉ được đặt khi loại vé tồn tại và đang trong thời gian mở bán; người dùng chỉ được quản lý đơn hàng của chính mình. Khi hủy một đơn hàng hợp lệ, hệ thống cập nhật trạng thái đơn và hoàn trả số lượng vé theo logic nghiệp vụ.

Một rủi ro cần quan tâm trong hệ thống săn vé là **overselling** — tổng số vé được xác nhận vượt quá số vé có thể bán — đặc biệt khi có nhiều yêu cầu đặt vé đến gần như đồng thời. Phase 1 xây dựng nền tảng nghiệp vụ và bộ kiểm thử ban đầu; khả năng chịu tải, tính nhất quán khi có tranh chấp đồng thời và các cải tiến kiến trúc sẽ tiếp tục được đánh giá trong Phase 2.

### Mục tiêu Phase 1

* Xây dựng backend theo mô hình REST API, trao đổi dữ liệu bằng JSON.
* Tổ chức mã nguồn theo kiến trúc ba tầng: API, Business và Data Access.
* Tách logic nghiệp vụ khỏi framework web và thư viện cơ sở dữ liệu.
* Hỗ trợ đăng ký, đăng nhập và xác thực JWT cho các API được bảo vệ.
* Cung cấp các chức năng xem concert, tra cứu loại vé, đặt vé và quản lý đơn hàng.
* Cung cấp tài liệu API và kiểm thử chức năng để làm cơ sở đánh giá chất lượng.

\---

## 3\. Chức năng chính

|Nhóm chức năng|Mô tả|
|-|-|
|**Tài khoản**|Đăng ký tài khoản và đăng nhập bằng email/mật khẩu.|
|**Xác thực**|Sử dụng JWT Bearer Token để xác thực người dùng ở các API yêu cầu đăng nhập.|
|**Tra cứu concert**|Lấy danh sách concert hiện có.|
|**Tra cứu loại vé**|Xem các loại vé thuộc một concert.|
|**Đặt vé**|Tạo đơn đặt vé theo loại vé và số lượng yêu cầu; kiểm tra các điều kiện nghiệp vụ liên quan.|
|**Xem đơn hàng**|Lấy danh sách đơn hàng của người dùng đang đăng nhập.|
|**Hủy đơn hàng**|Kiểm tra quyền sở hữu và trạng thái đơn trước khi hủy; xử lý hoàn trả số lượng vé theo nghiệp vụ.|
|**Kiểm tra dữ liệu**|Dùng schema để xác thực dữ liệu request và chuẩn hóa response.|
|**Tài liệu API**|Tích hợp Swagger UI và ReDoc thông qua FastAPI/OpenAPI.|
|**Kiểm thử**|Sử dụng pytest/HTTPX cho kiểm thử tự động; có kịch bản Locust để thử tải.|

\---

## 4\. Công nghệ sử dụng

|Công nghệ|Vai trò trong dự án|
|-|-|
|**Python 3.12**|Ngôn ngữ lập trình backend.|
|**FastAPI**|Xây dựng REST API và quản lý dependency của request.|
|**Uvicorn**|ASGI server để chạy ứng dụng FastAPI.|
|**SQLAlchemy 2.0**|ORM và lớp làm việc với dữ liệu.|
|**SQLite**|Cơ sở dữ liệu mặc định của Phase 1, phù hợp cho phát triển và kiểm thử ban đầu.|
|**Pydantic**|Kiểm tra và mô hình hóa dữ liệu đầu vào/đầu ra.|
|**PyJWT / JWT**|Tạo và xác minh access token.|
|**bcrypt**|Băm mật khẩu trước khi lưu trữ.|
|**pytest**|Chạy các bài kiểm thử tự động.|
|**HTTPX**|Gửi request trong kiểm thử API.|
|**Locust**|Mô phỏng nhiều người dùng và đo hành vi API dưới tải. Cài riêng nếu chưa có trong môi trường.|
|**Docker / Docker Compose**|Đóng gói và khởi chạy ứng dụng trong môi trường nhất quán.|

\---

## 5\. Kiến trúc phần mềm

Dự án áp dụng **kiến trúc 3 tầng (Three-Tier Architecture)**. Mỗi tầng có trách nhiệm riêng, giúp giảm phụ thuộc giữa giao tiếp HTTP, logic nghiệp vụ và thao tác dữ liệu.

```text
                         Client
                           |
                           | HTTP / JSON
                           v
             +-----------------------------+
             |          API Layer          |
             | Routers / Schemas / Deps    |
             +-----------------------------+
                           |
                           | Gọi nghiệp vụ
                           v
             +-----------------------------+
             |        Business Layer       |
             | Services / Business Rules   |
             +-----------------------------+
                           |
                           | Repository
                           v
             +-----------------------------+
             |       Data Access Layer     |
             | Repositories / ORM Models   |
             +-----------------------------+
                           |
                           v
                    SQLite Database
```

### 5.1. API Layer – Tầng giao tiếp

**Vị trí:** `app/api/`

Nhiệm vụ:

* Khai báo các endpoint REST và nhận HTTP request.
* Kiểm tra, chuyển đổi dữ liệu request/response bằng schema.
* Quản lý dependency dùng chung như phiên làm việc với database và người dùng đã xác thực.
* Xác thực JWT thông qua dependency dùng chung thay vì lặp lại logic xác thực trong từng endpoint.
* Chuyển yêu cầu hợp lệ đến tầng nghiệp vụ và ánh xạ kết quả thành HTTP response.

### 5.2. Business Layer – Tầng nghiệp vụ

**Vị trí:** `app/services/`

Nhiệm vụ:

* Xử lý nghiệp vụ đăng ký và đăng nhập.
* Lấy thông tin concert và loại vé thông qua tầng truy cập dữ liệu.
* Kiểm tra các điều kiện nghiệp vụ khi đặt vé.
* Xử lý tạo, xem và hủy đơn hàng theo quy tắc của hệ thống.
* Tập trung các lỗi nghiệp vụ để API Layer có thể chuyển thành phản hồi HTTP phù hợp.

Một nguyên tắc thiết kế quan trọng là tầng `services` không phụ thuộc trực tiếp vào FastAPI hoặc SQLAlchemy. Cách tổ chức này giúp kiểm thử logic nghiệp vụ độc lập và tạo điều kiện thay đổi công nghệ ở tầng ngoài trong tương lai.

### 5.3. Data Access Layer – Tầng truy cập dữ liệu

**Vị trí:** `app/repositories/`

Nhiệm vụ:

* Khai báo cấu hình kết nối và phiên làm việc với database.
* Định nghĩa các mô hình dữ liệu bằng SQLAlchemy ORM.
* Đóng gói các thao tác truy vấn và cập nhật cho người dùng, concert, loại vé và đơn hàng.
* Cung cấp giao diện truy cập dữ liệu cho tầng nghiệp vụ thông qua Repository.

### 5.4. Luồng xử lý đặt vé

1. Client gửi yêu cầu `POST /orders` kèm thông tin loại vé, số lượng và JWT Bearer Token.
2. API Layer xác thực người dùng và kiểm tra định dạng dữ liệu đầu vào.
3. Business Layer kiểm tra loại vé, thời gian mở bán và các điều kiện đặt vé.
4. Repository thực hiện các thao tác đọc/cập nhật dữ liệu cần thiết.
5. API trả kết quả đặt vé hoặc lỗi phù hợp dưới dạng JSON.

Chi tiết bảo đảm tính nguyên tử và hành vi khi có nhiều yêu cầu đồng thời cần được xác nhận bằng kiểm thử và số liệu thực tế; đây cũng là một nội dung đánh giá quan trọng cho Phase 2.

\---

## 6\. Cấu trúc thư mục

```text
.
├── app/
│   ├── api/
│   │   ├── routers/
│   │   │   ├── auth.py
│   │   │   ├── concerts.py
│   │   │   └── orders.py
│   │   ├── deps.py
│   │   └── schemas.py
│   ├── repositories/
│   │   ├── database.py
│   │   ├── models.py
│   │   ├── concert\_repo.py
│   │   ├── ticket\_type\_repo.py
│   │   ├── order\_repo.py
│   │   └── user\_repo.py
│   ├── services/
│   │   ├── auth\_service.py
│   │   ├── concert\_service.py
│   │   ├── order\_service.py
│   │   └── errors.py
│   ├── config.py
│   └── main.py
├── loadtest/
│   └── locustfile.py
├── scripts/
│   └── seed.py
├── tests/
│   ├── test\_api.py
│   └── test\_order\_service.py
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```

### Vai trò của các thư mục chính

* `app/api/`: định nghĩa endpoint, schema và dependency phục vụ API.
* `app/services/`: chứa logic nghiệp vụ.
* `app/repositories/`: thao tác với cơ sở dữ liệu thông qua SQLAlchemy.
* `scripts/`: các script hỗ trợ, bao gồm khởi tạo dữ liệu mẫu.
* `tests/`: kiểm thử API và logic nghiệp vụ.
* `loadtest/`: kịch bản kiểm thử tải bằng Locust.
* `Dockerfile`, `docker-compose.yml`: cấu hình đóng gói và khởi chạy ứng dụng.

\---

## 7\. Cài đặt và chạy dự án

### 7.1. Yêu cầu môi trường

* Python 3.12 trở lên.
* Git nếu clone mã nguồn từ repository.
* Docker và Docker Compose nếu muốn chạy bằng container.

### 7.2. Chạy bằng Docker Compose

**Bước 1: Clone repository**

```bash
git clone https://github.com/Vyvieee-1211/Software_Architecture
cd Software_Architecture
```

**Bước 2: Khởi chạy ứng dụng**

```bash
docker compose up --build
```

API mặc định được cung cấp tại `http://localhost:8000`.

**Bước 3: Khởi tạo dữ liệu mẫu**

Mở terminal khác tại thư mục dự án và chạy:

```bash
docker compose exec api python -m scripts.seed
```

**Bước 4: Dừng ứng dụng**

Nhấn `Ctrl + C` tại terminal đang chạy hoặc thực hiện:

```bash
docker compose down
```

### 7.3. Chạy trực tiếp bằng Python

**Bước 1: Tạo môi trường ảo**

Windows PowerShell:

```powershell
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
```

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

**Bước 2: Cài đặt thư viện**

```bash
python -m pip install -r requirements.txt
```

**Bước 3: Khởi chạy API**

```bash
uvicorn app.main:app --reload
```

**Bước 4: Khởi tạo dữ liệu mẫu**

Mở terminal khác, kích hoạt cùng môi trường ảo rồi chạy:

```bash
python -m scripts.seed
```

### 7.4. Tài liệu API

Khi ứng dụng đang chạy, truy cập:

* **Swagger UI:** http://localhost:8000/docs
* **ReDoc:** http://localhost:8000/redoc
* **Health check:** http://localhost:8000/health

## 8\. Danh sách API

|Phương thức|Endpoint|Xác thực|Mô tả|
|-|-|-|-|
|`POST`|`/auth/register`|Không yêu cầu|Đăng ký tài khoản.|
|`POST`|`/auth/login`|Không yêu cầu|Đăng nhập và nhận access token.|
|`GET`|`/concerts`|Không yêu cầu|Lấy danh sách concert; thêm `?on_sale=true` để chỉ lấy concert đã mở bán.|
|`GET`|`/concerts/{id}`|Không yêu cầu|Xem chi tiết concert; trả 404 nếu không tồn tại.|
|`GET`|`/concerts/{id}/ticket-types`|Không yêu cầu|Lấy các loại vé của một concert.|
|`POST`|`/orders`|Bearer Token|Tạo đơn đặt vé.|
|`GET`|`/orders/me`|Bearer Token|Xem đơn hàng của người dùng hiện tại.|
|`DELETE`|`/orders/{id}`|Bearer Token|Hủy đơn hàng theo điều kiện nghiệp vụ.|
|`GET`|`/health`|Không yêu cầu|Kiểm tra trạng thái hoạt động cơ bản của API.|

Các endpoint `/orders` được bảo vệ bằng dependency xác thực dùng chung. Chi tiết request schema, response schema và mã trạng thái HTTP có thể xem trong Swagger UI tại `/docs`.

## 9\. Kiểm thử và kết quả
Dự án sử dụng pytest để kiểm thử logic đặt vé và API; đồng thời sử dụng Locust trên Kaggle Notebook để đánh giá khả năng xử lý nhiều người dùng đồng thời. Kiểm tra phân tầng cho thấy `app/services/` cần độc lập với FastAPI và SQLAlchemy.
* Kiểm thử chức năng: Bộ kiểm thử gồm 12 test trong `tests/test_order_service.py` và `tests/test_api.py`. Chạy bằng `pytest -q`; cần ghi nhận số test Passed/Failed từ lần chạy thực tế.
* Kiểm thử tải: Kịch bản Kaggle mô phỏng tối đa 200 người dùng, tăng tải 20 người/giây trong 2 phút. Kết quả cần ghi nhận gồm RPS, P50/P95/P99 latency, tỉ lệ lỗi và tính nhất quán số vé đã bán.
* Kết quả hiện tại:
**RPS (Requests Per Second):** 
**P50/P95/P99 latency:** 
**Tỉ lệ lỗi:** 
**Số vé đã bán so với tổng số vé:**
  
Đây là số liệu **trước cải tiến (baseline)** của Phase 1.

Lệnh kiểm thử chức năng:
```bash
pytest -q
```

Lệnh kiểm thử tải trên Kaggle:
```bash
!git clone https://github.com/Vyvieee-1211/Software_Architecture \&\& cd Software_Architecture
!pip install -q -r requirements.txt locust
# Kaggle không có Docker → chạy uvicorn trực tiếp
!nohup uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 > api.log 2>\&1 \&
!sleep 3 \&\& python -m scripts.seed
!locust -f loadtest/locustfile.py --host http://127.0.0.1:8000 --headless -u 200 -r 20 -t 2m --csv loadtest/result
```

### 12. Giới hạn hiện tại
* Phase 1 cung cấp backend API; chưa có giao diện Frontend.
* SQLite đang được dùng làm database mặc định, vì vậy kết quả tải không đại diện cho mọi môi trường triển khai.
* Cấu hình thử tải là cấu hình gợi ý; hiệu năng thực tế phải được xác nhận bằng kết quả chạy.
* Khả năng xử lý tranh chấp tồn kho ở mức tải cao cần được kiểm tra riêng bằng kịch bản đồng thời và đối chiếu dữ liệu.
* Các cải tiến được mô tả ở mục Phase 2 mới là định hướng cho giai đoạn tiếp theo.
  
