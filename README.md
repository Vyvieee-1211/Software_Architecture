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

### 9.1. Kiểm thử chức năng

#### 9.1.1. Phương pháp và lệnh thực hiện

Kiểm thử chức năng được thực hiện trên mã nguồn tại commit `eea248c`, sử dụng hai file [tests/test_api.py](tests/test_api.py) và [tests/test_order_service.py](tests/test_order_service.py). Kết quả dưới đây ghi nhận từ lần chạy trực tiếp ngày **06/10/2026**, với **Python 3.14.3** và **pytest 9.1.1** trên Windows.

Chạy trong terminal PowerShell:

```powershell
cd D:\Study\ktpm\Software_Architecture-main
.\.venv\Scripts\python.exe -m pytest tests/test_api.py tests/test_order_service.py -v -p no:cacheprovider
```

`test_api.py` gửi request qua FastAPI TestClient và sử dụng SQLite trong bộ nhớ, khởi tạo dữ liệu riêng cho từng ca. `test_order_service.py` gọi trực tiếp `OrderService`, sử dụng repository giả và thời gian cố định để kiểm tra quy tắc nghiệp vụ. Không cần khởi động Uvicorn trước khi chạy hai file này.

Tùy chọn `-v` hiển thị kết quả từng ca; `-p no:cacheprovider` tắt cache của pytest.

#### 9.1.2. Kết quả tổng hợp

Terminal thu thập **43 ca kiểm thử** và trả về:

```text
collected 43 items
43 passed, 19 warnings in 11.60s
```

| File kiểm thử | Phạm vi | Số ca | PASSED | FAILED | Bỏ qua |
| --- | --- | ---: | ---: | ---: | ---: |
| `tests/test_api.py` | Xác thực, concert, hạng vé, đặt vé và hủy đơn qua API | 31 | 31 | 0 | 0 |
| `tests/test_order_service.py` | Quy tắc tạo đơn, giới hạn số lượng, quyền hủy và hoàn vé tại tầng service | 12 | 12 | 0 | 0 |
| **Tổng** | | **43** | **43** | **0** | **0** |

Tỷ lệ đạt của các ca đã chạy là **100%**. Có 19 cảnh báo: 1 cảnh báo Starlette về việc sử dụng HTTPX trong TestClient và 18 cảnh báo liên quan đến `datetime.utcnow()` tại `OrderService`. Các cảnh báo này không làm ca kiểm thử thất bại.

#### 9.1.3. Các trường hợp trong `test_api.py`

| Trường hợp | Nội dung đối chiếu | Số ca pytest | Kết quả |
| --- | --- | ---: | --- |
| Đăng nhập hợp lệ | HTTP 200; trả JWT với chủ sở hữu và thời hạn hợp lệ; token dùng được để xem đơn cá nhân | 1 | PASS |
| Token không hợp lệ | Token hết hạn, ký bằng khóa khác hoặc bị sửa nội dung bị từ chối trên cả GET `/orders/me`, POST `/orders` và DELETE `/orders/1`; trả 401 và giữ nguyên đơn, tồn kho | 9 | PASS |
| Đăng ký hợp lệ | HTTP 201; trả thông tin người dùng và thời điểm tạo; không trả mật khẩu hoặc password hash | 1 | PASS |
| Đăng ký trùng email | HTTP 409 với lỗi `EmailAlreadyExists` | 1 | PASS |
| Đăng nhập sai | Sai mật khẩu hoặc email không tồn tại trả 401, lỗi `InvalidCredentials` và không cấp token | 2 | PASS |
| Danh sách concert và lọc mở bán | `on_sale=true` trả đúng concert đã mở bán; không truyền bộ lọc thì trả cả concert chưa mở bán | 1 | PASS |
| Chi tiết concert | HTTP 200; nội dung trả về khớp các trường dữ liệu của concert | 1 | PASS |
| Concert không tồn tại | API chi tiết và API hạng vé cùng trả 404 với lỗi `NotFound` | 2 | PASS |
| Danh sách hạng vé công khai | HTTP 200; trả đúng hạng vé thuộc concert, các trường dữ liệu bắt buộc và lượng vé còn lại | 1 | PASS |
| Thiếu token | Xem đơn, tạo đơn và hủy đơn đều bị từ chối với HTTP 401 | 1 | PASS |
| Vòng đời đơn đặt vé | Tạo đơn `confirmed` và trừ kho; đặt vượt tồn trả `409 SoldOut`; xem đúng đơn; hủy chuyển `cancelled` và hoàn kho; hủy lần hai trả `409 InvalidState`, không hoàn thêm | 1 | PASS |
| Tạo đơn trước giờ mở bán | HTTP 400 với lỗi `SaleNotOpen`; không tạo đơn hoặc thay đổi tồn kho | 1 | PASS |
| Đặt hạng vé không tồn tại | HTTP 404 với lỗi `NotFound`; danh sách đơn và tồn kho giữ nguyên | 1 | PASS |
| Số lượng ở biên hợp lệ | Đặt 1 hoặc 10 vé trả 201, tạo đơn `confirmed` và trừ đúng số lượng | 2 | PASS |
| Số lượng không hợp lệ | Các giá trị 0, -1 và 11 trả 422 tại trường `quantity`; không tạo đơn hoặc trừ kho | 3 | PASS |
| Danh sách đơn theo người dùng | Hai tài khoản chỉ nhìn thấy đơn của chính mình | 1 | PASS |
| Hủy đơn của người khác | HTTP 403 với lỗi `Forbidden`; đơn vẫn giữ trạng thái cũ và không hoàn vé | 1 | PASS |
| Hủy đơn không tồn tại | HTTP 404 với lỗi `NotFound`; đơn đang có và tồn kho giữ nguyên | 1 | PASS |
| **Tổng** | | **31** | **31 PASS** |

Số ca được tính theo kết quả thu thập của pytest. Ví dụ, ba loại token sai kết hợp với ba API tạo thành 9 ca riêng; kiểm tra thiếu token trên ba API nằm trong một hàm test nên được tính là 1 ca.

#### 9.1.4. Các trường hợp trong `test_order_service.py`

| Hàm kiểm thử | Nội dung đối chiếu | Kết quả |
| --- | --- | --- |
| `test_create_order_ok` | Đặt 2 vé thành công, đơn ở trạng thái `confirmed`, tồn kho từ 5 còn 3 | PASS |
| `test_create_order_sold_out` | Đặt 6 vé khi chỉ còn 5 phát sinh `SoldOut`; tồn kho vẫn là 5 | PASS |
| `test_create_order_sale_not_open` | Đặt trước giờ mở bán phát sinh `SaleNotOpen` | PASS |
| `test_create_order_unknown_ticket_type` | Hạng vé không tồn tại phát sinh `NotFound` | PASS |
| `test_create_order_min_valid_quantity` | Đặt 1 vé thành công, tồn kho từ 5 còn 4 | PASS |
| `test_create_order_max_valid_quantity` | Đặt 10 vé thành công, tồn kho từ 20 còn 10 | PASS |
| `test_create_order_invalid_quantity_zero_or_negative` | Số lượng 0 và -1 đều phát sinh `ValueError`; hai giá trị được kiểm tra trong cùng 1 ca pytest | PASS |
| `test_create_order_exceeds_max_limit_per_request` | Đặt 11 vé phát sinh `ValueError` dù tồn kho còn 20 | PASS |
| `test_cancel_order_returns_tickets` | Hủy đơn chuyển trạng thái sang `cancelled`, hoàn đủ vé để tồn kho trở lại 5 | PASS |
| `test_cancel_order_of_other_user_forbidden` | Người khác hủy đơn phát sinh `Forbidden` | PASS |
| `test_cancel_twice_invalid` | Hủy đơn lần thứ hai phát sinh `InvalidState` | PASS |
| `test_cancel_order_not_found` | Hủy đơn không tồn tại phát sinh `NotFound` | PASS |

Kết quả xác nhận **43 ca chức năng trong hai file đều đạt** ở lần chạy này. Bộ test kiểm tra hành vi API và tầng service, chưa kiểm tra thao tác giao diện qua trình duyệt. Các ca hủy lặp được thực hiện tuần tự; khả năng xử lý nhiều yêu cầu mua hoặc hủy cùng lúc được đánh giá riêng trong mục 9.2.

### 9.2. Kiểm thử tải

Báo cáo tổng hợp **3 lượt baseline** chạy bằng notebook `kaggle_baseline_fixed.ipynb` trong VS Code trên **Windows 11**. Ba lượt sử dụng cùng cấu hình và cùng mã nguồn (đã đối chiếu SHA256 trong `metadata.json`). Mỗi lượt tăng tải 20 user/giây, đợi đủ **100 người dùng sẵn sàng**, sau đó đo thêm **120 giây steady**.

Backend chạy Uvicorn **1 worker**, Python **3.14.7**, SQLite **3.50.4**; máy được ghi nhận có **16 CPU logic**. API và bộ sinh tải chạy cùng máy. Dữ liệu workload gồm một concert mở bán với **150 vé**; mỗi user nghỉ **0,5–2 giây** giữa tác vụ và giữ tối đa một đơn hoạt động. Tài khoản được tạo trước thời gian đo; đăng nhập thuộc warmup. Chromium đo trang với mạng giả lập: latency **50 ms**, tải xuống **10 Mbps**, tải lên **5 Mbps**.
**Các lượt được sử dụng** — thời gian khởi động theo múi giờ Việt Nam (UTC+7):

| Lượt | Thư mục kết quả | Thời điểm khởi động |
| --- | --- | --- |
| 1 | phase1_nfr_10_qqx4i: `kaggle_runs/phase1_nfr_10_qqx4i/metadata.json` | 06/10/2026 03:44:05 |
| 2 | phase1_nfr_aexybsjy: `kaggle_runs/phase1_nfr_aexybsjy/metadata.json` | 06/10/2026 04:06:19 |
| 3 | phase1_nfr_h00mk3g5: `kaggle_runs/phase1_nfr_h00mk3g5/metadata.json` | 06/10/2026 04:14:52 |

**Kết quả workload hỗn hợp khi duy trì đủ 100 user**

| Lượt | Request | User đồng thời | Thời gian đo | HTTP 200 / 201 | 409 hết vé | Lỗi bất thường | Throughput¹ | P95² |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Lượt 1 | 9.505 | 100 | 120,19 s | 9.462 / 32 | 11 | 0 | 79,09 req/s | 19,00 ms |
| Lượt 2 | 9.506 | 100 | 120,13 s | 9.466 / 30 | 10 | 0 | 79,13 req/s | 18,13 ms |
| Lượt 3 | 9.464 | 100 | 120,07 s | 9.417 / 31 | 16 | 0 | 78,82 req/s | 19,78 ms |
| Gộp 3 lượt | 28.475 | 100 mỗi lượt | 360,39 s | 28.345 / 93 | 37 | 0 | 79,01 req/s | 19,01 ms |

Tổng cộng ghi nhận **28.475 request** trong steady, **0 lỗi bất thường** và **37 phản hồi `409 SoldOut`**. Throughput từng lượt nằm trong khoảng **78,82–79,13 req/s**. P95 gộp được tính từ toàn bộ mẫu request của ba lượt.

**Chi tiết các kịch bản của lượt mới nhất — lượt 3**

| Kịch bản | Request | User đồng thời | Thời gian đo | HTTP thành công | Không thành công³ | Throughput¹ | P95² |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Xem danh sách concert | 915 GET `/concerts` | 100 | 120,07 s | 915 (200) | 0 | 7,62 req/s | 17,03 ms |
| Lọc concert đang mở bán | 935 GET `/concerts?on_sale=true` | 100 | 120,07 s | 935 (200) | 0 | 7,79 req/s | 15,51 ms |
| Xem chi tiết concert | 959 GET `/concerts/{id}` | 100 | 120,07 s | 959 (200) | 0 | 7,99 req/s | 18,87 ms |
| Xem hạng vé | 30 GET `/concerts/{id}/ticket-types` | 100 | 120,07 s | 30 (200) | 0 | 0,25 req/s | 14,78 ms |
| Xem đơn vé của mình | 6.568 GET `/orders/me` | 100 | 120,07 s | 6.568 (200) | 0 | 54,70 req/s | 20,71 ms |
| Đặt vé | 47 POST `/orders` | 100 | 120,07 s | 31 (201) | 16 hết vé; 0 lỗi | 0,39 req/s | 25,98 ms |
| Hủy đơn vé | 10 DELETE `/orders/{id}` | 100 | 120,07 s | 10 (200) | 0 | 0,08 req/s | 33,40 ms |

Các dòng trên diễn ra **đồng thời trong cùng một workload**, nên dùng chung thời gian steady của lượt 3. Cột user phản ánh **100 phiên Locust đang sẵn sàng**; số request đang xử lý thực tế của từng endpoint thay đổi theo hành vi user.

**Đăng nhập trong giai đoạn warmup**

| Lượt | Request | Cách tăng tải | Thời gian warmup⁴ | HTTP 200 | Không thành công | Throughput⁴ | P95² |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 100 POST `/auth/login` | Tăng 20 user/s | 5,05 s | 100 | 0 | 19,80 req/s | 680,25 ms |
| 2 | 100 POST `/auth/login` | Tăng 20 user/s | 5,03 s | 100 | 0 | 19,87 req/s | 664,64 ms |
| 3 | 100 POST `/auth/login` | Tăng 20 user/s | 5,06 s | 100 | 0 | 19,75 req/s | 737,54 ms |

Mỗi lượt đăng nhập bằng **100 tài khoản riêng biệt**, mỗi tài khoản đăng nhập một lần. Các mẫu đăng nhập được trình bày riêng vì nằm ngoài steady.

**Kiểm tra tranh chấp tồn kho của lượt mới nhất**

| Kịch bản | Request | Request đồng thời cao nhất⁵ | Thời gian case⁵ | HTTP thành công | Không thành công / từ chối⁶ | Throughput⁵ | P95⁵ |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 100 người cùng mua 20 vé | 100 POST | 100 | 30,298 s | 0 (201) | 100 timeout | 3,30 req/s | 30.217,77 ms |
| Hủy 20 đơn khác nhau | 20 DELETE | 20 | 0,391 s | 20 (200) | 0 | 51,19 req/s | 317,15 ms |
| Hủy trùng cùng đơn — thử 1 | 10 DELETE | 10 | 0,073 s | 2 (200) | 8 hủy lặp (409) | 136,31 req/s | 70,26 ms |
| Hủy trùng cùng đơn — thử 2 | 10 DELETE | 10 | 0,055 s | 1 (200) | 9 hủy lặp (409) | 182,72 req/s | 48,02 ms |
| Hủy trùng cùng đơn — thử 3 | 10 DELETE | 10 | 0,052 s | 2 (200) | 8 hủy lặp (409) | 192,20 req/s | 47,88 ms |
| Hủy trùng cùng đơn — thử 4 | 10 DELETE | 10 | 0,054 s | 2 (200) | 8 hủy lặp (409) | 184,80 req/s | 49,93 ms |
| Hủy trùng cùng đơn — thử 5 | 10 DELETE | 10 | 0,069 s | 1 (200) | 9 hủy lặp (409) | 145,67 req/s | 63,38 ms |
| Mua và hủy cùng lúc | 100 POST + 10 DELETE | 110 | 30,371 s | 4 (201) + 3 (200) | 15 HTTP 500; 88 timeout | 3,62 req/s | 30.290,84 ms |

Burst mua vé của lượt 3 có **100/100 request timeout**, **0 đơn được tạo**, tồn kho của fixture vẫn **20/20 vé**. Throughput 3,30 req/s ở dòng này là tốc độ hoàn tất các request bị timeout; throughput **đặt vé thành công bằng 0**. Bài mua/hủy hỗn hợp có **4 lần mua và 3 lần hủy thành công**, **15 HTTP 500**, **88 timeout**; chỉ 3/10 đơn được yêu cầu hủy thực sự hủy thành công.

**Kết quả tranh chấp lặp lại qua ba lượt**

| Lượt | Burst mua vé | Hủy các đơn khác nhau | Hủy trùng cùng đơn | Mua/hủy hỗn hợp |
| --- | --- | --- | --- | --- |
| 1 | 100/100 timeout | 20/20 thành công | 4/5 thử nghiệm lỗi | 13 mua + 8 hủy; 15 HTTP 500 + 74 timeout |
| 2 | 100/100 timeout | 20/20 thành công | 2/5 thử nghiệm lỗi | 11 mua + 9 hủy; 14 HTTP 500 + 76 timeout |
| 3 | 100/100 timeout | 20/20 thành công | 3/5 thử nghiệm lỗi | 4 mua + 3 hủy; 15 HTTP 500 + 88 timeout |

Trong **9/15 thử nghiệm hủy trùng**, cùng một đơn nhận **2 phản hồi HTTP 200** và được hoàn vé hai lần. Fixture ban đầu có tổng 3 vé, còn 2 vé sau khi mua 1 vé; sau hủy trùng, tồn kho tăng lên **4 vé**, vượt tổng số vé của fixture. Ở lượt 3, các lần thử lỗi là **1, 3 và 4**. Đây là bằng chứng trực tiếp của lỗi cập nhật đồng thời, dù các request đó trả HTTP 200.

**Đối chiếu tồn kho của workload chính**

| Lượt | Vé từng đặt | Vé đã hoàn | Vé đang confirmed | Vé còn lại | Tổng vé | Đối chiếu |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 160 | 13 | 147 | 3 | 150 | Khớp |
| 2 | 163 | 13 | 150 | 0 | 150 | Khớp |
| 3 | 163 | 13 | 150 | 0 | 150 | Khớp |

Tổng vé từng đặt có thể lớn hơn 150 vì vé đã hủy được hoàn và bán lại. Cả ba workload chính thỏa `confirmed + remaining = total`. Các fixture tranh chấp được đối chiếu riêng; kết quả tồn kho khớp ở workload chính chưa đủ để kết luận an toàn trong mọi tình huống đồng thời.

**Đánh giá tiêu chí phi chức năng**

| NFR | Lượt 1 | Lượt 2 | Lượt 3 | Nhận xét |
| --- | --- | --- | --- | --- |
| NFR01 | PASS | PASS | PASS | 45/45 mẫu của 5 trang thành công; lớn nhất 592,855 ms < 3.000 ms trong mô hình mạng đã cấu hình. |
| NFR02 | PASS | PASS | PASS | 8.428 request xem concert không lỗi; trung bình gộp 6,630 ms < 2.000 ms. Trung bình từng lượt: 6,547 / 6,484 / 6,861 ms. |
| NFR03 | PASS | PASS | PASS | 100 user sẵn sàng xuyên suốt ít nhất 120 giây/lượt; 0 lỗi bất thường steady. Kết luận áp dụng cho workload có khoảng nghỉ 0,5–2 giây. |
| NFR04 | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | Burst 100 người mua có 100 timeout/lượt và 0 đơn thành công. Chưa đủ bằng chứng xác nhận chống bán vượt vé khi mua đồng thời. |
| NFR05 | FAIL | FAIL | FAIL | Hoàn vé hai lần khi hủy cùng một đơn ở 9/15 thử nghiệm; các fixture lỗi có remaining = 4 > total = 3. |

Các lượt thử cho thấy các trang và API đọc dữ liệu đáp ứng ngưỡng thời gian trong môi trường đã đo. Workload hỗn hợp duy trì được 100 user với throughput gộp **79,01 req/s** và P95 **19,01 ms**. Khi 100 request mua vé được phát cùng lúc, cả ba lượt đều timeout toàn bộ. Do đó kết quả NFR03 áp dụng cho hành vi Locust đã thử; chưa chứng minh năng lực xử lý 100 thao tác mua đồng thời.

`api.log` của cả ba lượt ghi nhận lỗi SQLAlchemy `QueuePool limit of size 5 overflow 10 reached` với timeout 30 giây và SQLite `database is locked` trong giai đoạn tranh chấp. Đây là các lỗi quan sát được; cần phân tích vòng đời session/transaction và tranh chấp kết nối để xác định nguyên nhân gốc. Lỗi hủy trùng tạo tồn kho sai và nguy cơ bán lại lượng vé hoàn dư. **Chưa thể kết luận hệ thống đạt đầy đủ NFR01–NFR05**.

**Quy ước tính số liệu và giới hạn diễn giải**

¹ **Throughput workload** = số request / thời gian steady thực tế, gồm cả request bị từ chối. Các endpoint cùng dùng thời gian quan sát của workload; không cộng thời gian các dòng.

² **P95 API** tính trực tiếp từ `requests.csv` bằng nearest-rank: sắp tăng dần độ trễ và lấy mẫu thứ `ceil(0,95 × N)`. Dùng trường `latency_ms` do Locust ghi nhận, gồm mọi outcome của nhóm. P95 gộp tính trên toàn bộ mẫu, không lấy trung bình các P95. Bộ đo này không cung cấp một chỉ số TTFB riêng.

³ **HTTP thành công** dùng 200 cho đọc/hủy và 201 cho tạo đơn. `409 SoldOut` là đặt vé không thành công do hết vé, được chấp nhận về nghiệp vụ và được tách khỏi lỗi bất thường trong NFR03. Ngưỡng lỗi bất thường của notebook là ≤1%; cả ba steady thực tế ghi nhận 0%.

⁴ **Thời gian warmup** tính từ mẫu đầu của `concurrency.csv` đến `steady_started_ts`. Throughput đăng nhập ở bảng này dùng số request login chia cho toàn bộ warmup, bao gồm thời gian tăng tải và các GET khởi tạo. P95 chỉ dùng mẫu login.

⁵ **Bài tranh chấp** dùng `peak_inflight` và `elapsed_ms` của từng case trong `concurrency.json`, cùng độ trễ request `elapsed_ms` do HTTPX ghi nhận. Thời gian case loại trừ tạo fixture, tạo đơn setup và bước chờ đối chiếu sau case. Peak in-flight là số request chồng lấn ở phía client, không phải số transaction chạy song song trên server.

⁶ `409 InvalidState` chỉ được chấp nhận trong bài hủy lặp cùng đơn. HTTP 200 vẫn có thể đi kèm sai nghiệp vụ: mỗi đơn chỉ được hoàn vé một lần. Bảng tranh chấp không tính các đơn setup vào request/throughput của case.

**Đối chiếu với output Locust tổng**

Các bảng hiệu năng chính ở trên chỉ dùng steady. `baseline_summary.txt` và `final_stats.csv` tổng hợp thêm warmup và drain, nên có số request, RPS và P95 khác:

| Lượt | Request toàn workload | RPS Locust toàn workload | P95 Locust (ms) | Thời gian tiến trình Locust |
| --- | --- | --- | --- | --- |
| 1 | 10.060 | 80,25 | 26 | 127,51 s |
| 2 | 10.052 | 80,24 | 25 | 127,16 s |
| 3 | 10.007 | 79,91 | 26 | 126,55 s |

RPS tổng ở bảng này được chép từ Locust; P95 của Locust dùng histogram làm tròn. Thời gian tiến trình có thêm chi phí khởi động/dừng, nên không dùng cột này để tính lại RPS do Locust xuất. Báo cáo chính dùng thời gian steady và percentile từ mẫu raw để thống nhất phương pháp giữa ba lượt.

**Nguồn dữ liệu kiểm chứng**

- Lượt 1: cấu hình: `kaggle_runs/phase1_nfr_10_qqx4i/metadata.json`, request raw: `kaggle_runs/phase1_nfr_10_qqx4i/requests.csv`, user theo thời gian: `kaggle_runs/phase1_nfr_10_qqx4i/concurrency.csv`, bảng NFR: `kaggle_runs/phase1_nfr_10_qqx4i/nfr_results.json`, đo trang: `kaggle_runs/phase1_nfr_10_qqx4i/pages.json`, tranh chấp: `kaggle_runs/phase1_nfr_10_qqx4i/concurrency.json`, tồn kho: `kaggle_runs/phase1_nfr_10_qqx4i/inventory_check.csv`, API log: `kaggle_runs/phase1_nfr_10_qqx4i/api.log`.
- Lượt 2: cấu hình: `kaggle_runs/phase1_nfr_aexybsjy/metadata.json`, request raw: `kaggle_runs/phase1_nfr_aexybsjy/requests.csv`, user theo thời gian: `kaggle_runs/phase1_nfr_aexybsjy/concurrency.csv`, bảng NFR: `kaggle_runs/phase1_nfr_aexybsjy/nfr_results.json`, đo trang: `kaggle_runs/phase1_nfr_aexybsjy/pages.json`, tranh chấp: `kaggle_runs/phase1_nfr_aexybsjy/concurrency.json`, tồn kho: `kaggle_runs/phase1_nfr_aexybsjy/inventory_check.csv`, API log: `kaggle_runs/phase1_nfr_aexybsjy/api.log`.
- Lượt 3: cấu hình: `kaggle_runs/phase1_nfr_h00mk3g5/metadata.json`, request raw: `kaggle_runs/phase1_nfr_h00mk3g5/requests.csv`, user theo thời gian: `kaggle_runs/phase1_nfr_h00mk3g5/concurrency.csv`, bảng NFR: `kaggle_runs/phase1_nfr_h00mk3g5/nfr_results.json`, đo trang: `kaggle_runs/phase1_nfr_h00mk3g5/pages.json`, tranh chấp: `kaggle_runs/phase1_nfr_h00mk3g5/concurrency.json`, tồn kho: `kaggle_runs/phase1_nfr_h00mk3g5/inventory_check.csv`, API log: `kaggle_runs/phase1_nfr_h00mk3g5/api.log`.
