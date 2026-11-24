from datetime import datetime

from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.repositories.models import Order

_UPSERT_INSERTS = {"sqlite": sqlite.insert, "postgresql": postgresql.insert}


class OrderRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, **fields) -> Order:
        """Tạo đơn giữ chỗ. fields: user_id, ticket_type_id, quantity, unit_price,
        total_amount, expires_at."""
        order = Order(status="held", **fields)
        self.db.add(order)
        self.db.flush()
        return order

    def create_idempotent(self, idempotency_key: str, **fields) -> Order | None:
        """Tạo đơn gắn với Idempotency-Key. Trả None nếu (user, key) đã có đơn.

        Việc kiểm tra trùng do unique index trong DB đảm nhiệm nên vẫn đúng khi hai
        request cùng key chạy song song: request đến sau chờ request trước commit,
        rồi INSERT của nó bị bỏ qua thay vì tạo đơn thứ hai.
        """
        values = dict(status="held", idempotency_key=idempotency_key, **fields)
        make_insert = _UPSERT_INSERTS.get(self.db.get_bind().dialect.name)
        if make_insert is not None:
            # Hỏi RETURNING thay vì rowcount (psycopg báo -1 cho INSERT có RETURNING).
            inserted_id = self.db.scalar(
                make_insert(Order)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["user_id", "idempotency_key"])
                .returning(Order.id)
            )
            if inserted_id is None:
                return None
        else:  # DB khác: dựa vào unique index + savepoint.
            try:
                with self.db.begin_nested():
                    self.db.execute(insert(Order).values(**values))
            except IntegrityError:
                return None
        return self.get_by_idempotency_key(fields["user_id"], idempotency_key)

    def get_by_idempotency_key(self, user_id: int, idempotency_key: str) -> Order | None:
        return self.db.scalar(
            select(Order).where(Order.user_id == user_id, Order.idempotency_key == idempotency_key)
        )

    def get_by_id(self, order_id: int) -> Order | None:
        return self.db.get(Order, order_id)

    def list_by_user(self, user_id: int) -> list[Order]:
        return list(
            self.db.scalars(
                select(Order)
                .where(Order.user_id == user_id)
                .order_by(Order.created_at.desc(), Order.id.desc())
            )
        )

    def held_quantity(self, user_id: int, now: datetime) -> int:
        """Tổng số vé user đang giữ tạm và còn hạn."""
        return self.db.scalar(
            select(func.coalesce(func.sum(Order.quantity), 0)).where(
                Order.user_id == user_id, Order.status == "held", Order.expires_at > now
            )
        )

    def expire_overdue(self, now: datetime, limit: int) -> list:
        """Chuyển tối đa `limit` đơn `held` đã quá hạn sang `expired` bằng MỘT câu lệnh,
        trả về (id, ticket_type_id, quantity) của đúng các đơn vừa đổi để hoàn kho.

        `FOR UPDATE SKIP LOCKED` (PostgreSQL) bỏ qua đơn đang bị transaction khác khóa
        (đang thanh toán/hủy, hoặc sweeper ở tiến trình khác đang xử lý), nên nhiều sweeper
        chạy song song không chờ nhau. Điều kiện `status = 'held' AND expires_at <= now`
        lặp lại trong UPDATE giữ nguyên bảo đảm "mỗi đơn chỉ chuyển trạng thái một lần".
        SQLite bỏ qua FOR UPDATE; ghi ở SQLite vốn tuần tự.
        """
        overdue = (Order.status == "held", Order.expires_at <= now)
        candidates = (
            select(Order.id).where(*overdue).order_by(Order.expires_at).limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(self.db.execute(
            update(Order)
            .where(Order.id.in_(candidates), *overdue)
            .values(status="expired")
            .returning(Order.id, Order.ticket_type_id, Order.quantity)
            .execution_options(synchronize_session=False)
        ).all())

    # --- Chuyển trạng thái -------------------------------------------------
    # Mỗi lần chuyển là MỘT câu UPDATE có điều kiện trên trạng thái hiện tại. Chỉ
    # request làm đổi được đúng một dòng mới được chạy tiếp (hoàn kho, xuất vé…),
    # nên hủy/hết hạn/thanh toán đồng thời không thể cùng thắng.

    def _transition(self, order_id: int, values: dict, *conditions, from_status: str = "held") -> bool:
        result = self.db.execute(
            update(Order)
            .where(Order.id == order_id, Order.status == from_status, *conditions)
            .values(**values)
            .execution_options(synchronize_session="fetch")
        )
        return result.rowcount == 1

    def cancel_hold(self, order_id: int, user_id: int) -> bool:
        return self._transition(order_id, {"status": "cancelled"}, Order.user_id == user_id)

    def mark_paid(self, order_id: int, now: datetime) -> bool:
        """Chốt đơn nếu vẫn đang giữ chỗ và CHƯA quá hạn."""
        return self._transition(order_id, {"status": "paid", "paid_at": now}, Order.expires_at > now)

    def mark_failed(self, order_id: int) -> bool:
        return self._transition(order_id, {"status": "failed"})

    def mark_expired(self, order_id: int, now: datetime) -> bool:
        return self._transition(order_id, {"status": "expired"}, Order.expires_at <= now)

    def mark_refunded(self, order_id: int, user_id: int) -> bool:
        """Chuyển đơn ĐÃ THANH TOÁN sang `refunded` (khách hủy, hoàn toàn bộ tiền)."""
        return self._transition(order_id, {"status": "refunded"}, Order.user_id == user_id,
                                from_status="paid")
