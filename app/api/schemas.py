"""Định nghĩa cấu trúc dữ liệu"""
from datetime import datetime, timezone
from decimal import Decimal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


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


class ProfileResponse(UserResponse):
    is_admin: bool


class TicketTypeInput(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    total_quantity: int = Field(ge=1, le=10000000)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        if not value.strip():
            raise ValueError("Ticket name must not be blank")
        return value.strip()


class ConcertInput(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    artist: str | None = Field(default=None, max_length=255)
    venue: str | None = Field(default=None, max_length=255)
    start_time: datetime
    sale_open_time: datetime

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        if not value.strip():
            raise ValueError("Concert name must not be blank")
        return value.strip()

    @field_validator("start_time", "sale_open_time")
    @classmethod
    def utc_time(cls, value):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value

    @model_validator(mode="after")
    def validate_dates(self):
        if self.sale_open_time > self.start_time:
            raise ValueError("Sale opening must not be after concert start")
        return self


class CreateConcertInput(ConcertInput):
    ticket_types: list[TicketTypeInput] = Field(min_length=1, max_length=30)


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
