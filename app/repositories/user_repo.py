from sqlalchemy import select
from sqlalchemy.orm import Session

from app.repositories.models import User


class UserRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, user_id: int) -> User | None:
        return self.db.get(User, user_id)

    def get_by_email(self, email: str) -> User | None:
        return self.db.scalar(select(User).where(User.email == email))

    def create(self, email: str, password_hash: str, full_name: str | None) -> User:
        user = User(email=email, password_hash=password_hash, full_name=full_name)
        self.db.add(user)
        self.db.flush()
        return user

    def lock(self, user_id: int) -> bool:
        """Khóa dòng user đến hết transaction để tuần tự hóa các thao tác của cùng một người
        (vd. kiểm tra giới hạn vé đang giữ). Trả False nếu user không tồn tại."""
        return self.db.scalar(select(User.id).where(User.id == user_id).with_for_update()) is not None
