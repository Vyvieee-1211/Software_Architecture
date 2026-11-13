# HIT THE VIBE — Ticket hunt v2

## Kaggle

Import `kaggle_baseline.ipynb` vào Kaggle (File → Import Notebook), bật Internet,
Accelerator None, chạy ô 1 → 5. Notebook chứa snapshot mã nguồn, không phụ thuộc
GitHub đã push hay chưa. Tải ZIP kết quả trước khi kết thúc session.
Sau khi sửa source, tạo lại notebook bằng `python -m scripts.build_kaggle_notebook`.

## Chạy local

```bash
python -m pip install -r loadtest/requirements.txt
python -m loadtest.run
```

Mặc định: 200 user, 20 user/giây, 120 giây, 150 vé, cổng 8000.
Nếu cổng đang dùng, đổi `--port 8017`. Không dừng server khác để lấy cổng.
Chạy thử ngắn: `python -m loadtest.run --users 20 --spawn-rate 2 --seconds 40 --tickets 60 --port 8017`.
Kết quả local kiểm tra bộ công cụ; không thay thế số liệu Kaggle.

## Dữ liệu và hành vi

- Mỗi lượt tạo `loadtest/runs/phase1_.../concert.db`, không sửa database gốc.
- Tạo đủ 200 tài khoản `kiemthugialap001@gmail.com` → `kiemthugialap200@gmail.com`,
  mật khẩu bằng email, băm bcrypt như backend. Không gọi `/auth/register` trong tải.
- User thử dùng tài khoản riêng, đăng nhập một lần khi bắt đầu; đăng nhập vẫn tính vào tải.
- Một concert mở bán, 150 vé chia VIP 30, Standard 45, GA 75; tham số `--tickets` đổi tổng vé.
- Khởi tạo: login → concert → hạng vé. Nghỉ 0,5–2 giây sau mỗi tác vụ chính.
- Chưa có đơn: chọn xem concert (20%), xem hạng (20%), mua (60%). Mỗi lần mua 1–2 vé.
- Giữ tối đa một đơn confirmed/user. Đã mua thì xem đơn hoặc concert.
- User có số thứ tự chia hết cho 10 có ý định hủy một lần sau 5–15 giây nếu mua thành công;
  sau hủy có thể mua lại. Đây không phải cam kết 10% tổng đơn sẽ bị hủy.
- Khi hết vé, xem lại tồn kho. Không chọn concert chưa mở bán.
- POST lỗi không rõ đã commit hay chưa: chỉ đối chiếu đơn; không thử mua lại khi chưa xác định
  đơn trước đó, vì backend chưa có idempotency key. GET rỗng ngay sau timeout chưa chứng minh POST thất bại.
- Timeout kết nối/đọc 3/10 giây. Cuối thời gian tải chờ tối đa 15 giây để tác vụ hiện tại hoàn tất.
- Không chạy distributed, `--processes`, hoặc tự chạy Locust với DB tùy ý. Dùng runner để đảm bảo
  tài khoản không bị dùng trùng và luôn có manifest khớp dữ liệu.
- Backend chưa được tối ưu trong thay đổi này. Không bật WAL, giảm bcrypt hay đổi database ngầm.

## Đọc kết quả

`baseline_summary.txt` và `summary.json`: tổng hợp, số user thực sự sẵn sàng, lỗi và kiểm tra dữ liệu.
`final_stats.csv`, `final_failures.csv`, `final_exceptions.csv`: số liệu chốt sau khi dừng tải,
được dùng cho báo cáo. `result_*.csv`: số liệu định kỳ có thể thiếu vài request cuối.
`report.html`: Locust. `requests.csv`: từng request, user, trạng thái, độ trễ,
kết quả nghiệp vụ; không lưu token. `scenario.json`: đếm lỗi khởi tạo và lỗi kịch bản riêng.
`inventory_check.csv`: từng hạng, tổng vé, còn lại, vé confirmed/cancelled/từng đặt.
`resources.csv`: CPU và RAM của API/Locust; CPU process có thể vượt 100% trên máy nhiều lõi.
`api.log`, `locust.log`, các file `_tail.txt`: chẩn đoán. `metadata.json`, `source/`,
`requirements-resolved.txt`: tái lập môi trường. ZIP chứa tất cả, kể cả DB thử.

Chỉ HTTP 409 với `error=SoldOut` là từ chối nghiệp vụ hợp lệ. Nó không phải mua thành công.
Các 400/409 khác, 5xx, timeout, JSON sai cấu trúc đều là lỗi. HTTP 4xx/5xx được đếm riêng.
Không lấy median/percentile nhanh của phản hồi lỗi để kết luận API khỏe.
Số response mua thành công có thể khác số đơn trong DB khi phản hồi bị mất.
Tổng vé đã đặt - vé hoàn phải khớp vé confirmed = tổng vé - tồn kho.
Kiểm tra thêm SQLite integrity, foreign keys, trạng thái đơn và tối đa một đơn active/user.
Tồn kho khớp khi có 0 đơn không có nghĩa bài kiểm thử đạt.

Đây là mô phỏng săn vé hữu hạn: tải đặt vé giảm khi user đã mua/hết vé. Không phải bài đo
thông lượng đặt vé bền vững. API và Locust cùng máy; logging/sampling cũng tiêu thụ tài nguyên.
Random seed cố định lựa chọn riêng của user; lịch thread, khoảng nghỉ và kết quả tranh vé không xác định hoàn toàn.

Giữ cùng workload v2, thông số, dữ liệu và môi trường cho Phase 1/2; chạy >=3 lượt độc lập,
báo cáo tất cả kết quả. Không so trực tiếp với baseline cũ có đăng ký tài khoản.
`--phase`/`PHASE` chỉ đổi nhãn báo cáo; khi sửa backend Phase 2 phải tạo lại notebook để chứa mã mới.
Exit 0: Locust không ghi lỗi; exit 1: lỗi tải/kịch bản; exit 2: runner thất bại.
Luôn đọc cảnh báo và đối chiếu dữ liệu, không chỉ nhìn exit code.

## Nguồn

- https://docs.locust.io/en/stable/writing-a-locustfile.html
- https://docs.locust.io/en/stable/extending-locust.html
- https://docs.locust.io/en/stable/running-without-web-ui.html
