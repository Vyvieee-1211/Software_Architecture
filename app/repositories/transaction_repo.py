"""Log dòng tiền: chỉ ghi thêm, không sửa, không xóa."""
from decimal import Decimal

from sqlalchemy import insert, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.repositories.models import Transaction

_UPSERT_INSERTS = {"sqlite": sqlite.insert, "postgresql": postgresql.insert}
_EVENT_KEY = ["provider", "payment_id", "type", "status"]


class TransactionRepository:
    def __init__(self, db: Session):
        self.db = db

    def record(self, *, order_id: int, payment_id: str, provider: str, type: str,
               status: str, amount: Decimal, raw_log: str) -> bool:
        """Ghi một sự kiện. Trả False nếu sự kiện này đã được ghi (vd. webhook gửi lặp).

        Ràng buộc duy nhất trong DB quyết định, nên hai webhook trùng đến đồng thời
        thì chỉ một cái ghi được và được xử lý tiếp.
        """
        values = dict(order_id=order_id, payment_id=payment_id, provider=provider,
                      type=type, status=status, amount=amount, raw_log=raw_log)
        make_insert = _UPSERT_INSERTS.get(self.db.get_bind().dialect.name)
        if make_insert is not None:
            # Hỏi RETURNING thay vì rowcount: với psycopg, INSERT có RETURNING báo rowcount
            # -1, nên dòng bị bỏ qua và dòng ghi được không phân biệt được bằng rowcount.
            inserted_id = self.db.scalar(
                make_insert(Transaction)
                .values(**values)
                .on_conflict_do_nothing(index_elements=_EVENT_KEY)
                .returning(Transaction.id)
            )
            return inserted_id is not None
        try:
            with self.db.begin_nested():
                self.db.execute(insert(Transaction).values(**values))
        except IntegrityError:
            return False
        return True

    def get_event(self, provider: str, payment_id: str, type: str, status: str) -> Transaction | None:
        return self.db.scalar(
            select(Transaction).where(
                Transaction.provider == provider, Transaction.payment_id == payment_id,
                Transaction.type == type, Transaction.status == status,
            )
        )

    def latest_pending_payment(self, order_id: int, provider: str | None = None) -> Transaction | None:
        return self._latest_payment(order_id, "pending", provider)

    def succeeded_payment(self, order_id: int) -> Transaction | None:
        """Giao dịch đã thu tiền của đơn (đơn `paid` có đúng một dòng như vậy)."""
        return self._latest_payment(order_id, "succeeded")

    def _latest_payment(self, order_id: int, status: str,
                        provider: str | None = None) -> Transaction | None:
        query = select(Transaction).where(Transaction.order_id == order_id,
                                          Transaction.type == "payment", Transaction.status == status)
        if provider is not None:
            query = query.where(Transaction.provider == provider)
        return self.db.scalar(query.order_by(Transaction.id.desc()).limit(1))

    def list_by_order(self, order_id: int) -> list[Transaction]:
        return list(
            self.db.scalars(
                select(Transaction).where(Transaction.order_id == order_id).order_by(Transaction.id)
            )
        )
