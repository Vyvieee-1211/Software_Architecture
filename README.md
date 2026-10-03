🎟️ Hệ thống săn vé Concert — Concert Ticketing System
> **Phase 1:** Xây dựng backend REST API cho hệ thống đặt vé concert.  
---
1. Thông tin học phần
Nội dung	Thông tin
Tên môn học	Kiến trúc phần mềm
Mã lớp học phần	INT3105 2
Giảng viên	PGS.TS. Võ Đình Hiếu
Tên dự án	Hệ thống săn vé Concert (Concert Ticketing System)
Thành viên nhóm
STT	Họ và tên	MSSV
1	Phan Thị Hà Vy	24020370
2	Thìn Thị Thúy	24020319
3	Trần Đình Hiếu	23021555
---
2. Giới thiệu dự án
Concert Ticketing System là hệ thống cung cấp REST API phục vụ việc xem thông tin concert, tra cứu loại vé, đăng ký/đăng nhập và đặt vé trực tuyến. Client giao tiếp với backend thông qua HTTP và dữ liệu JSON.
Bài toán đặt vé concert có một số yêu cầu nghiệp vụ đáng chú ý: người dùng phải đăng nhập trước khi đặt vé; số lượng vé đặt phải hợp lệ; vé chỉ được đặt khi loại vé tồn tại và đang trong thời gian mở bán; người dùng chỉ được quản lý đơn hàng của chính mình. Khi hủy một đơn hàng hợp lệ, hệ thống cập nhật trạng thái đơn và hoàn trả số lượng vé theo logic nghiệp vụ.
Một rủi ro cần quan tâm trong hệ thống săn vé là overselling — tổng số vé được xác nhận vượt quá số vé có thể bán — đặc biệt khi có nhiều yêu cầu đặt vé đến gần như đồng thời. Phase 1 xây dựng nền tảng nghiệp vụ và bộ kiểm thử ban đầu; khả năng chịu tải, tính nhất quán khi có tranh chấp đồng thời và các cải tiến kiến trúc sẽ tiếp tục được đánh giá trong Phase 2.
Mục tiêu Phase 1
Xây dựng backend theo mô hình REST API, trao đổi dữ liệu bằng JSON.
Tổ chức mã nguồn theo kiến trúc ba tầng: API, Business và Data Access.
Tách logic nghiệp vụ khỏi framework web và thư viện cơ sở dữ liệu.
Hỗ trợ đăng ký, đăng nhập và xác thực JWT cho các API được bảo vệ.
Cung cấp các chức năng xem concert, tra cứu loại vé, đặt vé và quản lý đơn hàng.
Cung cấp tài liệu API và kiểm thử chức năng để làm cơ sở đánh giá chất lượng.
---
3. Chức năng chính
Nhóm chức năng	Mô tả
Tài khoản	Đăng ký tài khoản và đăng nhập bằng email/mật khẩu.
Xác thực	Sử dụng JWT Bearer Token để xác thực người dùng ở các API yêu cầu đăng nhập.
Tra cứu concert	Lấy danh sách concert hiện có.
Tra cứu loại vé	Xem các loại vé thuộc một concert.
Đặt vé	Tạo đơn đặt vé theo loại vé và số lượng yêu cầu; kiểm tra các điều kiện nghiệp vụ liên quan.
Xem đơn hàng	Lấy danh sách đơn hàng của người dùng đang đăng nhập.
Hủy đơn hàng	Kiểm tra quyền sở hữu và trạng thái đơn trước khi hủy; xử lý hoàn trả số lượng vé theo nghiệp vụ.
Kiểm tra dữ liệu	Dùng schema để xác thực dữ liệu request và chuẩn hóa response.
Tài liệu API	Tích hợp Swagger UI và ReDoc thông qua FastAPI/OpenAPI.
Kiểm thử	Sử dụng pytest/HTTPX cho kiểm thử tự động; có kịch bản Locust để thử tải.
---
4. Công nghệ sử dụng
Công nghệ	Vai trò trong dự án
Python 3.12	Ngôn ngữ lập trình backend.
FastAPI	Xây dựng REST API và quản lý dependency của request.
Uvicorn	ASGI server để chạy ứng dụng FastAPI.
SQLAlchemy 2.0	ORM và lớp làm việc với dữ liệu.
SQLite	Cơ sở dữ liệu mặc định của Phase 1, phù hợp cho phát triển và kiểm thử ban đầu.
Pydantic	Kiểm tra và mô hình hóa dữ liệu đầu vào/đầu ra.
PyJWT / JWT	Tạo và xác minh access token.
bcrypt	Băm mật khẩu trước khi lưu trữ.
pytest	Chạy các bài kiểm thử tự động.
HTTPX	Gửi request trong kiểm thử API.
Locust	Mô phỏng nhiều người dùng và đo hành vi API dưới tải. Cài riêng nếu chưa có trong môi trường.
Docker / Docker Compose	Đóng gói và khởi chạy ứng dụng trong môi trường nhất quán.
---
5. Kiến trúc phần mềm
Dự án áp dụng kiến trúc 3 tầng (Three-Tier Architecture). Mỗi tầng có trách nhiệm riêng, giúp giảm phụ thuộc giữa giao tiếp HTTP, logic nghiệp vụ và thao tác dữ liệu.
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
5.1. API Layer – Tầng giao tiếp
Vị trí: `app/api/`
Nhiệm vụ:
Khai báo các endpoint REST và nhận HTTP request.
Kiểm tra, chuyển đổi dữ liệu request/response bằng schema.
Quản lý dependency dùng chung như phiên làm việc với database và người dùng đã xác thực.
Xác thực JWT thông qua dependency dùng chung thay vì lặp lại logic xác thực trong từng endpoint.
Chuyển yêu cầu hợp lệ đến tầng nghiệp vụ và ánh xạ kết quả thành HTTP response.
5.2. Business Layer – Tầng nghiệp vụ
Vị trí: `app/services/`
Nhiệm vụ:
Xử lý nghiệp vụ đăng ký và đăng nhập.
Lấy thông tin concert và loại vé thông qua tầng truy cập dữ liệu.
Kiểm tra các điều kiện nghiệp vụ khi đặt vé.
Xử lý tạo, xem và hủy đơn hàng theo quy tắc của hệ thống.
Tập trung các lỗi nghiệp vụ để API Layer có thể chuyển thành phản hồi HTTP phù hợp.
Một nguyên tắc thiết kế quan trọng là tầng `services` không phụ thuộc trực tiếp vào FastAPI hoặc SQLAlchemy. Cách tổ chức này giúp kiểm thử logic nghiệp vụ độc lập và tạo điều kiện thay đổi công nghệ ở tầng ngoài trong tương lai.
5.3. Data Access Layer – Tầng truy cập dữ liệu
Vị trí: `app/repositories/`
Nhiệm vụ:
Khai báo cấu hình kết nối và phiên làm việc với database.
Định nghĩa các mô hình dữ liệu bằng SQLAlchemy ORM.
Đóng gói các thao tác truy vấn và cập nhật cho người dùng, concert, loại vé và đơn hàng.
Cung cấp giao diện truy cập dữ liệu cho tầng nghiệp vụ thông qua Repository.
5.4. Luồng xử lý đặt vé
Client gửi yêu cầu `POST /orders` kèm thông tin loại vé, số lượng và JWT Bearer Token.
API Layer xác thực người dùng và kiểm tra định dạng dữ liệu đầu vào.
Business Layer kiểm tra loại vé, thời gian mở bán và các điều kiện đặt vé.
Repository thực hiện các thao tác đọc/cập nhật dữ liệu cần thiết.
API trả kết quả đặt vé hoặc lỗi phù hợp dưới dạng JSON.
Chi tiết bảo đảm tính nguyên tử và hành vi khi có nhiều yêu cầu đồng thời cần được xác nhận bằng kiểm thử và số liệu thực tế; đây cũng là một nội dung đánh giá quan trọng cho Phase 2.
---
6. Cấu trúc thư mục
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
│   │   ├── concert_repo.py
│   │   ├── ticket_type_repo.py
│   │   ├── order_repo.py
│   │   └── user_repo.py
│   ├── services/
│   │   ├── auth_service.py
│   │   ├── concert_service.py
│   │   ├── order_service.py
│   │   └── errors.py
│   ├── config.py
│   └── main.py
├── loadtest/
│   └── locustfile.py
├── scripts/
│   └── seed.py
├── tests/
│   ├── test_api.py
│   └── test_order_service.py
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```
Vai trò của các thư mục chính
`app/api/`: định nghĩa endpoint, schema và dependency phục vụ API.
`app/services/`: chứa logic nghiệp vụ.
`app/repositories/`: thao tác với cơ sở dữ liệu thông qua SQLAlchemy.
`scripts/`: các script hỗ trợ, bao gồm khởi tạo dữ liệu mẫu.
`tests/`: kiểm thử API và logic nghiệp vụ.
`loadtest/`: kịch bản kiểm thử tải bằng Locust.
`Dockerfile`, `docker-compose.yml`: cấu hình đóng gói và khởi chạy ứng dụng.
---
7. Cài đặt và chạy dự án
7.1. Yêu cầu môi trường
Python 3.12 trở lên.
Git nếu clone mã nguồn từ repository.
Docker và Docker Compose nếu muốn chạy bằng container.
7.2. Chạy bằng Docker Compose
Bước 1: Clone repository
```bash
git clone https://github.com/Vyvieee-1211/Software_Architecture
cd Software_Architecture
```
Thay URL và tên thư mục mẫu bằng thông tin repository thực tế của nhóm.
Bước 2: Khởi chạy ứng dụng
```bash
docker compose up --build
```
API mặc định được cung cấp tại `http://localhost:8000`.
Bước 3: Khởi tạo dữ liệu mẫu
Mở terminal khác tại thư mục dự án và chạy:
```bash
docker compose exec api python -m scripts.seed
```
Bước 4: Dừng ứng dụng
Nhấn `Ctrl + C` tại terminal đang chạy hoặc thực hiện:
```bash
docker compose down
```
7.3. Chạy trực tiếp bằng Python
Bước 1: Tạo môi trường ảo
Windows PowerShell:
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```
Linux/macOS:
```bash
python3 -m venv .venv
source .venv/bin/activate
```
Bước 2: Cài đặt thư viện
```bash
python -m pip install -r requirements.txt
```
Bước 3: Khởi chạy API
```bash
uvicorn app.main:app --reload
```
Bước 4: Khởi tạo dữ liệu mẫu
Mở terminal khác, kích hoạt cùng môi trường ảo rồi chạy:
```bash
python -m scripts.seed
```
7.4. Tài liệu API
Khi ứng dụng đang chạy, truy cập:
Swagger UI: http://localhost:8000/docs
ReDoc: http://localhost:8000/redoc
Health check: http://localhost:8000/health
7.5. Cấu hình môi trường
Các biến môi trường chính:
Biến	Giá trị mặc định	Ý nghĩa
`DATABASE_URL`	`sqlite:///./concert.db`	Chuỗi kết nối database khi chạy local.
`JWT_SECRET`	Khóa mặc định phục vụ phát triển	Khóa dùng để ký JWT. Cần thay bằng giá trị bí mật riêng khi triển khai thật.
`JWT_EXPIRE_MINUTES`	`60`	Thời hạn access token tính bằng phút.
Trong Docker Compose, database mặc định được lưu tại `/data/concert.db` trong container và ánh xạ ra thư mục `data/` trên máy host. Cấu hình hiện tại đọc biến môi trường trực tiếp; không giả định rằng file `.env` sẽ tự được nạp trong mọi cách chạy.
Lưu ý bảo mật: Không dùng khóa bí mật mặc định hoặc mật khẩu demo trong môi trường production. Không commit secret thật lên GitHub.
---
8. Danh sách API
Phương thức	Endpoint	Xác thực	Mô tả
`POST`	`/auth/register`	Không yêu cầu	Đăng ký tài khoản.
`POST`	`/auth/login`	Không yêu cầu	Đăng nhập và nhận access token.
`GET`	`/concerts`	Không yêu cầu	Lấy danh sách concert.
`GET`	`/concerts/{id}/ticket-types`	Không yêu cầu	Lấy các loại vé của một concert.
`POST`	`/orders`	Bearer Token	Tạo đơn đặt vé.
`GET`	`/orders/me`	Bearer Token	Xem đơn hàng của người dùng hiện tại.
`DELETE`	`/orders/{id}`	Bearer Token	Hủy đơn hàng theo điều kiện nghiệp vụ.
`GET`	`/health`	Không yêu cầu	Kiểm tra trạng thái hoạt động cơ bản của API.
Các endpoint `/orders` được bảo vệ bằng dependency xác thực dùng chung. Chi tiết request schema, response schema và mã trạng thái HTTP có thể xem trong Swagger UI tại `/docs`.
8.1. Ví dụ sử dụng API
Các ví dụ sau giả định API đang chạy tại `localhost:8000` và dữ liệu mẫu đã được khởi tạo. Ví dụ shell dùng cú pháp Bash.
Đăng nhập và lấy token
```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"demo@example.com","password":"password123"}' \
  | python -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')
```
Xem danh sách concert
```bash
curl http://localhost:8000/concerts
```
Xem loại vé của concert có ID là 1
```bash
curl http://localhost:8000/concerts/1/ticket-types
```
Đặt 2 vé thuộc loại vé có ID là 1
```bash
curl -X POST http://localhost:8000/orders \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"ticket_type_id":1,"quantity":2}'
```
Xem đơn hàng của tài khoản đang đăng nhập
```bash
curl http://localhost:8000/orders/me \
  -H "Authorization: Bearer $TOKEN"
```
Hủy đơn hàng có ID là 1
```bash
curl -X DELETE http://localhost:8000/orders/1 \
  -H "Authorization: Bearer $TOKEN"
```
Tài khoản demo và các ID ví dụ chỉ sử dụng được nếu phù hợp với dữ liệu do `scripts/seed.py` tạo ra.
---
9. Kiểm thử chức năng
Dự án sử dụng pytest để kiểm thử tự động logic nghiệp vụ và API. Các bài kiểm thử hiện có:
`tests/test_order_service.py`: kiểm thử logic nghiệp vụ đặt vé với repository giả.
`tests/test_api.py`: kiểm thử API với SQLite in-memory.
Chạy toàn bộ bộ kiểm thử tại thư mục gốc dự án:
```bash
pytest -q            # 12 test, SQLite in-memory (xác nhận lại bằng kết quả chạy thực tế)
```
Kiểm tra ràng buộc phân tầng: lệnh sau phải không trả về kết quả nếu Business Layer không phụ thuộc trực tiếp vào FastAPI hoặc SQLAlchemy:
```bash
grep -ri "fastapi\|sqlalchemy" app/services/
```
10. Kiểm thử tải (Kaggle CPU)
Phần kiểm thử tải được thực hiện trên Kaggle Notebook, không chạy bằng Docker. Mục tiêu là mô phỏng nhiều người dùng đồng thời truy cập API và ghi nhận số liệu baseline để so sánh với các cải tiến dự kiến ở Phase 2.
Trong một notebook Kaggle, bật Internet: On, sau đó chạy các lệnh theo quy trình:
```bash
!git clone https://github.com/Vyvieee-1211/Software_Architecture && cd Software_Architecture
!pip install -q -r requirements.txt locust
# Kaggle không có Docker → chạy uvicorn trực tiếp
!nohup uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 > api.log 2>&1 &
!sleep 3 && python -m scripts.seed
!locust -f loadtest/locustfile.py --host http://127.0.0.1:8000 --headless -u 200 -r 20 -t 2m --csv loadtest/result
```
Các tham số Locust:
Tham số	Ý nghĩa
`-u 200`	Mục tiêu 200 người dùng đồng thời
`-r 20`	Tăng số người dùng với tốc độ 20 người/giây
`-t 2m`	Chạy kiểm thử trong 2 phút
`--csv loadtest/result`	Lưu kết quả vào các file CSV có tiền tố `loadtest/result`
RPS (Requests Per Second): 
P50/P95/P99 latency: 
Tỉ lệ lỗi: 
Số vé đã bán so với tổng số vé: 
Đây là số liệu trước cải tiến (baseline) của Phase 1.
11. Định hướng Phase 2 – Cải tiến chất lượng phần mềm
Phase 2 dự kiến kế thừa trực tiếp mã nguồn Phase 1, xác định một số thuộc tính chất lượng cần cải thiện và đánh giá bằng số liệu hoặc kiểm thử. Kế hoạch:
11.1. Hiệu năng và khả năng mở rộng (Performance & Scalability)
Vấn đề cần đánh giá: Khi nhiều người dùng cùng xem concert hoặc đặt vé, độ trễ và tỷ lệ lỗi có thể tăng. SQLite phù hợp với bản mẫu nhưng có những giới hạn khi có nhiều thao tác ghi đồng thời.
Hướng cải tiến dự kiến:
Dùng kết quả Locust của Phase 1 làm baseline.
Xác định endpoint có độ trễ cao và kiểm tra truy vấn database trước khi tối ưu.
Đánh giá chuyển từ SQLite sang PostgreSQL nếu cần hỗ trợ môi trường có nhiều giao dịch đồng thời.
Xem xét chỉ mục database cho các truy vấn thường xuyên sau khi kiểm tra kế hoạch truy vấn và dữ liệu thực tế.
Chỉ cân nhắc cache cho dữ liệu đọc nhiều, ít thay đổi như danh sách concert; không dùng cache thay thế cơ chế bảo đảm tính đúng đắn khi đặt vé.
Cách đánh giá: Chạy lại cùng kịch bản Locust trước và sau thay đổi; so sánh RPS, P95/P99 latency, failure rate và mức sử dụng tài nguyên. Chỉ kết luận có cải thiện khi có kết quả đo được.
11.2. Tính nhất quán và xử lý đặt vé đồng thời (Consistency & Concurrency)
Vấn đề cần đánh giá: Nhiều yêu cầu có thể cùng tranh chấp số vé còn lại. Một kiểm thử tuần tự thành công không đủ chứng minh hệ thống không oversell khi có yêu cầu đồng thời.
Hướng cải tiến dự kiến:
Thiết kế kiểm thử đồng thời cho nhiều yêu cầu đặt vé khi tồn kho thấp.
Rà soát transaction và điều kiện cập nhật tồn kho để việc kiểm tra và trừ vé được thực hiện an toàn.
Với PostgreSQL, đánh giá cập nhật có điều kiện/khóa bản ghi phù hợp với mô hình dữ liệu.
Xem xét idempotency key để hạn chế tạo đơn trùng khi client gửi lại yêu cầu do timeout.
Bổ sung kiểm tra bất biến dữ liệu: số vé xác nhận không vượt quá sức chứa được phép bán; việc hủy đơn phải hoàn trả vé đúng một lần.
Cách đánh giá: Gửi nhiều yêu cầu đồng thời trong điều kiện tồn kho xác định; sau thử nghiệm, đối chiếu tổng vé trong các đơn hợp lệ, trạng thái đơn và số lượng còn lại trong database.
11.3. Khả năng thay đổi và bảo trì (Modifiability & Maintainability)
Mục tiêu: Có thể thay đổi quy tắc đặt vé, cơ sở dữ liệu hoặc cách giao tiếp mà không phải sửa nhiều tầng cùng lúc.
Hướng cải tiến dự kiến:
Tiếp tục duy trì ranh giới API → Services → Repositories.
Giữ logic nghiệp vụ trong `app/services/`; tránh đưa quy tắc nghiệp vụ vào router hoặc câu lệnh truy vấn rải rác.
Xác định giao diện Repository rõ ràng hơn nếu xuất hiện nhiều cách triển khai hoặc cần thay database.
Tách cấu hình theo môi trường và kiểm tra việc khởi động khi thiếu cấu hình bắt buộc.
Bổ sung kiểm thử đơn vị cho các quy tắc nghiệp vụ quan trọng để giảm rủi ro khi refactor.
Cách đánh giá: Chọn một thay đổi nghiệp vụ đại diện và ghi nhận những module phải sửa, mức độ ảnh hưởng và các test cần cập nhật trước/sau khi cải tiến.
11.4. Bảo mật (Security)
Mục tiêu: Giảm rủi ro cấu hình yếu và bảo vệ dữ liệu, quyền truy cập của người dùng.
Hướng cải tiến dự kiến:
Bắt buộc cấu hình JWT secret riêng khi triển khai ngoài môi trường phát triển.
Rà soát thời hạn token, thông báo lỗi và việc xử lý dữ liệu nhạy cảm.
Kiểm thử quyền truy cập theo chủ sở hữu đơn hàng và các tình huống thiếu/sai token.
Cân nhắc giới hạn tần suất đăng nhập và các endpoint nhạy cảm nếu yêu cầu triển khai cần thiết.
Không ghi mật khẩu, token hoặc secret vào log; không đưa dữ liệu bí mật vào repository.
Cách đánh giá: Dùng checklist bảo mật và các test âm tính để xác nhận những request không được phép bị từ chối đúng cách.
11.5. Khả năng quan sát và kiểm thử (Observability & Testability)
Hướng cải tiến dự kiến:
Chuẩn hóa log cho request lỗi, lỗi nghiệp vụ và lỗi truy cập database; tránh ghi thông tin bí mật.
Bổ sung request ID hoặc correlation ID để theo dõi một yêu cầu qua các tầng.
Tách kiểm thử nghiệp vụ, kiểm thử API và kiểm thử tải thành các bước rõ ràng.
Lưu kết quả test và file CSV của Locust kèm cấu hình môi trường, thời điểm chạy và phiên bản mã nguồn.
Cách đánh giá: Kiểm tra khả năng lần theo một request lỗi qua log; so sánh tỷ lệ test thành công, mức bao phủ các trường hợp nghiệp vụ và các chỉ số tải giữa hai phiên bản.
11.6. Kế hoạch thực hiện dự kiến
Bước	Công việc	Đầu ra mong đợi
1	Chạy test và kiểm thử tải trên Phase 1	Baseline gồm kết quả test, CSV Locust và cấu hình môi trường.
2	Xác định vấn đề chất lượng ưu tiên từ bằng chứng	Danh sách vấn đề, nguyên nhân giả thuyết và thuộc tính chất lượng liên quan.
3	Chọn chiến thuật kiến trúc và triển khai thay đổi có phạm vi rõ ràng	Các thay đổi mã nguồn và giải thích quyết định thiết kế.
4	Chạy lại cùng bộ kiểm thử và kịch bản tải	Kết quả sau cải tiến có thể so sánh với baseline.
5	Tổng hợp đánh đổi và giới hạn	Báo cáo nêu cải thiện, chi phí, rủi ro còn lại và hướng tiếp theo.
Nhóm sẽ lựa chọn các hạng mục phù hợp với thời gian và kết quả đánh giá Phase 1. Không nhất thiết triển khai tất cả các hướng cải tiến cùng lúc; ưu tiên dựa trên vấn đề đo được và yêu cầu của học phần.
---
