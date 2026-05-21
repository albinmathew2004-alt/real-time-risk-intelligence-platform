from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.auth.security import hash_password, verify_password
from app.models.user import User, UserRole


LOCAL_DEMO_MODES = {
    "local",
    "local-demo",
    "local-postgres-redis",
    "dev",
    "development",
    "docker-compose",
    "controlled-demo",
}


@dataclass(frozen=True)
class DemoAdminConfig:
    email: str
    password: str
    full_name: str


def resolve_demo_admin_config() -> DemoAdminConfig:
    email = os.getenv("DEMO_ADMIN_EMAIL", "").strip() or os.getenv("DEFAULT_ADMIN_EMAIL", "").strip()
    password = os.getenv("DEMO_ADMIN_PASSWORD", "").strip() or os.getenv("DEFAULT_ADMIN_PASSWORD", "").strip()
    full_name = os.getenv("DEMO_ADMIN_NAME", "").strip() or os.getenv("DEFAULT_ADMIN_NAME", "Demo Administrator").strip()
    return DemoAdminConfig(email=email, password=password, full_name=full_name or "Demo Administrator")


def should_verify_demo_admin_on_startup(app_mode: str) -> bool:
    explicit = os.getenv("VERIFY_DEMO_ADMIN_ON_STARTUP", "").strip().lower()
    if explicit:
        return explicit in {"1", "true", "yes", "on"}
    return (app_mode or "").strip().lower() in LOCAL_DEMO_MODES


def ensure_demo_admin(db: Session, *, config: DemoAdminConfig | None = None) -> dict[str, object]:
    resolved = config or resolve_demo_admin_config()
    if not resolved.email or not resolved.password:
        raise RuntimeError(
            "Missing DEMO_ADMIN_EMAIL/DEMO_ADMIN_PASSWORD (or DEFAULT_ADMIN_EMAIL/DEFAULT_ADMIN_PASSWORD). "
            "Set demo admin credentials before starting hosted demo mode."
        )

    existing = db.query(User).filter(User.email == resolved.email).first()
    created = False
    updated = False

    if not existing:
        existing = User(
            email=resolved.email,
            full_name=resolved.full_name,
            hashed_password=hash_password(resolved.password),
            role=UserRole.ADMIN.value,
            is_active=True,
        )
        db.add(existing)
        created = True
        updated = True
    else:
        if existing.role != UserRole.ADMIN.value:
            existing.role = UserRole.ADMIN.value
            updated = True
        if not existing.is_active:
            existing.is_active = True
            updated = True
        if (existing.full_name or "") != resolved.full_name:
            existing.full_name = resolved.full_name
            updated = True
        if not verify_password(resolved.password, existing.hashed_password):
            existing.hashed_password = hash_password(resolved.password)
            updated = True

    if updated:
        db.commit()
        db.refresh(existing)

    return {
        "email": existing.email,
        "role": existing.role,
        "created": created,
        "updated": updated,
        "is_active": bool(existing.is_active),
    }
