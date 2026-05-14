from __future__ import annotations

import os
from typing import Callable, Iterable, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from engine.db.database import SessionLocal
from app.auth.security import decode_token
from app.models.user import User, UserRole


bearer_scheme = HTTPBearer(auto_error=False)


def _get_auth_settings() -> tuple[str, str]:
    secret = os.getenv("JWT_SECRET_KEY", "")
    algo = os.getenv("JWT_ALGORITHM", "HS256")
    if not secret:
        # Fail closed for protected routes if the server isn't configured
        raise RuntimeError("JWT_SECRET_KEY is not set")
    return secret, algo


def get_current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> User:
    if creds is None or not creds.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    try:
        secret, algo = _get_auth_settings()
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)) from e
    try:
        payload = decode_token(token=creds.credentials, secret_key=secret, algorithm=algo)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    subject = payload.get("sub")
    if not subject:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == str(subject)).first()
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
        if not user.is_active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User inactive")
        return user
    finally:
        db.close()


def _require_roles(roles: Iterable[str]) -> Callable[[User], User]:
    allowed = set(roles)

    def checker(user: User = Depends(get_current_user)) -> User:
        if (user.role or "").upper() not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user

    return checker


require_admin = _require_roles([UserRole.ADMIN.value])
require_reviewer = _require_roles([UserRole.ADMIN.value, UserRole.REVIEWER.value])
require_viewer_or_above = _require_roles([UserRole.ADMIN.value, UserRole.REVIEWER.value, UserRole.VIEWER.value])
# "internal" interpreted as system automation + admins
require_system_or_internal = _require_roles([UserRole.ADMIN.value, UserRole.SYSTEM.value])
