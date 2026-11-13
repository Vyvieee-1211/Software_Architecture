from fastapi import APIRouter, Depends, status

from app.api.deps import get_auth_service, get_current_user
from app.api.schemas import LoginRequest, RegisterRequest, TokenResponse, UserResponse, ProfileResponse
from app.config import ADMIN_EMAILS
from app.services.auth_service import AuthService
from app.repositories.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me", response_model=ProfileResponse)
def get_profile(user: User = Depends(get_current_user)):
    profile = UserResponse.model_validate(user).model_dump()
    return {**profile, "is_admin": user.email.lower() in ADMIN_EMAILS}


@router.post("/register", response_model = UserResponse, status_code = status.HTTP_201_CREATED) # tra ve status 201 
def register(body: RegisterRequest, auth: AuthService = Depends(get_auth_service)):
    return auth.register(body.email, body.password, body.full_name)

@router.post("/login", response_model = TokenResponse) # tra ve token -> phien dang nhap 
def login(body: LoginRequest, auth: AuthService = Depends(get_auth_service)): 
    token = auth.login(body.email, body.password)
    return TokenResponse(access_token=token) 

