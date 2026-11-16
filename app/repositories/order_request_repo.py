"""Hàng đợi yêu cầu đặt vé, lưu trong bảng `order_requests` (Queue-Based Load Leveling).

Dùng chính PostgreSQL làm hàng đợi bền vững: không thêm hạ tầng, và việc "lấy việc" với
"tạo đơn" nằm chung một transaction nên không có trạng thái lửng (đã tạo đơn nhưng hàng
đợi chưa biết, hoặc ngược lại).
"""
from datetime import datetime

from sqlalchemy import case, insert, select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.repositories.models import OrderRequest

_UPSERT_INSERTS = {"sqlite": sqlite.insert, "postgresql": postgresql.insert}
_DETAIL_MAX = 255


class OrderRequestRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, *, user_id: int, ticket_type_id: int, quantity: int,
               idempotency_key: str | None) -> OrderRequest | None:
        """Xếp một yêu cầu vào hàng đợi. Trả None nếu (user, key) đã có yêu cầu.

        Unique index quyết định trùng khóa, nên vẫn đúng khi các lần gửi lại cùng khóa
        đến đồng thời. Dùng RETURNING thay vì rowcount (psycopg báo -1 cho RETURNING).
        """
        values = dict(user_id=user_id, ticket_type_id=ticket_type_id, quantity=quantity,
                      idempotency_key=idempotency_key, status="pending", attempts=0)
        make_insert = _UPSERT_INSERTS.get(self.db.get_bind().dialect.name)
        if make_insert is not None:
            request_id = self.db.scalar(
                make_insert(OrderRequest)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["user_id", "idempotency_key"])
                .returning(OrderRequest.id)
            )
        else:  # DB khác: dựa vào unique index + savepoint.
            try:
                with self.db.begin_nested():
                    request_id = self.db.scalar(insert(OrderRequest).values(**values)
                                                .returning(OrderRequest.id))
            except IntegrityError:
                request_id = None
        return None if request_id is None else self.db.get(OrderRequest, request_id)

    def get_by_id(self, request_id: int) -> OrderRequest | None:
        return self.db.get(OrderRequest, request_id)

    def get_by_key(self, user_id: int, idempotency_key: str) -> OrderRequest | None:
        return self.db.scalar(select(OrderRequest).where(
            OrderRequest.user_id == user_id, OrderRequest.idempotency_key == idempotency_key))

    def claim_next(self) -> OrderRequest | None:
        """Lấy yêu cầu chờ lâu nhất và khóa nó đến hết transaction (Competing Consumers).

        SKIP LOCKED: các worker song song lấy các yêu cầu khác nhau thay vì chờ nhau.
        Worker chết giữa chừng → transaction rollback → khóa nhả, yêu cầu vẫn `pending`
        và worker khác sẽ lấy lại. (SQLite bỏ qua FOR UPDATE; khóa idempotency của đơn
        và `complete()` có điều kiện vẫn bảo đảm mỗi yêu cầu chỉ sinh một đơn.)
        """
        return self.db.scalar(
            select(OrderRequest)
            .where(OrderRequest.status == "pending")
            .order_by(OrderRequest.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )

    def _finish(self, request_id: int, values: dict) -> bool:
        result = self.db.execute(
            update(OrderRequest)
            .where(OrderRequest.id == request_id, OrderRequest.status == "pending")
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1

    def complete(self, request_id: int, order_id: int, now: datetime) -> bool:
        return self._finish(request_id, {"status": "succeeded", "order_id": order_id,
                                         "completed_at": now})

    def fail(self, request_id: int, error: str, detail: str, now: datetime) -> bool:
        return self._finish(request_id, {"status": "failed", "error": error,
                                         "detail": detail[:_DETAIL_MAX], "completed_at": now})

    def record_crash(self, request_id: int, detail: str, max_attempts: int, now: datetime) -> bool:
        """Ghi một lần worker lỗi bất ngờ; đủ `max_attempts` lần thì loại yêu cầu (failed)
        để nó không chặn mãi đầu hàng đợi."""
        exhausted = OrderRequest.attempts + 1 >= max_attempts
        return self._finish(request_id, {
            "attempts": OrderRequest.attempts + 1,
            "status": case((exhausted, "failed"), else_=OrderRequest.status),
            "error": case((exhausted, "ProcessingFailed"), else_=OrderRequest.error),
            "detail": detail[:_DETAIL_MAX],
            "completed_at": case((exhausted, now), else_=OrderRequest.completed_at),
        })
