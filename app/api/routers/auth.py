from fastapi import APIRouter, Depends, status

from app.api.deps import get_auth_service
from app.api.schemas import LoginRequest, RegisterRequest, TokenResponse, UserResponse
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, auth: AuthService = Depends(get_auth_service)):
    return auth.register(body.email, body.password, body.full_name)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, auth: AuthService = Depends(get_auth_service)):
    return TokenResponse(access_token=auth.login(body.email, body.password))
