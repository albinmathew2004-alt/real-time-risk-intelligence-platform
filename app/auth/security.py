from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import bcrypt
from jose import JWTError, jwt


def hash_password(password: str) -> str:
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return False


def create_access_token(
    *,
    subject: str,
    secret_key: str,
    algorithm: str,
    expires_minutes: int,
    extra_claims: Optional[Dict[str, Any]] = None,
) -> tuple[str, int]:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=expires_minutes)
    claims: Dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "type": "access",
    }
    if extra_claims:
        claims.update(extra_claims)

    token = jwt.encode(claims, secret_key, algorithm=algorithm)
    return token, int((expire - now).total_seconds())


def decode_token(*, token: str, secret_key: str, algorithm: str) -> Dict[str, Any]:
    try:
        payload = jwt.decode(token, secret_key, algorithms=[algorithm])
        return payload
    except JWTError as e:
        raise ValueError("Invalid token") from e
