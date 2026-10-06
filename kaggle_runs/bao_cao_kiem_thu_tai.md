## 12.2. Kết quả kiểm thử tải

Báo cáo tổng hợp **3 lượt baseline** chạy bằng notebook `kaggle_baseline_fixed.ipynb` trong VS Code trên **Windows 11**. Ba lượt sử dụng cùng cấu hình và cùng mã nguồn (đã đối chiếu SHA256 trong `metadata.json`). Mỗi lượt tăng tải 20 user/giây, đợi đủ **100 người dùng sẵn sàng**, sau đó đo thêm **120 giây steady**.

Backend chạy Uvicorn **1 worker**, Python **3.14.7**, SQLite **3.50.4**; máy được ghi nhận có **16 CPU logic**. API và bộ sinh tải chạy cùng máy. Dữ liệu workload gồm một concert mở bán với **150 vé**; mỗi user nghỉ **0,5–2 giây** giữa tác vụ và giữ tối đa một đơn hoạt động. Tài khoản được tạo trước thời gian đo; đăng nhập thuộc warmup. Chromium đo trang với mạng giả lập: latency **50 ms**, tải xuống **10 Mbps**, tải lên **5 Mbps**.

**Các lượt được sử dụng** — thời gian khởi động theo múi giờ Việt Nam (UTC+7):

| Lượt | Thư mục kết quả | Thời điểm khởi động |
| --- | --- | --- |
| 1 | [phase1_nfr_10_qqx4i](kaggle_runs/phase1_nfr_10_qqx4i/metadata.json) | 06/10/2026 03:44:05 |
| 2 | [phase1_nfr_aexybsjy](kaggle_runs/phase1_nfr_aexybsjy/metadata.json) | 06/10/2026 04:06:19 |
| 3 | [phase1_nfr_h00mk3g5](kaggle_runs/phase1_nfr_h00mk3g5/metadata.json) | 06/10/2026 04:14:52 |

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

- Lượt 1: [cấu hình](kaggle_runs/phase1_nfr_10_qqx4i/metadata.json), [request raw](kaggle_runs/phase1_nfr_10_qqx4i/requests.csv), [user theo thời gian](kaggle_runs/phase1_nfr_10_qqx4i/concurrency.csv), [bảng NFR](kaggle_runs/phase1_nfr_10_qqx4i/nfr_results.json), [đo trang](kaggle_runs/phase1_nfr_10_qqx4i/pages.json), [tranh chấp](kaggle_runs/phase1_nfr_10_qqx4i/concurrency.json), [tồn kho](kaggle_runs/phase1_nfr_10_qqx4i/inventory_check.csv), [API log](kaggle_runs/phase1_nfr_10_qqx4i/api.log).
- Lượt 2: [cấu hình](kaggle_runs/phase1_nfr_aexybsjy/metadata.json), [request raw](kaggle_runs/phase1_nfr_aexybsjy/requests.csv), [user theo thời gian](kaggle_runs/phase1_nfr_aexybsjy/concurrency.csv), [bảng NFR](kaggle_runs/phase1_nfr_aexybsjy/nfr_results.json), [đo trang](kaggle_runs/phase1_nfr_aexybsjy/pages.json), [tranh chấp](kaggle_runs/phase1_nfr_aexybsjy/concurrency.json), [tồn kho](kaggle_runs/phase1_nfr_aexybsjy/inventory_check.csv), [API log](kaggle_runs/phase1_nfr_aexybsjy/api.log).
- Lượt 3: [cấu hình](kaggle_runs/phase1_nfr_h00mk3g5/metadata.json), [request raw](kaggle_runs/phase1_nfr_h00mk3g5/requests.csv), [user theo thời gian](kaggle_runs/phase1_nfr_h00mk3g5/concurrency.csv), [bảng NFR](kaggle_runs/phase1_nfr_h00mk3g5/nfr_results.json), [đo trang](kaggle_runs/phase1_nfr_h00mk3g5/pages.json), [tranh chấp](kaggle_runs/phase1_nfr_h00mk3g5/concurrency.json), [tồn kho](kaggle_runs/phase1_nfr_h00mk3g5/inventory_check.csv), [API log](kaggle_runs/phase1_nfr_h00mk3g5/api.log).
