"""Cấu hình đọc từ biến môi trường. Không chứa secret thật trong code."""
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./concert.db")
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me-please-use-32-bytes-or-more")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))
