# HIT THE VIBE frontend

Run the existing backend using the project's existing instructions, then:

```powershell
node frontend/dev-server.cjs
```

Open **http://localhost:3000**. No frontend dependencies or build step are needed.
The development server serves this directory's website assets and forwards
the existing `/concerts`, `/auth`, and `/orders` requests to the backend.
Existing booking and browsing contracts are preserved. Management adds authenticated
concert/ticket CRUD endpoints; `/auth/me` also reports administrator access.

Optional configuration:

```powershell
$env:FRONTEND_PORT = '3001'
$env:API_ORIGIN = 'http://127.0.0.1:8000'
node frontend/dev-server.cjs
```

The development server binds to localhost. It is a local preview tool, not a
production hosting configuration.

## Concert data and artwork

Event names, artists, venues, dates, ticket types, prices, remaining quantities,
and orders come from the existing API. One ticket type is selected per order,
with 1–10 tickets, matching the existing booking contract.

City filters match normalized names in `venue`; missing or ambiguous venue
information is not assigned an invented city. “Sắp diễn ra” shows future events
in chronological order. Featured events are editorial selections from the same
data, not fabricated popularity rankings. “Đang mở bán” follows `sale_open_time`;
availability is checked on the event's ticket page and by the booking API.

The concert response provides no images or descriptions. Original decorative
artwork is stored in `assets/`; it is not official event or artist imagery.
The description area explicitly shows a placeholder until content is available.

`assets/concert-night.png` was generated using the built-in imagegen tool.
The final prompt was:

> Use case: photorealistic-natural. Asset type: original local atmospheric placeholder background for HIT THE VIBE concert ticket marketplace, not a real named event. Create a cinematic ultra-wide landscape editorial photograph of a massive outdoor live music concert at night, shot from within the crowd. Dense silhouetted crowd at bottom, raised hands, a single anonymous performer seen as a small silhouette on a spectacular stage in the right half, dramatic violet laser beams, orange amber stage lighting, smoky atmosphere, realistic film grain, deep near-black shadows. Keep the left third darker with atmospheric smoke and crowd to support white concert title overlay in HTML. Rich genuine photographic texture, exciting premium festival atmosphere, high contrast and panoramic composition. No recognizable celebrity, no text, no logos, no typography, no watermark. This is generic decorative concert artwork, not official artist imagery.

`assets/city-hcm.svg`, `assets/city-hanoi.svg`, and `assets/city-dalat.svg` are
original vector illustrations for the location tiles.
# Quản lý concert

Đăng nhập tài khoản quản trị rồi chọn **Quản lý concert** trên thanh điều hướng
(hoặc mở `http://localhost:3000/#/manage`). Tạo concert cùng ít nhất một hạng vé;
các sự kiện được lưu trong database và xuất hiện trên trang khám phá.
Có thể sửa thông tin concert, thêm/sửa/xóa hạng vé và xóa concert chưa có đơn.
Thời gian nhập trong form là giờ Việt Nam (UTC+7).

Backend dùng biến môi trường `ADMIN_EMAILS` (các email cách nhau bằng dấu phẩy)
để cấp quyền quản trị; mặc định local là `demo@example.com`.
Khi triển khai, đặt `ADMIN_EMAILS` thành email quản trị của bạn, thay mật khẩu
tài khoản quản trị và cấu hình `JWT_SECRET`. Đăng ký tài khoản không tự cấp quyền quản trị.
Concert/hạng vé có lịch sử đơn (kể cả đơn đã hủy) không thể xóa;
giá hạng vé đã có đơn không thể đổi. Tổng vé không được thấp hơn số đang được đặt.

