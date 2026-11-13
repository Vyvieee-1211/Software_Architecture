"""Từng vé cụ thể: sinh sẵn theo hạng vé, gán cho đơn khi giữ chỗ, xuất khi đã thanh toán."""
from collections.abc import Callable

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from app.repositories.models import Ticket

SEAT_WIDTH = 6


class TicketRepository:
    def __init__(self, db: Session):
        self.db = db

    def generate(self, ticket_type_id: int, count: int) -> None:
        """Sinh thêm `count` vé trống, số ghế nối tiếp số lớn nhất đang có."""
        if count <= 0:
            return
        last = self.db.scalar(
            select(func.max(Ticket.seat_no)).where(Ticket.ticket_type_id == ticket_type_id)
        )
        start = int(last) + 1 if last else 1
        self.db.execute(
            insert(Ticket),
            [
                {
                    "ticket_type_id": ticket_type_id,
                    "seat_no": f"{number:0{SEAT_WIDTH}d}",
                    "status": "available",
                    "version": 1,
                }
                for number in range(start, start + count)
            ],
        )

    def hold(self, ticket_type_id: int, order_id: int, quantity: int) -> list[Ticket] | None:
        """Gán `quantity` vé trống cho đơn. Trả None nếu không lấy đủ vé.

        PostgreSQL: SKIP LOCKED để các giao dịch đồng thời lấy các vé khác nhau thay vì
        chờ nhau. Cột version là lớp bảo vệ thứ hai (và là lớp duy nhất trên SQLite,
        nơi FOR UPDATE bị bỏ qua): flush sẽ ném StaleDataError nếu vé đã bị giao dịch
        khác sửa, và cả nghiệp vụ được chạy lại.
        """
        tickets = list(
            self.db.scalars(
                select(Ticket)
                .where(Ticket.ticket_type_id == ticket_type_id, Ticket.status == "available")
                .order_by(Ticket.id)
                .limit(quantity)
                .with_for_update(skip_locked=True)
            )
        )
        if len(tickets) != quantity:
            return None
        for ticket in tickets:
            ticket.order_id = order_id
            ticket.status = "held"
        self.db.flush()
        return tickets

    def _held_by_order(self, order_id: int) -> list[Ticket]:
        return list(
            self.db.scalars(
                select(Ticket)
                .where(Ticket.order_id == order_id, Ticket.status == "held")
                .order_by(Ticket.id)
                .with_for_update()
            )
        )

    def sell(self, order_id: int, make_code: Callable[[int], str]) -> list[Ticket]:
        """Xuất vé cho đơn đã thanh toán và sinh mã QR cho từng vé."""
        tickets = self._held_by_order(order_id)
        for ticket in tickets:
            ticket.status = "sold"
            ticket.qr_code = make_code(ticket.id)
        self.db.flush()
        return tickets

    def release(self, order_id: int) -> int:
        """Trả các vé đang giữ của đơn về trạng thái trống. Trả về số vé đã trả."""
        tickets = self._held_by_order(order_id)
        for ticket in tickets:
            ticket.order_id = None
            ticket.status = "available"
        self.db.flush()
        return len(tickets)

    def list_by_order(self, order_id: int) -> list[Ticket]:
        return list(
            self.db.scalars(select(Ticket).where(Ticket.order_id == order_id).order_by(Ticket.id))
        )

    def count_by_status(self, ticket_type_id: int, status: str) -> int:
        return self.db.scalar(
            select(func.count())
            .select_from(Ticket)
            .where(Ticket.ticket_type_id == ticket_type_id, Ticket.status == status)
        )

    def remove_available(self, ticket_type_id: int, count: int) -> int:
        """Xóa `count` vé trống có số ghế lớn nhất (khi giảm tổng số vé). Trả về số vé đã xóa."""
        if count <= 0:
            return 0
        ids = list(
            self.db.scalars(
                select(Ticket.id)
                .where(Ticket.ticket_type_id == ticket_type_id, Ticket.status == "available")
                .order_by(Ticket.id.desc())
                .limit(count)
                .with_for_update(skip_locked=True)
            )
        )
        if ids:
            self.db.execute(delete(Ticket).where(Ticket.id.in_(ids), Ticket.status == "available"))
        return len(ids)

    def delete_by_type(self, ticket_type_id: int) -> None:
        self.db.execute(delete(Ticket).where(Ticket.ticket_type_id == ticket_type_id))
