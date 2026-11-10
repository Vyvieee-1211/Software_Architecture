from sqlalchemy import insert, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.repositories.models import Order

_UPSERT_INSERTS = {"sqlite": sqlite.insert, "postgresql": postgresql.insert}


class OrderRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, user_id: int, ticket_type_id: int, quantity: int) -> Order:
        order = Order(
            user_id=user_id,
            ticket_type_id=ticket_type_id,
            quantity=quantity,
            status="confirmed",
        )
        self.db.add(order)
        self.db.flush()
        return order

    def create_idempotent(
        self, user_id: int, ticket_type_id: int, quantity: int, idempotency_key: str
    ) -> Order | None:
        """Tạo đơn gắn với Idempotency-Key. Trả None nếu (user, key) đã có đơn.

        Việc kiểm tra trùng do unique index trong DB đảm nhiệm nên vẫn đúng khi hai
        request cùng key chạy song song: request đến sau chờ request trước commit,
        rồi INSERT của nó bị bỏ qua thay vì tạo đơn thứ hai.
        """
        values = dict(
            user_id=user_id,
            ticket_type_id=ticket_type_id,
            quantity=quantity,
            status="confirmed",
            idempotency_key=idempotency_key,
        )
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
        return self.get_by_idempotency_key(user_id, idempotency_key)

    def get_by_idempotency_key(self, user_id: int, idempotency_key: str) -> Order | None:
        return self.db.scalar(
            select(Order).where(Order.user_id == user_id, Order.idempotency_key == idempotency_key)
        )

    def get_by_id(self, order_id: int) -> Order | None:
        return self.db.get(Order, order_id)

    def list_by_user(self, user_id: int) -> list[Order]:
        return list(
            self.db.scalars(
                select(Order).where(Order.user_id == user_id).order_by(Order.created_at.desc())
            )
        )

    def set_status(self, order: Order, status: str) -> Order:
        order.status = status
        self.db.flush()
        return order
