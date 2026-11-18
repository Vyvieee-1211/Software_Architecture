# Sử dụng DTO trong hệ thống đặt vé concert

**DTO** là viết tắt của *Data Transfer Object*, một đối tượng chứa dữ liệu cần chuyển giữa các lớp. Trong dự án, `ConcertData` và `TicketTypeData` là DTO dùng cho luồng đọc concert và hạng vé. Chúng là `dataclass(frozen=True)`, chứa các giá trị như tên, giá vé và số vé còn lại. Service tạo DTO trong lúc session còn mở để FastAPI có thể tạo phản hồi sau khi kết nối đã được trả về pool.

## Các lớp tham gia

| Thành phần | Vai trò trong luồng đọc | File |
|---|---|---|
| Model ORM `Concert`, `TicketType` | Ánh xạ bản ghi của PostgreSQL. Repository truy vấn và trả về các đối tượng này cho service. | [models.py](../app/repositories/models.py) |
| DTO `ConcertData`, `TicketTypeData` | Sao chép các trường cần đọc từ ORM thành dữ liệu độc lập với session. | [concert_data.py](../app/services/concert_data.py) |
| Schema `ConcertResponse`, `TicketTypeResponse` | Xác định hợp đồng JSON. Pydantic đọc thuộc tính trên DTO qua `from_attributes=True`. | [schemas.py](../app/api/schemas.py) |

Luồng này áp dụng cho `GET /concerts`, `GET /concerts/{concert_id}` và `GET /concerts/{concert_id}/ticket-types`. Các endpoint khai báo schema phản hồi bằng `response_model` rồi gọi service tương ứng. [concerts.py](../app/api/routers/concerts.py)

## Ví dụ đọc hạng vé

Đây là cách `ConcertService.list_ticket_types()` lấy dữ liệu và tạo DTO. [concert_service.py](../app/services/concert_service.py)

```python
def list_ticket_types(self, concert_id: int) -> list[TicketTypeData]:
    with self.protect(), self.uow_factory() as uow:
        self._find_concert(uow.concerts, concert_id)
        return [
            TicketTypeData.from_ticket_type(ticket_type)
            for ticket_type in uow.ticket_types.list_by_concert(concert_id)
        ]
```

1. FastAPI lấy `ConcertService` qua dependency `get_concert_service()`. Dependency truyền hàm tạo Unit of Work và cơ chế bảo vệ khi truy cập DB. [deps.py](../app/api/deps.py)
2. Repository lấy các hạng vé trong session của Unit of Work. `from_ticket_type()` sao chép `id`, `concert_id`, `name`, `price`, `total_quantity` và `remaining` vào DTO ngay trong khối `with`. [ticket_type_repo.py](../app/repositories/ticket_type_repo.py), [concert_data.py](../app/services/concert_data.py)
3. Python tạo xong danh sách DTO rồi mới thoát khối `with`. Unit of Work rollback giao dịch đọc và đóng session. Kết nối được trả về pool trước bước tạo JSON. [unit_of_work.py](../app/repositories/unit_of_work.py)
4. FastAPI nhận danh sách DTO và dùng `list[TicketTypeResponse]` để kiểm tra và chuyển dữ liệu thành JSON. Giá trị `remaining` trong phản hồi phản ánh thời điểm truy vấn. [concerts.py](../app/api/routers/concerts.py), [schemas.py](../app/api/schemas.py)

## Khi thêm một trường đọc mới

- Xác định trường nào API cần công khai và repository lấy trường đó bằng cách nào.
- Nếu service cần đọc trường đó từ bản ghi, cập nhật `ConcertRecord` hoặc `TicketTypeRecord` và hàm tạo DTO tương ứng. Với dữ liệu từ bảng liên quan, đọc và chuyển thành giá trị hoặc DTO con trước khi rời Unit of Work. [unit_of_work.py](../app/services/unit_of_work.py), [concert_data.py](../app/services/concert_data.py)
- Cập nhật schema phản hồi nếu trường mới phải xuất hiện trong JSON. Kiểm tra nội dung JSON và thứ tự trả kết nối cho cả ba endpoint. [schemas.py](../app/api/schemas.py), [test_api.py](../tests/test_api.py)

Kiểm thử `test_concert_reads_return_connection_before_serializing` quan sát sự kiện lấy và trả kết nối. Khi FastAPI bắt đầu tạo phản hồi, kiểm thử yêu cầu thứ tự sự kiện là `checkout` rồi `checkin`. Chạy từ thư mục gốc dự án. [test_api.py](../tests/test_api.py)

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_api.py -k concert_reads_return_connection_before_serializing -q
```
