"""Hàng chờ Virtual Waiting Room, lưu ở bảng `waiting_room_entries`.

Lưu trong PostgreSQL để mọi tiến trình API thấy cùng một hàng chờ. Mỗi lần hỏi vị trí chỉ
là một phép đếm trên index (concert_id, id), không ghi gì; chỉ ghi khi vào hàng, khi được
vào lần đầu (`admitted_at`) và khi xếp lại cuối hàng.
"""
from datetime import datetime

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.repositories.models import WaitingRoomEntry

_UPSERT_INSERTS = {"sqlite": sqlite.insert, "postgresql": postgresql.insert}


class WaitingRoomRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, concert_id: int, user_id: int) -> WaitingRoomEntry | None:
        return self.db.scalar(select(WaitingRoomEntry).where(
            WaitingRoomEntry.concert_id == concert_id, WaitingRoomEntry.user_id == user_id))

    def join(self, concert_id: int, user_id: int, now: datetime) -> WaitingRoomEntry:
        """Vào hàng chờ; đã có chỗ thì giữ nguyên chỗ cũ (gọi lại nhiều lần vẫn an toàn)."""
        values = dict(concert_id=concert_id, user_id=user_id, joined_at=now)
        make_insert = _UPSERT_INSERTS.get(self.db.get_bind().dialect.name)
        if make_insert is not None:
            self.db.execute(make_insert(WaitingRoomEntry).values(**values)
                            .on_conflict_do_nothing(index_elements=["concert_id", "user_id"]))
        else:
            try:
                with self.db.begin_nested():
                    self.db.execute(insert(WaitingRoomEntry).values(**values))
            except IntegrityError:
                pass
        return self.get(concert_id, user_id)

    def requeue(self, entry: WaitingRoomEntry, now: datetime) -> WaitingRoomEntry:
        """Xếp lại cuối hàng (thẻ vào cửa cũ đã hết hạn): xóa chỗ cũ rồi vào hàng lại."""
        self.db.execute(delete(WaitingRoomEntry).where(WaitingRoomEntry.id == entry.id))
        self.db.expunge(entry)
        return self.join(entry.concert_id, entry.user_id, now)

    def count_ahead(self, concert_id: int, entry_id: int) -> int:
        """Số người vào hàng trước người này (kể cả người đã được vào): vị trí tính từ 0."""
        return self.db.scalar(select(func.count()).select_from(WaitingRoomEntry).where(
            WaitingRoomEntry.concert_id == concert_id, WaitingRoomEntry.id < entry_id))

    def mark_admitted(self, entry_id: int, now: datetime) -> bool:
        """Ghi mốc được vào, chỉ một lần (UPDATE có điều kiện)."""
        result = self.db.execute(
            update(WaitingRoomEntry)
            .where(WaitingRoomEntry.id == entry_id, WaitingRoomEntry.admitted_at.is_(None))
            .values(admitted_at=now)
            .execution_options(synchronize_session="fetch"))
        return result.rowcount == 1
