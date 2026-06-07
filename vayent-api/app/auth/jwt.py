"""Authentication utilities and JWT token management."""
from datetime import datetime, timedelta
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext
import logging

from app.config import get_settings

logger = logging.getLogger(__name__)

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    return pwd_context.verify(plain_password, hashed_password)


def _create_token(
    data: dict,
    *,
    token_type: str,
    expires_delta: timedelta,
) -> tuple[str, datetime]:
    """Create a signed JWT with common claims."""
    settings = get_settings()
    issued_at = datetime.utcnow()
    expire = issued_at + expires_delta
    to_encode = data.copy()
    to_encode.update(
        {
            "exp": expire,
            "iat": issued_at,
            "token_type": token_type,
        }
    )

    encoded_jwt = jwt.encode(
        to_encode,
        settings.secret_key,
        algorithm=settings.algorithm,
    )
    return encoded_jwt, expire


def create_access_token(
    data: dict,
    expires_delta: Optional[timedelta] = None,
) -> tuple[str, datetime]:
    """Create a short-lived access token."""
    settings = get_settings()
    return _create_token(
        data,
        token_type="access",
        expires_delta=expires_delta
        or timedelta(minutes=settings.access_token_expiration_minutes),
    )


def create_refresh_token(
    data: dict,
    expires_delta: Optional[timedelta] = None,
) -> tuple[str, datetime]:
    """Create a refresh token."""
    settings = get_settings()
    return _create_token(
        data,
        token_type="refresh",
        expires_delta=expires_delta
        or timedelta(days=settings.refresh_token_expiration_days),
    )


def verify_token(token: str, expected_token_type: Optional[str] = None) -> Optional[dict]:
    """
    Verify and decode JWT token.

    Returns: token claims if valid, None if invalid
    """
    settings = get_settings()

    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.algorithm],
        )
        if expected_token_type and payload.get("token_type") != expected_token_type:
            logger.warning("Invalid token type")
            return None
        return payload
    except JWTError as e:
        logger.warning(f"Invalid token: {e}")
        return None


def extract_user_id_from_token(
    token: str,
    expected_token_type: str = "access",
) -> Optional[str]:
    """Extract user_id from JWT token."""
    payload = verify_token(token, expected_token_type=expected_token_type)
    if payload:
        return payload.get("sub")
    return None
