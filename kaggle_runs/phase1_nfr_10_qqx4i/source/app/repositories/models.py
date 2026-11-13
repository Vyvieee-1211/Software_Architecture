from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
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
    __tablename__ = "ticket_types"
    __table_args__ = (CheckConstraint("remaining >= 0", name="ck_remaining_non_negative"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    concert_id: Mapped[int] = mapped_column(ForeignKey("concerts.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    total_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    remaining: Mapped[int] = mapped_column(Integer, nullable=False)

    concert: Mapped["Concert"] = relationship(back_populates="ticket_types")


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_quantity_positive"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    ticket_type_id: Mapped[int] = mapped_column(ForeignKey("ticket_types.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="orders")
    ticket_type: Mapped["TicketType"] = relationship()
