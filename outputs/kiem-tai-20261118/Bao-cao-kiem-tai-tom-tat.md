# Báo cáo kiểm tải hệ thống đặt vé concert

Ngày 18/11/2026, bộ S0–S10 chạy **48 lượt** trên cùng máy. Mỗi cấu hình chạy ba lượt và mỗi lượt dùng một DB PostgreSQL mới. FastAPI chạy với **1 API worker**. S0 dùng một tiến trình Locust, còn các kịch bản khác dùng một master và bốn Locust worker. Pool mặc định trong bộ đo là 20 kết nối và 10 kết nối overflow. S9 thay đổi kích thước pool trong khi giữ overflow ở 10.

**Kết quả nổi bật.** Kiểm tra tồn kho sau chạy đạt ở 48/48 lượt. S1 giữ được 3.000 user trong 120 giây nhưng p95 đọc khoảng 7–8 giây. S2 giữ đúng 500 vé mỗi lượt, trong khi hơn một nửa mẫu HTTP không nhận được phản hồi. Hai kết quả này cho thấy cần đánh giá khả năng giữ đúng tồn kho và trải nghiệm phản hồi bằng các chỉ số riêng.

## 1. Kịch bản

| Mã | Tải thực hiện | Hành vi kiểm tra |
| --- | --- | --- |
| S0 | 10 user, 60 giây | Giữ vé → thanh toán mock → đọc vé có QR → hủy và hoàn kho. |
| S1 | 3.000 user, tăng 100/s, 120 giây | Đọc danh sách concert và hạng vé. User bắt đầu gọi GET ngay khi được tạo. |
| S2 | 3.000 user, tăng 200/s, 60 giây | Chờ đủ user rồi gửi yêu cầu giữ 1–4 vé mỗi đơn vào cùng một hạng có 500 vé. |
| S3 | 1.000 user, 10 phút | Xem → giữ vé → thanh toán mock → đọc vé đã cấp. Xác suất bỏ đơn 20%, mock thành công 90%. |
| S4 | 500 user, 160 giây | Giữ rồi bỏ. Thời hạn giữ 60 giây, sweeper chạy mỗi 5 giây. |
| S5 | 500 user, 120 giây | Mỗi nhóm gửi ba POST song song cùng `Idempotency-Key`, đối chiếu request ID và đơn tạo ra. |
| S6 | 200 user, 180 giây | Mỗi đơn thử 10 DELETE song song. Tối đa 100 user thử thanh toán sát hạn giữ. |
| S7 | 300 user, 120 giây | Gửi năm webhook cùng nội dung và chữ ký cho mỗi giao dịch. Kiểm tra thanh toán, phản hồi trùng và số vé đã bán. |
| S8 | 300 user, 30 phút | Lặp workload S3 và so sánh độ trễ, RAM, kết nối DB giữa 5 phút đầu và cuối. |
| S9 | 18 lượt, ba lượt mỗi cấu hình | Lặp S2 và S3 với `DB_POOL_SIZE` 5, 10 và 20. Dùng 1 API worker. |
| S10 | 1.000 user, 10 phút | Chạy workload S3, yêu cầu dừng DB ở khoảng giây 180 của steady, chờ 10 giây rồi khởi động lại. |

## 2. Hiệu năng và khả năng chịu tải

Độ trễ p50/p95/p99 dùng nearest-rank trên các mẫu `steady` trong `requests_*.csv`. Throughput là số mẫu HTTP chia thời lượng steady cấu hình. Các mẫu nội bộ `QUEUE`, `VERIFY` và `SWEEPER` được đo riêng. Khoảng trong bảng và phụ lục là min–max của chỉ số qua ba lượt, với độ trễ chính tính bằng mili giây. Số user là mức thấp nhất quan sát trong steady, lấy từ `concurrency_master.csv` hoặc `concurrency_local.csv`.

| Kịch bản / cấu hình | User thực tế | HTTP req/s | Lỗi hạ tầng (%) | Lượt ≤ 1% |
| --- | --- | --- | --- | --- |
| S0 | 10 | 37,03–37,97 | 0,00 | 3/3 |
| S1 | 3.000 | 352,87–361,82 | 0,00–0,08 | 3/3 |
| S2 | 3.000 | 76,80–81,05 | 52,33–56,16 | 0/3 |
| S3 | 1.000 | 215,11–259,80 | 0,71–1,27 | 2/3 |
| S4 | 500 | 105,47–136,31 | 0,15–0,57 | 3/3 |
| S5 | 500 | 141,72–149,23 | 14,06–15,89 | 0/3 |
| S6 | 200 | 31,06–33,54 | 30,33–31,57 | 0/3 |
| S7 | 300 | 25,63–25,81 | 10,29–10,86 | 0/3 |
| S8 | 300 | 193,22–223,38 | 0,00 | 3/3 |
| S9 flash/5 | 3.000 | 77,23–81,17 | 53,10–56,54 | 0/3 |
| S9 flash/10 | 3.000 | 75,53–76,97 | 56,67–57,83 | 0/3 |
| S9 flash/20 | 3.000 | 75,75–76,20 | 57,49–57,77 | 0/3 |
| S9 funnel/5 | 1.000 | 143,90–167,38 | 2,07–2,42 | 0/3 |
| S9 funnel/10 | 1.000 | 172,06–182,37 | 1,66–2,02 | 0/3 |
| S9 funnel/20 | 1.000 | 193,72–199,71 | 1,38–1,57 | 0/3 |
| S10 | 1.000 | 204,17–256,54 | 6,92–7,81 | 0/3 |

S1 là tải đọc cao nhất đã đo. Ở 3.000 user, cả ba lượt dưới 1% lỗi, còn p95 đọc từ **7.075 đến 7.815 ms**, vượt mốc 300 ms. S3 duy trì 1.000 user với hai lượt dưới 1% lỗi. Tỷ lệ đơn paid trên số đơn giữ thành công của S3 là 70,94–72,90%. Ở tải mở bán S2, p95 `POST /orders` khoảng **4.520–4.863 ms** và p99 khoảng **7.045–7.364 ms**, vượt hai ngưỡng tương ứng 1.000 và 2.000 ms. S10 có gián đoạn chủ đích nên cần đọc thêm kết quả theo cửa sổ sự cố ở mục 7.

[Phụ lục p50/p95/p99 và throughput từng endpoint](#phụ-lục-số-đo-theo-endpoint)

## 3. Đúng đắn dưới tranh chấp

- **48/48 lượt** qua kiểm tra SQL sau chạy. Mỗi hạng vé thỏa `remaining + Σ(quantity của đơn held/paid) = total_quantity` và số vé `available = remaining`. Có 0 vé sai liên kết/trạng thái và 0 đơn lệch số lượng vé. Mỗi bản ghi vé có tối đa một `order_id`. Kiểm tra SQL còn đối chiếu đơn gán cùng hạng và vé `sold` có QR.
- **S2** giữ đúng 500/500 vé ở cả ba lượt, `remaining = 0` và 0 vé vượt sức chứa trong snapshot kiểm kho.
- **S5–S7** kiểm tra DB không phát hiện đơn trùng theo khóa idempotency ở S5 hay giao dịch trùng ở S6/S7. S6 ghi nhận **261–300** lời gọi mock refund mỗi lượt và các giao dịch hoàn tiền được đối chiếu đều khớp một lời gọi. S7 ghi nhận **300 đơn paid và 300 vé sold mỗi lượt**. Kiểm tra HTTP vẫn ghi nhận các nhóm retry, hủy hoặc webhook có phản hồi thiếu, thể hiện trong tỷ lệ lỗi bên dưới.

Nguồn đối chiếu gồm `inventory_check.csv`, `scenario_checks.json` và log kiểm tra luồng S5–S7. Snapshot được lấy sau lượt chạy.

## 4. Phân loại lỗi

Các số đếm dưới đây cộng ba lượt cho mỗi cấu hình và lấy từ mẫu `steady`. Mã `0` biểu thị Locust không nhận được mã HTTP. Dòng log nhận diện được lỗi từ chối kết nối được đếm riêng. Một số mẫu chỉ ghi `unexpected 0` nên nguyên nhân mất phản hồi của chúng vẫn chưa xác định.

| Kịch bản / cấu hình | 503 | 5xx khác | Từ chối kết nối | Timeout nhận diện | Mất phản hồi còn lại | 400/409 nghiệp vụ được chấp nhận |
| --- | --- | --- | --- | --- | --- | --- |
| S0 | 0 | 0 | 0 | 0 | 0 | 0 |
| S1 | 0 | 0 | 35 | 0 | 0 | 0 |
| S2 | 0 | 0 | 0 | 0 | 7.719 | 0 |
| S3 | 0 | 0 | 3.011 | 0 | 805 | 83.841 |
| S4 | 0 | 0 | 0 | 0 | 184 | 0 |
| S5 | 0 | 0 | 0 | 0 | 7.932 | 0 |
| S6 | 0 | 0 | 0 | 0 | 5.465 | 5.044 |
| S7 | 0 | 0 | 0 | 0 | 971 | 0 |
| S8 | 0 | 0 | 0 | 0 | 0 | 239.807 |
| S9 flash/5 | 0 | 0 | 0 | 0 | 7.825 | 0 |
| S9 flash/10 | 0 | 0 | 0 | 0 | 7.859 | 0 |
| S9 flash/20 | 0 | 0 | 0 | 0 | 7.870 | 0 |
| S9 funnel/5 | 0 | 0 | 4.758 | 0 | 1.382 | 41.867 |
| S9 funnel/10 | 0 | 0 | 4.418 | 0 | 1.294 | 54.435 |
| S9 funnel/20 | 0 | 0 | 4.115 | 0 | 1.166 | 64.146 |
| S10 | 25.058 | 32 | 3.648 | 0 | 1.059 | 69.611 |

Các phản hồi nghiệp vụ dự kiến như `409 SoldOut`, `HoldLimitExceeded`, `InvalidState` hoặc `400 SaleClosed` được phân loại theo cặp mã HTTP và tên lỗi trong Locust. Với hàng đợi, API có thể trả `200` cho lần hỏi trạng thái và báo lỗi nghiệp vụ trong nội dung. Ví dụ S2 ghi **696** kết quả `SoldOut` qua hàng đợi trong ba lượt. Cột 400/409 đếm các phản hồi HTTP trực tiếp được chấp nhận, còn kết quả nghiệp vụ trong hàng đợi nằm trong bộ đếm của `nfr_results.json`.

## 5. Tài nguyên

Mỗi khoảng là min–max của **giá trị đỉnh từng lượt trong steady**. CPU dùng phần trăm, có thể vượt 100% khi dùng nhiều lõi. API RAM là RSS của tiến trình, DB RAM lấy từ Docker stats. Số kết nối PostgreSQL lấy từ DB thử nghiệm, bỏ kết nối dùng để lấy mẫu. Cột pool timeout đếm sự kiện từ `api.log` của cả ba lượt.

| Kịch bản / cấu hình | CPU API (%) | RAM API (MiB) | CPU DB (%) | RAM DB (MiB) | Kết nối PG | Chờ khóa PG | Deadlock PG | Pool timeout |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| S0 | 88,4–104,1 | 111,0–111,4 | 26,9–42,2 | 77,7–101,6 | 10–13 | 0 | 0 | 0 |
| S1 | 189,2–202,3 | 395,8–396,3 | 190,8–198,9 | 136,9–212,9 | 30 | 0 | 0 | 0 |
| S2 | 173,2–463,2 | 357,0–359,7 | 88,2–111,5 | 230,3–251,6 | 30 | 0–1 | 0 | 0 |
| S3 | 174,7–508,3 | 870,6–1.065,5 | 75,0–108,9 | 122,7–288,1 | 25–30 | 1–2 | 0 | 0 |
| S4 | 180,6–374,8 | 173,7–183,8 | 96,5–116,1 | 132,7–162,0 | 30 | 0–1 | 0 | 0 |
| S5 | 161,1–430,7 | 550,4–574,5 | 90,8–92,5 | 177,7–203,6 | 30 | 4–11 | 0 | 0 |
| S6 | 153,7–527,5 | 278,6–304,2 | 65,6–111,8 | 215,2–243,5 | 30 | 1–9 | 0 | 0 |
| S7 | 146,5–154,3 | 210,8–213,8 | 71,2–95,5 | 241,2–252,6 | 26–30 | 0–1 | 0 | 0 |
| S8 | 168,6–174,6 | 1.405,5–1.761,9 | 79,1–90,6 | 244,1–253,0 | 30 | 2 | 0 | 0 |
| S9 flash/5 | 163,1–367,6 | 352,8–354,8 | 122,3–132,7 | 207,2–210,5 | 15 | 0–1 | 0 | 0 |
| S9 flash/10 | 158,8–469,8 | 350,1–354,3 | 91,5–117,8 | 224,5–228,9 | 19–20 | 0 | 0 | 0 |
| S9 flash/20 | 145,0–166,3 | 347,2–354,0 | 84,3–94,3 | 251,8–260,6 | 24–30 | 0 | 0 | 0 |
| S9 funnel/5 | 184,9–188,2 | 621,7–676,1 | 149,7–161,1 | 218,8–221,8 | 15 | 1–2 | 0 | 0 |
| S9 funnel/10 | 168,3–183,2 | 712,5–793,6 | 101,2–102,6 | 238,6–241,1 | 20 | 1–2 | 0 | 0 |
| S9 funnel/20 | 165,0–344,1 | 778,0–797,9 | 68,9–100,5 | 262,9–269,1 | 26–30 | 1–2 | 0 | 0 |
| S10 | 160,1–176,2 | 841,2–996,3 | 86,3–94,0 | 149,6–269,6 | 30 | 1–2 | 0 | 0 |

**S8** có 0 lỗi HTTP. Các p95 endpoint đều giảm giữa hai cửa sổ 5 phút, nhưng p95 đọc toàn steady vẫn từ 1.095 đến 1.335 ms. RAM RSS trung vị cuối lượt cao gấp **4,55–10,07 lần** đầu lượt, còn trung vị kết nối PG giảm từ **22 xuống 20** ở cả ba lượt. Số liệu cho thấy RAM tăng trong 30 phút. Xác định rò rỉ RAM cần thêm bằng chứng. Nguồn là `resources.csv`, `pg_activity.csv` và `api.log`.

## 6. Khả năng mở rộng

S9 đánh giá riêng tác động của pool. Bảng ghi **trung bình của ba lượt** cho throughput, p95 `POST /orders` và tỷ lệ lỗi từng cấu hình. CPU DB là khoảng của các đỉnh từng lượt.

| Workload | Pool + overflow | HTTP req/s | p95 POST /orders (ms) | Lỗi HTTP (%) | CPU DB đỉnh (%) |
| --- | --- | --- | --- | --- | --- |
| Mở bán | 5 + 10 | 78,67 | 5.668,44 | 55,29 | 122,30–132,74 |
| Mở bán | 10 + 10 | 76,44 | 4.548,49 | 57,12 | 91,50–117,75 |
| Mở bán | 20 + 10 | 75,91 | 4.677,18 | 57,60 | 84,33–94,30 |
| Phễu mua vé | 5 + 10 | 151,73 | 8.933,57 | 2,26 | 149,67–161,09 |
| Phễu mua vé | 10 + 10 | 178,12 | 8.007,25 | 1,78 | 101,15–102,58 |
| Phễu mua vé | 20 + 10 | 197,18 | 7.083,81 | 1,49 | 68,86–100,55 |

Pool 20 cải thiện thông lượng phễu mua vé từ khoảng 152 lên 197 request/giây so với pool 5. Mở bán vẫn có hơn 55% lỗi HTTP trung bình ở cả ba cấu hình. Mức CPU DB trên 90% xuất hiện ở một số lượt, cần xu hướng CPU theo thời gian để định vị bão hòa. Ma trận **Uvicorn 1/2/4 worker chưa được đo**. Các kết quả này áp dụng cho 1 API worker. Nguồn là `scalability.csv`, `resources.csv` và `pg_activity.csv`.

## 7. Chịu lỗi và phục hồi

S10 dùng `fault.log` để lấy thời điểm lệnh dừng được yêu cầu, Docker xác nhận đã dừng DB và PostgreSQL trả lời lại `SELECT 1`. Ghép mốc này với thời gian hoàn tất request trong `requests_*.csv`.

| Lượt | Yêu cầu dừng → 503 có Retry-After (s) | Xác nhận dừng → 503 tiếp theo có Retry-After (s) | 503 có Retry-After trước xác nhận dừng | DB sẵn sàng → HTTP thành công đầu tiên (s) | Lỗi bất thường trong cửa sổ sự cố | Lỗi ngoài cửa sổ (%) | Bất biến kho |
| --- | --- | --- | --- | --- | --- | --- | --- |
| S10 · lượt 1 | 1,406 | 0,070 | 0 | 1,224 | 27 | 1,49 | Đúng |
| S10 · lượt 2 | 0,884 | 0,006 | 86 | 0,817 | 8 | 1,67 | Đúng |
| S10 · lượt 3 | 0,639 | 0,003 | 306 | 0,189 | 7 | 0,75 | Đúng |

Cửa sổ sự cố của bộ đánh giá kéo dài từ lúc yêu cầu dừng đến 15 giây sau khi DB sẵn sàng. Các phản hồi 503 ghi nhận trong steady đều có `Retry-After` và `api.log` có chuyển trạng thái circuit breaker mở rồi đóng ở cả ba lượt. Phản hồi thành công đầu tiên xuất hiện trong ngưỡng 15 giây ở cả ba lượt. Hai lượt vẫn vượt 1% lỗi ngoài cửa sổ sự cố. Mốc thành công đầu tiên phản ánh thời điểm API bắt đầu trả lời thành công trở lại.

## 8. Độ trễ nghiệp vụ

- **Hồi kho S4.** Mẫu SQL định kỳ trong `remaining.csv` thấy kho hồi đủ **19,51–20,21 giây** sau `expires_at` cuối. Mốc này đạt giới hạn 90 giây. Phép đo riêng `SWEEPER` từ `expires_at` đến khi Locust thấy đơn `expired` có p95 **19,38–21,57 giây**, vượt mục tiêu 10 giây. Kết quả gồm thời gian chờ lần hỏi HTTP tiếp theo.
- **Giữ vé đầu-cuối.** `POST /orders` trả `202` khi tiếp nhận. Phép đo `QUEUE` tính từ lúc gửi đến lúc Locust đọc được trạng thái cuối. p95 của các mẫu `QUEUE` là **14,97–15,98 giây** ở S2 và **18,87–26,65 giây** ở S3.
- **Webhook → vé có QR.** Bộ chạy xác nhận trạng thái `sold` và có QR khi đọc vé sau thanh toán. Log và dữ liệu DB của đợt chạy thiếu cặp timestamp webhook nhận được và QR được cấp cho từng vé, nên chưa có số đo độ trễ này. p95 của `GET /orders/{id}/tickets` trong phụ lục là độ trễ riêng của request đọc vé.

## Kết luận

Kho vé thỏa các bất biến được kiểm tra ở 48/48 lượt. Điểm cần ưu tiên đánh giá tiếp là độ trễ khi duy trì 3.000 user đọc và lượng mất phản hồi khi mở bán đồng thời. Tăng pool có tác dụng với phễu mua vé ở cấu hình hiện tại. S8 và S10 cho thấy cần đọc chỉ số tài nguyên và số lỗi theo thời gian bên cạnh tổng lỗi HTTP.

Dữ liệu gốc được tạo dưới `loadtest/results/full_dto_ramp_20261118/`. `progress.json` chỉ tới `nfr_results.json` của từng lượt.

## Phụ lục số đo theo endpoint

Mở từng mục để xem khoảng min–max qua ba lượt. Độ trễ tính bằng ms. Throughput endpoint là số mẫu của endpoint chia thời lượng steady, gồm cả các mẫu bị đánh lỗi. Với endpoint có ít mẫu hơn, cột số lượt đo cho biết phạm vi dùng để tính.

<details>
<summary>S0 · Luồng cơ bản</summary>

| Endpoint | Lượt có mẫu | Số mẫu | Req/s | p50 (ms) | p95 (ms) | p99 (ms) |
| --- | --- | --- | --- | --- | --- | --- |
| `DELETE /orders/{id} [DELETE]` | 3/3 | 316–325 | 5,27–5,42 | 38,79–39,39 | 57,95–62,66 | 118,59–147,64 |
| `GET /concerts` | 3/3 | 319–327 | 5,32–5,45 | 8,22–8,29 | 13,05–15,15 | 48,95–50,64 |
| `GET /orders/requests/{id} [GET]` | 3/3 | 317–325 | 5,28–5,42 | 15,48–15,56 | 23,00–24,67 | 34,85–45,10 |
| `GET /orders/{id}/tickets [GET]` | 3/3 | 317–325 | 5,28–5,42 | 14,22–14,48 | 21,63–24,50 | 47,69–58,52 |
| `POST /mock-gateway/pay [POST]` | 3/3 | 317–325 | 5,28–5,42 | 18,71–18,92 | 26,16–33,60 | 62,07–72,02 |
| `POST /orders [POST]` | 3/3 | 319–326 | 5,32–5,43 | 21,97–22,16 | 31,84–36,58 | 68,23–80,63 |
| `POST /orders/{id}/pay [POST]` | 3/3 | 317–325 | 5,28–5,42 | 21,52–22,10 | 29,54–35,35 | 65,93–81,47 |

</details>

<details>
<summary>S1 · Đọc concert</summary>

| Endpoint | Lượt có mẫu | Số mẫu | Req/s | p50 (ms) | p95 (ms) | p99 (ms) |
| --- | --- | --- | --- | --- | --- | --- |
| `GET /concerts` | 3/3 | 24.242–24.782 | 202,02–206,52 | 6.500,53–6.662,42 | 7.070,98–7.808,87 | 7.242,58–8.354,23 |
| `GET /concerts/{id}/ticket-types` | 3/3 | 18.102–18.636 | 150,85–155,30 | 6.504,26–6.663,70 | 7.074,76–7.814,78 | 7.238,64–8.353,72 |

</details>

<details>
<summary>S2 · Mở bán đồng loạt</summary>

| Endpoint | Lượt có mẫu | Số mẫu | Req/s | p50 (ms) | p95 (ms) | p99 (ms) |
| --- | --- | --- | --- | --- | --- | --- |
| `GET /orders/requests/{id} [GET]` | 3/3 | 1.608–1.863 | 26,80–31,05 | 612,13–861,01 | 1.544,65–1.623,39 | 1.587,04–1.658,68 |
| `POST /orders [POST flash]` | 3/3 | 3.000 | 50,00 | 2.625,47–2.764,93 | 4.520,02–4.862,52 | 7.045,30–7.364,12 |

</details>

<details>
<summary>S3 · Phễu mua vé</summary>

| Endpoint | Lượt có mẫu | Số mẫu | Req/s | p50 (ms) | p95 (ms) | p99 (ms) |
| --- | --- | --- | --- | --- | --- | --- |
| `GET /concerts` | 3/3 | 17.668–21.858 | 29,45–36,43 | 2.340,72–2.690,99 | 4.064,35–5.796,85 | 4.611,18–6.941,90 |
| `GET /concerts/{id}` | 3/3 | 17.548–21.734 | 29,25–36,22 | 2.338,23–2.672,53 | 4.056,01–5.708,14 | 4.509,71–6.828,43 |
| `GET /concerts/{id}/ticket-types` | 3/3 | 41.563–51.016 | 69,27–85,03 | 2.345,17–2.701,19 | 4.076,57–5.865,08 | 4.602,50–6.975,15 |
| `GET /orders/me` | 3/3 | 5.832–7.210 | 9,72–12,02 | 3.112,96–3.634,23 | 5.407,91–7.778,50 | 6.118,05–8.851,22 |
| `GET /orders/requests/{id} [GET]` | 3/3 | 7.056–7.582 | 11,76–12,64 | 5.315,66–6.743,61 | 5.974,00–9.108,94 | 6.214,83–9.362,43 |
| `GET /orders/{id}/tickets [GET]` | 3/3 | 3.098–3.178 | 5,16–5,30 | 5.334,88–6.839,70 | 5.941,48–9.064,70 | 6.221,07–9.312,96 |
| `POST /mock-gateway/pay [POST]` | 3/3 | 3.438–3.528 | 5,73–5,88 | 6.684,87–8.429,88 | 7.395,10–11.197,08 | 7.740,52–11.674,47 |
| `POST /orders [POST]` | 3/3 | 29.426–36.294 | 49,04–60,49 | 3.120,76–3.581,42 | 5.429,21–7.689,49 | 5.891,43–9.066,19 |
| `POST /orders/{id}/pay [POST]` | 3/3 | 3.438–3.528 | 5,73–5,88 | 6.664,91–8.596,74 | 7.434,77–11.229,56 | 7.750,12–11.683,67 |

</details>

<details>
<summary>S4 · Hết hạn giữ chỗ</summary>

| Endpoint | Lượt có mẫu | Số mẫu | Req/s | p50 (ms) | p95 (ms) | p99 (ms) |
| --- | --- | --- | --- | --- | --- | --- |
| `GET /orders/requests/{id} [GET]` | 3/3 | 1.418–1.776 | 8,86–11,10 | 1.313,05–1.640,09 | 1.797,61–2.009,05 | 1.854,07–2.042,13 |
| `GET /orders/{id} [expiry]` | 3/3 | 14.958–19.534 | 93,49–122,09 | 801,00–995,01 | 1.296,02–1.683,85 | 1.404,54–1.961,82 |
| `POST /orders [POST abandon]` | 3/3 | 500 | 3,12 | 2.160,39–2.195,54 | 6.794,36–7.930,46 | 6.977,02–8.396,36 |

</details>

<details>
<summary>S5 · Retry idempotent</summary>

| Endpoint | Lượt có mẫu | Số mẫu | Req/s | p50 (ms) | p95 (ms) | p99 (ms) |
| --- | --- | --- | --- | --- | --- | --- |
| `DELETE /orders/{id} [DELETE]` | 3/3 | 2.369–2.511 | 19,74–20,93 | 6.341,58–7.139,53 | 6.966,27–7.989,89 | 7.125,75–8.216,87 |
| `GET /orders/requests/{id} [GET]` | 3/3 | 4.505–4.977 | 37,54–41,48 | 4.139,90–4.344,45 | 4.750,14–5.339,40 | 5.369,83–6.793,66 |
| `POST /orders [POST retry x3]` | 3/3 | 10.132–10.453 | 84,43–87,11 | 4.481,62–4.907,62 | 7.785,86–8.817,09 | 8.749,55–9.500,17 |

</details>

<details>
<summary>S6 · Tranh chấp hủy và hoàn tiền</summary>

| Endpoint | Lượt có mẫu | Số mẫu | Req/s | p50 (ms) | p95 (ms) | p99 (ms) |
| --- | --- | --- | --- | --- | --- | --- |
| `DELETE /orders/{id} [DELETE x10 held]` | 3/3 | 1.980–2.000 | 11,00–11,11 | 2.051,83–2.099,22 | 7.354,16–10.183,52 | 7.764,92–10.807,78 |
| `DELETE /orders/{id} [DELETE x10 paid]` | 3/3 | 1.660–2.000 | 9,22–11,11 | 2.784,40–4.130,33 | 6.146,25–8.388,10 | 6.857,24–9.261,66 |
| `DELETE /orders/{id} [DELETE]` | 3/3 | 36–42 | 0,20–0,23 | 39,69–53,28 | 49,28–98,86 | 60,56–121,83 |
| `GET /orders/requests/{id} [GET]` | 3/3 | 729–786 | 4,05–4,37 | 1.190,97–1.920,75 | 1.592,82–3.186,76 | 1.993,65–3.269,62 |
| `GET /orders/{id} [race result]` | 3/3 | 95–100 | 0,53–0,56 | 14,97–19,82 | 18,51–60,30 | 29,43–67,84 |
| `GET /orders/{id}/tickets [GET]` | 3/3 | 36–42 | 0,20–0,23 | 13,57–18,99 | 16,28–35,26 | 20,10–46,45 |
| `POST /mock-gateway/pay [POST pay-vs-expire]` | 3/3 | 95–100 | 0,53–0,56 | 25,82–32,32 | 30,78–56,95 | 32,92–83,36 |
| `POST /mock-gateway/pay [POST]` | 3/3 | 166–200 | 0,92–1,11 | 1.903,79–3.131,88 | 2.257,08–3.669,77 | 2.431,72–3.699,26 |
| `POST /orders [POST]` | 3/3 | 500 | 2,78 | 1.430,84–2.354,42 | 2.391,51–3.845,24 | 4.113,49–5.292,73 |
| `POST /orders/{id}/pay [POST]` | 3/3 | 261–300 | 1,45–1,67 | 1.767,45–3.229,80 | 2.418,03–3.647,35 | 2.504,52–3.686,01 |

</details>

<details>
<summary>S7 · Webhook lặp</summary>

| Endpoint | Lượt có mẫu | Số mẫu | Req/s | p50 (ms) | p95 (ms) | p99 (ms) |
| --- | --- | --- | --- | --- | --- | --- |
| `GET /orders/requests/{id} [GET]` | 3/3 | 760–777 | 6,33–6,47 | 2.370,37–2.454,52 | 2.641,03–2.909,11 | 2.711,19–2.959,81 |
| `GET /orders/{id}/tickets [GET]` | 3/3 | 216–220 | 1,80–1,83 | 913,73–1.344,77 | 2.560,03–2.912,44 | 2.691,63–2.983,31 |
| `POST /orders [POST]` | 3/3 | 300–303 | 2,50–2,52 | 2.147,86–3.019,52 | 6.813,42–8.311,71 | 7.273,89–8.571,01 |
| `POST /orders/{id}/pay [POST]` | 3/3 | 300 | 2,50 | 2.745,07–3.129,29 | 3.382,32–3.733,37 | 3.407,72–3.767,26 |
| `POST /payments/webhook [POST x5]` | 3/3 | 1.500 | 12,50 | 2.722,20–3.024,11 | 5.936,61–6.470,83 | 7.249,39–7.725,68 |

</details>

<details>
<summary>S8 · Chạy kéo dài</summary>

| Endpoint | Lượt có mẫu | Số mẫu | Req/s | p50 (ms) | p95 (ms) | p99 (ms) |
| --- | --- | --- | --- | --- | --- | --- |
| `GET /concerts` | 3/3 | 50.281–58.898 | 27,93–32,72 | 446,27–696,67 | 1.088,75–1.322,91 | 1.433,17–1.767,09 |
| `GET /concerts/{id}` | 3/3 | 50.264–58.881 | 27,92–32,71 | 445,66–694,85 | 1.080,08–1.326,40 | 1.428,55–1.747,71 |
| `GET /concerts/{id}/ticket-types` | 3/3 | 117.375–137.144 | 65,21–76,19 | 451,05–702,75 | 1.094,58–1.334,55 | 1.439,15–1.765,83 |
| `GET /orders/me` | 3/3 | 16.718–19.689 | 9,29–10,94 | 590,02–928,27 | 1.431,81–1.756,09 | 1.914,98–2.316,88 |
| `GET /orders/requests/{id} [GET]` | 3/3 | 14.075–15.915 | 7,82–8,84 | 1.366,45–1.699,39 | 1.954,03–2.159,44 | 2.174,41–2.479,73 |
| `GET /orders/{id}/tickets [GET]` | 3/3 | 4.217–4.268 | 2,34–2,37 | 1.408,96–1.764,36 | 2.035,06–2.347,21 | 2.236,13–2.669,34 |
| `POST /mock-gateway/pay [POST]` | 3/3 | 4.679–4.729 | 2,60–2,63 | 1.707,20–2.183,62 | 2.179,23–2.895,04 | 2.760,14–3.536,48 |
| `POST /orders [POST]` | 3/3 | 83.689–97.838 | 46,49–54,35 | 599,67–939,44 | 1.446,44–1.777,47 | 1.926,12–2.288,18 |
| `POST /orders/{id}/pay [POST]` | 3/3 | 4.679–4.729 | 2,60–2,63 | 1.713,54–2.191,25 | 2.105,66–2.839,34 | 2.728,68–3.289,87 |

</details>

<details>
<summary>S9 · Mở bán · pool 5</summary>

| Endpoint | Lượt có mẫu | Số mẫu | Req/s | p50 (ms) | p95 (ms) | p99 (ms) |
| --- | --- | --- | --- | --- | --- | --- |
| `GET /orders/requests/{id} [GET]` | 3/3 | 1.634–1.870 | 27,23–31,17 | 912,16–983,20 | 1.829,62–2.463,16 | 1.953,06–2.511,05 |
| `POST /orders [POST flash]` | 3/3 | 3.000 | 50,00 | 2.891,92–2.966,16 | 5.116,91–6.012,36 | 8.271,48–9.248,17 |

</details>

<details>
<summary>S9 · Mở bán · pool 10</summary>

| Endpoint | Lượt có mẫu | Số mẫu | Req/s | p50 (ms) | p95 (ms) | p99 (ms) |
| --- | --- | --- | --- | --- | --- | --- |
| `GET /orders/requests/{id} [GET]` | 3/3 | 1.532–1.618 | 25,53–26,97 | 711,00–895,45 | 1.523,12–1.619,27 | 1.550,85–1.700,38 |
| `POST /orders [POST flash]` | 3/3 | 3.000 | 50,00 | 2.849,75–2.892,01 | 4.531,32–4.560,24 | 7.439,56–7.782,01 |

</details>

<details>
<summary>S9 · Mở bán · pool 20</summary>

| Endpoint | Lượt có mẫu | Số mẫu | Req/s | p50 (ms) | p95 (ms) | p99 (ms) |
| --- | --- | --- | --- | --- | --- | --- |
| `GET /orders/requests/{id} [GET]` | 3/3 | 1.545–1.572 | 25,75–26,20 | 560,62–868,33 | 1.431,96–1.843,47 | 1.454,29–1.895,62 |
| `POST /orders [POST flash]` | 3/3 | 3.000 | 50,00 | 2.859,54–2.948,41 | 4.466,55–4.906,69 | 7.314,68–8.712,89 |

</details>

<details>
<summary>S9 · Phễu mua · pool 5</summary>

| Endpoint | Lượt có mẫu | Số mẫu | Req/s | p50 (ms) | p95 (ms) | p99 (ms) |
| --- | --- | --- | --- | --- | --- | --- |
| `GET /concerts` | 3/3 | 10.893–13.175 | 18,16–21,96 | 3.337,02–4.850,61 | 6.574,66–6.906,31 | 7.328,51–8.664,54 |
| `GET /concerts/{id}` | 3/3 | 10.774–13.055 | 17,96–21,76 | 3.325,64–4.851,07 | 6.561,43–6.878,32 | 7.072,91–7.353,92 |
| `GET /concerts/{id}/ticket-types` | 3/3 | 25.238–30.594 | 42,06–50,99 | 3.336,72–4.874,59 | 6.570,99–6.924,09 | 7.352,85–8.903,68 |
| `GET /orders/me` | 3/3 | 3.651–4.339 | 6,08–7,23 | 4.379,75–6.578,91 | 8.689,11–9.172,00 | 9.386,38–10.090,24 |
| `GET /orders/requests/{id} [GET]` | 3/3 | 7.687–7.863 | 12,81–13,11 | 8.482,72–8.491,73 | 9.069,33–9.439,35 | 9.317,79–9.657,80 |
| `GET /orders/{id}/tickets [GET]` | 3/3 | 3.089–3.183 | 5,15–5,30 | 8.429,10–8.495,72 | 9.026,51–9.504,47 | 9.321,34–9.701,73 |
| `POST /mock-gateway/pay [POST]` | 3/3 | 3.437–3.528 | 5,73–5,88 | 10.541,20–10.686,81 | 11.185,44–11.781,95 | 11.458,46–12.054,58 |
| `POST /orders [POST]` | 3/3 | 17.719–21.495 | 29,53–35,83 | 4.402,30–6.564,74 | 8.728,94–9.237,71 | 9.304,10–9.755,44 |
| `POST /orders/{id}/pay [POST]` | 3/3 | 3.437–3.528 | 5,73–5,88 | 10.586,27–10.678,84 | 11.247,18–11.830,51 | 11.485,65–12.059,25 |

</details>

<details>
<summary>S9 · Phễu mua · pool 10</summary>

| Endpoint | Lượt có mẫu | Số mẫu | Req/s | p50 (ms) | p95 (ms) | p99 (ms) |
| --- | --- | --- | --- | --- | --- | --- |
| `GET /concerts` | 3/3 | 13.494–14.537 | 22,49–24,23 | 3.301,25–3.762,86 | 5.889,30–6.084,22 | 6.673,08–7.095,18 |
| `GET /concerts/{id}` | 3/3 | 13.386–14.437 | 22,31–24,06 | 3.298,80–3.754,13 | 5.857,16–6.066,50 | 6.561,57–6.801,51 |
| `GET /concerts/{id}/ticket-types` | 3/3 | 31.493–33.855 | 52,49–56,42 | 3.308,21–3.768,65 | 5.895,58–6.098,16 | 6.701,60–7.115,41 |
| `GET /orders/me` | 3/3 | 4.599–4.885 | 7,67–8,14 | 4.381,54–5.017,93 | 7.878,74–8.083,39 | 8.817,93–9.288,39 |
| `GET /orders/requests/{id} [GET]` | 3/3 | 7.705–7.865 | 12,84–13,11 | 7.229,62–7.620,37 | 8.394,72–8.701,93 | 8.782,24–9.214,48 |
| `GET /orders/{id}/tickets [GET]` | 3/3 | 3.123–3.196 | 5,21–5,33 | 7.254,68–7.643,25 | 8.574,75–8.798,89 | 8.869,33–9.259,32 |
| `POST /mock-gateway/pay [POST]` | 3/3 | 3.481–3.564 | 5,80–5,94 | 9.092,10–9.630,86 | 10.641,12–10.870,21 | 10.928,14–11.205,62 |
| `POST /orders [POST]` | 3/3 | 22.237–23.824 | 37,06–39,71 | 4.397,20–4.994,51 | 7.870,11–8.142,87 | 8.734,38–8.965,00 |
| `POST /orders/{id}/pay [POST]` | 3/3 | 3.481–3.564 | 5,80–5,94 | 9.060,24–9.557,07 | 10.688,59–10.799,91 | 10.957,63–11.438,53 |

</details>

<details>
<summary>S9 · Phễu mua · pool 20</summary>

| Endpoint | Lượt có mẫu | Số mẫu | Req/s | p50 (ms) | p95 (ms) | p99 (ms) |
| --- | --- | --- | --- | --- | --- | --- |
| `GET /concerts` | 3/3 | 15.752–16.347 | 26,25–27,25 | 3.107,29–3.334,97 | 5.222,29–5.398,06 | 6.012,01–6.131,42 |
| `GET /concerts/{id}` | 3/3 | 15.654–16.218 | 26,09–27,03 | 3.107,31–3.325,81 | 5.203,46–5.351,42 | 5.602,51–5.823,69 |
| `GET /concerts/{id}/ticket-types` | 3/3 | 36.470–37.675 | 60,78–62,79 | 3.111,01–3.341,83 | 5.242,82–5.413,72 | 5.995,96–6.253,92 |
| `GET /orders/me` | 3/3 | 5.133–5.418 | 8,55–9,03 | 4.129,25–4.441,33 | 6.923,85–7.261,74 | 7.726,91–8.084,70 |
| `GET /orders/requests/{id} [GET]` | 3/3 | 6.379–7.557 | 10,63–12,60 | 6.767,62–6.956,71 | 7.488,80–7.605,04 | 7.902,88–8.053,78 |
| `GET /orders/{id}/tickets [GET]` | 3/3 | 3.123–3.173 | 5,21–5,29 | 6.778,06–6.976,28 | 7.326,49–7.608,65 | 7.779,17–7.946,83 |
| `POST /mock-gateway/pay [POST]` | 3/3 | 3.443–3.558 | 5,74–5,93 | 8.486,32–8.712,26 | 9.353,22–9.448,08 | 9.708,63–9.755,98 |
| `POST /orders [POST]` | 3/3 | 25.895–26.619 | 43,16–44,37 | 4.139,18–4.453,26 | 6.955,71–7.170,98 | 7.549,80–7.770,01 |
| `POST /orders/{id}/pay [POST]` | 3/3 | 3.443–3.558 | 5,74–5,93 | 8.480,51–8.720,22 | 9.195,28–9.491,23 | 9.669,09–9.799,33 |

</details>

<details>
<summary>S10 · DB gián đoạn</summary>

| Endpoint | Lượt có mẫu | Số mẫu | Req/s | p50 (ms) | p95 (ms) | p99 (ms) |
| --- | --- | --- | --- | --- | --- | --- |
| `GET /concerts` | 3/3 | 16.562–21.398 | 27,60–35,66 | 2.446,17–3.004,62 | 4.137,55–6.096,34 | 4.617,85–6.863,76 |
| `GET /concerts/{id}` | 3/3 | 16.449–21.304 | 27,41–35,51 | 2.435,97–2.997,25 | 4.128,33–6.054,83 | 4.481,32–6.706,44 |
| `GET /concerts/{id}/ticket-types` | 3/3 | 38.912–49.736 | 64,85–82,89 | 2.455,67–2.997,08 | 4.150,00–6.112,49 | 4.636,96–6.870,20 |
| `GET /orders/me` | 3/3 | 5.586–7.112 | 9,31–11,85 | 3.276,58–3.977,71 | 5.509,12–8.115,71 | 6.322,12–8.838,86 |
| `GET /orders/requests/{id} [GET]` | 3/3 | 8.456–9.653 | 14,09–16,09 | 5.392,91–6.810,09 | 5.993,99–8.411,58 | 7.143,19–8.903,77 |
| `GET /orders/{id}/tickets [GET]` | 3/3 | 2.719–2.820 | 4,53–4,70 | 5.454,06–7.694,37 | 6.199,55–8.523,42 | 7.442,20–8.899,02 |
| `POST /mock-gateway/pay [POST]` | 3/3 | 3.036–3.257 | 5,06–5,43 | 6.786,94–9.522,72 | 7.474,84–10.603,45 | 8.817,49–10.905,79 |
| `POST /orders [POST]` | 3/3 | 27.362–35.276 | 45,60–58,79 | 3.286,63–4.000,15 | 5.541,15–8.101,57 | 6.018,11–8.777,39 |
| `POST /orders/{id}/pay [POST]` | 3/3 | 3.179–3.369 | 5,30–5,62 | 6.776,90–9.291,85 | 7.447,61–10.535,69 | 8.853,72–11.108,31 |

</details>
