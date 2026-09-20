"""Pydantic schema: hình dạng JSON vào/ra của API. Chỉ tầng API dùng."""
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    full_name: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: int
    email: str
    full_name: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ConcertResponse(BaseModel):
    id: int
    name: str
    artist: str | None
    venue: str | None
    start_time: datetime
    sale_open_time: datetime

    model_config = {"from_attributes": True}


class TicketTypeResponse(BaseModel):
    id: int
    concert_id: int
    name: str
    price: Decimal
    total_quantity: int
    remaining: int

    model_config = {"from_attributes": True}


class CreateOrderRequest(BaseModel):
    ticket_type_id: int
    quantity: int = Field(gt=0, le=10)


class OrderResponse(BaseModel):
    id: int
    user_id: int
    ticket_type_id: int
    quantity: int
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}
