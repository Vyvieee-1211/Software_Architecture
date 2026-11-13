from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    orders: Mapped[list["Order"]] = relationship(back_populates="user")


class Concert(Base):
    __tablename__ = "concerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    artist: Mapped[str | None] = mapped_column(String(255))
    venue: Mapped[str | None] = mapped_column(String(255))
    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    sale_open_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    ticket_types: Mapped[list["TicketType"]] = relationship(back_populates="concert")


class TicketType(Base):
    """Hạng vé và bộ đếm tồn kho. `remaining` = số vé chưa bị giữ và chưa bán."""

    __tablename__ = "ticket_types"
    __table_args__ = (
        CheckConstraint("remaining >= 0", name="ck_remaining_non_negative"),
        # Chốt chặn cuối: hoàn kho thừa (hoàn hai lần) bị DB từ chối.
        CheckConstraint("remaining <= total_quantity", name="ck_remaining_within_total"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    concert_id: Mapped[int] = mapped_column(ForeignKey("concerts.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    total_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    remaining: Mapped[int] = mapped_column(Integer, nullable=False)

    concert: Mapped["Concert"] = relationship(back_populates="ticket_types")


class Order(Base):
    """Đơn giữ chỗ: gom các vé khách đang chọn, tổng tiền và hạn giữ chỗ.

    Trạng thái: held → paid | expired | cancelled | failed.
    """

    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_quantity_positive"),
        # Chốt chặn cuối cùng chống tạo đơn trùng khi client retry: mỗi user chỉ có
        # tối đa một đơn cho mỗi Idempotency-Key. NULL (không gửi key) không bị ràng buộc.
        Index("uq_orders_user_idempotency_key", "user_id", "idempotency_key", unique=True),
        Index("ix_orders_status_expires_at", "status", "expires_at"),  # quét đơn quá hạn
        Index("ix_orders_user_status", "user_id", "status"),           # đếm vé đang giữ
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    ticket_type_id: Mapped[int] = mapped_column(ForeignKey("ticket_types.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    # Giá chốt tại thời điểm giữ chỗ; đổi giá hạng vé về sau không ảnh hưởng đơn đã tạo.
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime)
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="orders")
    ticket_type: Mapped["TicketType"] = relationship()


class Ticket(Base):
    """Từng vé cụ thể, sinh sẵn khi tạo hạng vé.

    Trạng thái: available (order_id rỗng) → held → sold; quay về available khi hoàn kho.
    `version` là khóa lạc quan: mọi UPDATE qua ORM kèm `WHERE version = :cũ`.
    """

    __tablename__ = "tickets"
    __table_args__ = (
        UniqueConstraint("ticket_type_id", "seat_no", name="uq_tickets_type_seat"),
        Index("ix_tickets_type_status", "ticket_type_id", "status", "id"),
        Index("ix_tickets_order", "order_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_type_id: Mapped[int] = mapped_column(ForeignKey("ticket_types.id"), nullable=False)
    seat_no: Mapped[str] = mapped_column(String(20), nullable=False)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="available")
    # Mã QR có chữ ký, chỉ sinh khi vé được xuất (đơn đã thanh toán).
    qr_code: Mapped[str | None] = mapped_column(String(128), unique=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": version}


class Transaction(Base):
    """Log dòng tiền, chỉ ghi thêm. Mỗi sự kiện của cổng thanh toán là một dòng.

    type: payment | refund; status: pending | succeeded | failed.
    Ràng buộc duy nhất giúp webhook gửi lặp không được ghi (và xử lý) hai lần.
    """

    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("provider", "payment_id", "type", "status", name="uq_transactions_event"),
        Index("ix_transactions_order", "order_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False)
    payment_id: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    raw_log: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
