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
            result = self.db.execute(
                make_insert(Order)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["user_id", "idempotency_key"])
            )
            if result.rowcount != 1:
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

    def list_expired_ids(self, now: datetime, limit: int) -> list[int]:
        return list(
            self.db.scalars(
                select(Order.id)
                .where(Order.status == "held", Order.expires_at <= now)
                .order_by(Order.expires_at)
                .limit(limit)
            )
        )

    # --- Chuyển trạng thái -------------------------------------------------
    # Mỗi lần chuyển là MỘT câu UPDATE có điều kiện trên trạng thái hiện tại. Chỉ
    # request làm đổi được đúng một dòng mới được chạy tiếp (hoàn kho, xuất vé…),
    # nên hủy/hết hạn/thanh toán đồng thời không thể cùng thắng.

    def _transition(self, order_id: int, values: dict, *conditions) -> bool:
        result = self.db.execute(
            update(Order)
            .where(Order.id == order_id, Order.status == "held", *conditions)
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
