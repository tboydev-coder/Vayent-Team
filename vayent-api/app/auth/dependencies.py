"""FastAPI dependencies for authentication."""
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
import logging

from app.database import get_db_session
from app.auth.jwt import extract_user_id_from_token
from app.config import get_settings
from app.models import User
from app.services.activity_service import activity_service

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)


def has_admin_access(user: User) -> bool:
    """Return true when a user has effective admin access."""
    settings = get_settings()
    bootstrap_emails = {
        email.strip().lower()
        for email in settings.admin_bootstrap_emails
        if email.strip()
    }
    return bool(
        user.is_admin
        or user.is_super_admin
        or (user.email or "").lower() in bootstrap_emails
    )


def has_super_admin_access(user: User) -> bool:
    """Return true when a user can perform sensitive admin operations."""
    settings = get_settings()
    bootstrap_emails = {
        email.strip().lower()
        for email in settings.admin_bootstrap_emails
        if email.strip()
    }
    return bool(user.is_super_admin or (user.email or "").lower() in bootstrap_emails)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db_session),
) -> User:
    """
    Dependency to get current authenticated user.
    Validates JWT token and fetches user from database.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization token",
        )

    token = credentials.credentials

    user_id = extract_user_id_from_token(token, expected_token_type="access")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    # Fetch user from database
    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    if not user.is_active or getattr(user, "is_suspended", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive",
        )

    return user


async def get_current_active_user(
    request: Request,
    user: User = Depends(get_current_user),
) -> User:
    """Get current active user (wrapper for clarity)."""
    request.state.current_user_context = {
        "user_id": user.id,
        "username": user.username or "-",
        "user_email": user.email or "-",
        "authenticated": True,
    }
    return user


async def get_current_admin_user(
    request: Request,
    user: User = Depends(get_current_active_user),
) -> User:
    """Require an active platform admin."""
    if has_admin_access(user):
        return user

    activity_service.log_event(
        action="admin.access_denied",
        status="warning",
        user=user,
        resource_type="admin_route",
        resource_id=request.url.path,
        details={
            "endpoint": request.url.path,
            "method": request.method,
            "reason": "missing_admin_role",
        },
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Admin access required",
    )


async def get_current_super_admin_user(
    request: Request,
    user: User = Depends(get_current_active_user),
) -> User:
    """Require a super admin for sensitive administrative actions."""
    if has_super_admin_access(user):
        return user

    activity_service.log_event(
        action="admin.super_access_denied",
        status="warning",
        user=user,
        resource_type="admin_route",
        resource_id=request.url.path,
        details={
            "endpoint": request.url.path,
            "method": request.method,
            "reason": "missing_super_admin_role",
        },
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Super admin access required",
    )
