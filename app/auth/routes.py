from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, status

from engine.db.database import SessionLocal
from app.auth.security import create_access_token, verify_password
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse, UserOut
from app.auth.dependencies import get_current_user


router = APIRouter(prefix="/v1/auth", tags=["auth"])


def _token_settings() -> tuple[str, str, int]:
    secret = os.getenv("JWT_SECRET_KEY", "")
    algo = os.getenv("JWT_ALGORITHM", "HS256")
    expire = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "480"))
    if not secret:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="JWT_SECRET_KEY is not set")
    return secret, algo, expire


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == payload.email).first()
        if not user or not user.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
        if not verify_password(payload.password, user.hashed_password):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

        secret, algo, expire = _token_settings()
        token, expires_in = create_access_token(
            subject=user.email,
            secret_key=secret,
            algorithm=algo,
            expires_minutes=expire,
            extra_claims={"role": user.role, "uid": user.id},
        )

        return TokenResponse(
            access_token=token,
            expires_in=expires_in,
            user=UserOut.model_validate(user, from_attributes=True),
        )
    finally:
        db.close()


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    return UserOut.model_validate(current_user, from_attributes=True)
