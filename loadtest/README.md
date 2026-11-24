# Kiểm tải Phase 2 theo notebook: S0–S10

Nguồn yêu cầu: bảng **mục 2 — Danh sách kịch bản** trong `kaggle_baseline_fixed.ipynb`
(bản người dùng cung cấp trong Downloads). Không chạy các ô mã Phase 1 nhúng trong notebook.
Kịch bản dùng API hiện tại: `POST /orders` → `202 + Location` → hỏi trạng thái tới khi
`succeeded/failed`, sau đó mới thanh toán. `202` chỉ là đã nhận yêu cầu, chưa phải giữ vé thành công.
S5 theo hợp đồng hiện tại: cả ba POST trả **202**, cùng request ID, đúng một request mới
và hai `Idempotent-Replayed: true`; mô tả `200` trong notebook thuộc hợp đồng cũ.

## Cài đặt và chạy nhanh (PowerShell ở thư mục gốc)

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r loadtest/requirements.txt
# Bật Docker Desktop trước, rồi khởi động PostgreSQL:
docker compose up -d db
$env:DATABASE_URL = 'postgresql+psycopg://concert:concert@localhost:5432/concert'
$env:LOADTEST_DATABASE_URL = $env:DATABASE_URL
$env:PAYMENT_PROVIDER = 'mock'
$env:PAYMENT_WEBHOOK_SECRET = 'dev-payment-secret-change-me'
.\.venv\Scripts\python.exe -m loadtest.run S0 --start-api --prepare
```

Chọn DB thử nghiệm. `--prepare` thêm concert tương lai và hạng vé riêng cho từng lượt:
500 vé cho flash, 10.000 vé cho các workload khác, có đủ các dòng `tickets`.
Không xóa DB/volume đang có. Vì mỗi lượt dùng concert mới, việc lặp không tái dùng tồn kho đã bị tiêu thụ;
để so sánh hiệu năng chặt chẽ, tự chuẩn bị DB sạch riêng cho từng lượt như notebook yêu cầu.

`--start-api` chạy API riêng ở cổng 8000 và dừng tiến trình do runner tạo sau lượt đo.
Nếu API đã chạy, bỏ cờ này và cấu hình API trước: `PAYMENT_PROVIDER=mock`, cùng DB, cùng
`PAYMENT_WEBHOOK_SECRET`. Biến môi trường của Locust không thay đổi cấu hình API đang chạy.
S4/S6 cần API đặt `HOLD_MINUTES=1`, `HOLD_SWEEP_INTERVAL_SECONDS=5`; runner tự đặt khi dùng `--start-api`.
Khi đổi cổng, truyền đồng thời `--api-port 8001 --host http://localhost:8001`.

## Danh sách kịch bản đã triển khai

| Mã | Workload | Tải mặc định / thời gian steady | Kiểm tra chính |
|---|---|---|---|
| S0 | smoke | 10 user, 60s | Bắt buộc giữ → thanh toán thành công → đủ vé và QR; hủy hoàn kho |
| S1 | browse | 3.000 user, 100/s, 120s | Chỉ đọc concert, chi tiết, hạng vé; có thể chạy 1.000 user bằng `--users 1000` |
| S2 | flash | 3.000 user, 200/s, 60s | Đợi toàn bộ user đăng nhập, cùng gửi giữ 1–4 vé vào một hạng 500 vé; giữ đơn, không thanh toán |
| S3 | funnel | 1.000 user, 600s | Xem → giữ → 20% bỏ đơn; còn lại mock thành công 90%; kiểm tra đủ vé sold và QR |
| S4 | abandon | 500 user, 160s | Giữ một lần, chờ expired; đo lag sau expires_at, kiểm tra lag <10s |
| S5 | retry | 500 user, 120s | Ba POST cùng key song song, cùng request ID, đúng một request mới; chờ kết quả và hủy |
| S6 | contention | 200 user, 180s | Mỗi user tạo một held và một paid, hủy x10; 100 user đầu làm thêm pay vs expiry |
| S7 | replay | 300 user, 120s | Một giao dịch/user, gửi cùng body và chữ ký 5 lần trực tiếp vào `/payments/webhook`; 1 paid + 4 duplicate |
| S8 | soak | 300 user, 1.800s | Workload S3, lưu mẫu theo thời gian và tài nguyên để so đầu/cuối |
| S9 | scalability | S2 hoặc S3 | Một API worker, thử `DB_POOL_SIZE` 5, 10 và 20, ba lượt mỗi cấu hình |
| S10 | fault | 1.000 user, 600s | Workload S3; tùy chọn dừng DB ở giây steady 180, chờ 10s rồi khởi động lại |

S1 gửi request ngay khi mỗi user được tạo. S2 đợi đủ user đăng nhập rồi master phát lệnh
mở rào chung tới các worker để gửi yêu cầu giữ chỗ đồng loạt. Mẫu HTTP bắt đầu trong pha
chuẩn bị hoặc hoàn tất sau pha steady nằm riêng trong CSV và bị loại khỏi percentile báo cáo.
Thời gian `--seconds` không gồm setup. Timeout setup mặc định 600s; HTTP 20s; chờ queue 60s.
Có thể đổi bằng `SETUP_TIMEOUT_SECONDS`, `HTTP_TIMEOUT_SECONDS`, `QUEUE_TIMEOUT_SECONDS`.
S2/S4/S6/S7 là tác vụ một lần/user; user ở lại tới cuối lượt, tránh sinh lại và làm sai số lượng.

## Lệnh chạy

Docker và runner đều khởi động một API worker. Ứng dụng mặc định dùng pool 20 + 10.
Runner giữ `DB_POOL_SIZE=5` làm baseline. Ví dụ S1 ba lượt với pool 20 + 10:

```powershell
.\.venv\Scripts\python.exe -m loadtest.run S1 --start-api --prepare --db-pool-size 20 --locust-workers 4 --repeat 3
```

Muốn dữ liệu sạch hoàn toàn giữa ba lượt, dùng DB riêng cho từng lượt như `loadtest.suite`.

```powershell
# Xem cấu hình mà chưa chạy tải:
.\.venv\Scripts\python.exe -m loadtest.run S2 --dry-run
# Chạy phân tán trên Windows, runner tự tạo master và 4 worker:
.\.venv\Scripts\python.exe -m loadtest.run S1 --locust-workers 4 --repeat 3 --start-api --prepare
.\.venv\Scripts\python.exe -m loadtest.run S2 --locust-workers 4 --repeat 3 --start-api --prepare
# Thay S3 bằng S4, S5, S6, S7, S8 để chạy kịch bản tương ứng:
.\.venv\Scripts\python.exe -m loadtest.run S3 --locust-workers 4 --repeat 3 --start-api --prepare
# S9: 2 workload x 3 cấu hình x 3 lượt (cần nhiều thời gian):
.\loadtest\run_matrix.ps1
# S10 chỉ tác động dịch vụ db của Docker Compose dự án khi truyền cờ này:
.\.venv\Scripts\python.exe -m loadtest.run S10 --start-api --prepare --locust-workers 4 --inject-db-outage
```

Nếu dùng API từ Docker Compose, secret mock mặc định trong Compose là
`change-me-payment-webhook-secret`; đặt Locust `PAYMENT_WEBHOOK_SECRET` giống giá trị đó cho S7.
S10 có thao tác dừng DB thật của Compose, chỉ chạy trên môi trường kiểm thử.
Runner không tự gây lỗi khi thiếu `--inject-db-outage`.

Có thể chạy Locust trực tiếp: `$env:SCENARIO='S5'` rồi
`.\.venv\Scripts\python.exe -m locust -f loadtest/locustfile.py --headless --host http://localhost:8000 -u 10 -r 2`.
`STEADY_SECONDS` mặc định 60; nên dùng runner để nhận đúng cấu hình S0–S10.

## Kết quả và phạm vi đánh giá

Mỗi lượt có thư mục `loadtest/results/Sx_<time>_<repeat>/`:

- `metadata.json`: cấu hình tải, fixture, Python, hệ điều hành, commit, hash các file kiểm tải.
- `requests_<node>.csv`: từng HTTP request, pha setup/steady, latency, mã HTTP và lỗi;
  có thêm phép đo `QUEUE` end-to-end, `VERIFY`, `SWEEPER`.
- `concurrency_<node>.csv`: master/local ghi user sẵn sàng và user đang chạy mỗi 0,5s.
- `locust.log`, `worker_*.log`, `locust_*csv`, `report.html`: báo cáo chuẩn Locust.
- `api.log` khi dùng `--start-api`; `resources.csv` chứa mẫu Docker stats hoặc lỗi lấy mẫu.
- `inventory_check.csv` khi có `LOADTEST_DATABASE_URL`: đối chiếu kho với đơn held/paid,
  vé available/held/sold, vé gán sai đơn, số lượng vé/đơn và QR.
- `nfr_results.json`: percentile nearest-rank theo endpoint chỉ pha steady, lỗi HTTP,
  bộ đếm nghiệp vụ, kiểm tra kho và PASS/FAIL/INCONCLUSIVE.

Chỉ các lỗi nghiệp vụ được định danh và có mã HTTP đúng mới được chấp nhận;
400/409 bất kỳ không tự được tính thành công. Queue báo lỗi bất ngờ, quá hạn chờ,
kiểm tra retry/webhook sai hoặc script exception đều được ghi nhận.
HTTP throughput cần đọc Locust stats theo HTTP method; không cộng QUEUE/VERIFY/SWEEPER vào RPS HTTP.

`scenario_checks.json` ghi phép đối chiếu chi tiết. S4 lấy mẫu trực tiếp `remaining` qua SQL
trong `remaining.csv`, đo thời gian hồi kho sau expires_at. S6 đếm lời gọi mock refund từ
log của điểm chạy `loadtest.instrumented:app`; adapter nghiệp vụ giữ nguyên. S7 kiểm tra
transactions trùng và tổng vé sold qua SQL; không hủy các đơn này sau khi nhận vé.
`resources.csv` có CPU/RSS của cả cây tiến trình API và Docker stats; `pg_activity.csv`
có số kết nối, trạng thái active/idle, chờ khóa và deadlock của riêng DB thử nghiệm.
S8 đối chiếu p95 từng endpoint giữa 5 phút đầu/cuối; với RAM và kết nối, quy ước ổn định
quan sát là trung vị RAM tăng <=20%, trung vị kết nối tăng <=2. Các quy ước này không phải
bằng chứng tuyệt đối không leak; vượt quy ước thì cần phân tích thêm, không tự kết luận leak.
S10 ghi các mốc stop/start/DB ready, kiểm tra 503 kèm Retry-After, phục hồi <15s và log
circuit breaker mở/đóng. 503 hợp lệ trong cửa sổ gây lỗi được thống kê riêng; timeout,
status bất ngờ hoặc lỗi ngoài cửa sổ vẫn làm FAIL. Tỷ lệ paid/held mục tiêu 72%, dung sai
đo ±5 điểm phần trăm, chỉ xét khi có ít nhất 100 lượt giữ thành công.
S6 đặt race sát expires_at; kết quả hợp lệ chưa bảo đảm hai tiến trình thực sự tranh cùng khóa.
Không có PostgreSQL/không có mẫu/thiếu lượt nghiệp vụ thì không tự kết luận PASS.
Các ngưỡng và tải là mục tiêu trong notebook, không phải khả năng chịu tải đã được chứng minh.

## Kiểm tra bộ kịch bản

```powershell
New-Item -ItemType Directory -Force loadtest/results | Out-Null
.\.venv\Scripts\python.exe -m pytest loadtest/tests -q --basetemp loadtest/results/check_temp
```

Các bài này chạy API thật và Locust thật ở tải nhỏ trên SQLite tạm để kiểm tra script,
bao gồm master + 2 worker; không thay thế S0–S10 trên PostgreSQL.
Tham khảo API sự kiện/phân tán: https://docs.locust.io/en/stable/api.html.

## Bộ chuẩn ba lượt mỗi kịch bản theo ảnh người dùng cung cấp

```powershell
$env:DATABASE_URL = 'postgresql+psycopg://concert:concert@localhost:5432/concert'
.\.venv\Scripts\python.exe -m loadtest.suite --output loadtest/results/full_standard_20261117
```

Bộ chuẩn gồm 48 lượt với một API worker và pool 20 + 10. S0–S8 và S10 mỗi kịch bản ba lượt. S9 gồm hai workload
× ba kích thước pool × ba lượt. Mỗi lượt tạo một DB PostgreSQL mới và seed riêng, giữ DB cũ để tra cứu bằng chứng;
không cần xóa volume đang có để đạt điều kiện dữ liệu sạch. S0 dùng một tiến trình Locust;
các kịch bản còn lại dùng một master và bốn worker. S6 là lượt chạy HTTP độc lập với các
workload khác, do các task Locust điều phối, không chạy pytest trong pha đo.

Tài khoản được seed với bcrypt chuẩn trước pha đo và vẫn đăng nhập qua API thật. Mỗi worker
chỉ xác thực tối đa hai user cùng lúc, thời gian setup tối đa 1.800s. Sau khi đủ user sẵn sàng,
master phát lệnh mở rào chung; pha steady giữ nguyên tải và thời lượng trong bảng.
Các workload funnel chờ queue rỗng và sweeper hết các đơn bỏ sau khi ngừng tải (drain tối đa
900s); mẫu drain không tính vào percentile steady. Vì vậy toàn bộ bộ chuẩn mất nhiều giờ.
`progress.json` và `summary.md` được cập nhật sau mỗi lượt; S9 có `scalability.csv` và
`scalability.svg` vẽ trung bình throughput và p95 POST /orders theo kích thước pool.
Tiếp tục bộ bị ngắt bằng `--resume` nếu kế hoạch trùng khớp. Dùng thư mục đầu ra mới cho
bộ chạy theo ma trận một API worker. Đặt file `stop-after-current` trong thư mục bộ chạy
để ngừng sau lượt hiện tại. FAIL vẫn được giữ và bộ chạy tiếp tục sang lượt kế tiếp.
