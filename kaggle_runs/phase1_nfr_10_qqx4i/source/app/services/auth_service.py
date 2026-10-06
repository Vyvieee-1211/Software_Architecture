"""Nghiệp vụ tài khoản: đăng ký, đăng nhập, cấp và kiểm tra token"""
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.services.errors import EmailAlreadyExists, InvalidCredentials, InvalidToken


class AuthService:
    def __init__(self, user_repo, jwt_secret: str, expire_minutes: int = 60):
        self.user_repo = user_repo
        self.jwt_secret = jwt_secret
        self.expire_minutes = expire_minutes

    def register(self, email: str, password: str, full_name: str | None):
        if self.user_repo.get_by_email(email) is not None:
            raise EmailAlreadyExists(email)
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        return self.user_repo.create(email=email, password_hash=password_hash, full_name=full_name)
    
    def login(self, email: str, password: str) -> str: 
        """Tra ve token neu dung email, password"""
        user = self.user_repo.get_by_email(email)
        if user is None or not bcrypt.checkpw(password.encode(), user.password_hash.encode()):
            raise InvalidCredentials()
        now = datetime.now(timezone.utc)
        payload = {"sub": str(user.id), "iat": now, "exp": now + timedelta(minutes=self.expire_minutes)}
        return jwt.encode(payload, self.jwt_secret, algorithm="HS256")
        

    def verify_token(self, token: str) -> int:
        """Trả về user_id. Ném InvalidToken nếu sai/hết hạn."""
        try:
            payload = jwt.decode(token, self.jwt_secret, algorithms=["HS256"])
            return int(payload["sub"])
        except (jwt.PyJWTError, KeyError, ValueError):
            raise InvalidToken()
