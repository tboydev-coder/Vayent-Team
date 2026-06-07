"""Authentication service layer."""
from datetime import datetime
import re
import unicodedata
import uuid

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.auth.github_oauth import github_oauth
from app.auth.google_oauth import google_oauth
from app.auth.jwt import create_access_token, create_refresh_token
from app.config import get_settings
from app.models import OAuthAccount, User

MAX_USERNAME_LENGTH = 50
_USERNAME_SANITIZE_RE = re.compile(r"[^a-z0-9._-]+")
_USERNAME_SEPARATOR_RE = re.compile(r"[._-]{2,}")


def build_oauth_username_base(preferred: str | None, email: str | None = None) -> str:
    """Build a safe username seed from OAuth profile data."""
    candidates = [
        preferred or "",
        (email or "").split("@")[0],
    ]

    for raw_value in candidates:
        normalized = (
            unicodedata.normalize("NFKD", raw_value)
            .encode("ascii", "ignore")
            .decode("ascii")
            .strip()
            .lower()
        )
        normalized = normalized.replace("@", "-")
        normalized = _USERNAME_SANITIZE_RE.sub("-", normalized)
        normalized = _USERNAME_SEPARATOR_RE.sub("-", normalized)
        normalized = normalized.strip("._-")
        was_truncated = len(normalized) > MAX_USERNAME_LENGTH
        normalized = normalized[:MAX_USERNAME_LENGTH].rstrip("._-")

        if was_truncated:
            last_separator = max(
                normalized.rfind("-"),
                normalized.rfind("_"),
                normalized.rfind("."),
            )
            if last_separator >= MAX_USERNAME_LENGTH - 12:
                normalized = normalized[:last_separator].rstrip("._-")

        if normalized:
            return normalized

    return "user"


class AuthService:
    """Service for authentication operations."""

    async def handle_github_oauth(self, code: str) -> dict:
        """
        Handle GitHub OAuth login and registration.

        The method exchanges the authorization code for a GitHub access token,
        loads the profile, resolves an application user, and returns Vayent
        session tokens.
        """
        if not code:
            raise Exception("No GitHub authorization code supplied")

        token_data = await github_oauth.exchange_code_for_token(code)
        if not token_data:
            raise Exception("Failed to exchange GitHub code")

        github_token = token_data.get("access_token")
        if not github_token:
            raise Exception("GitHub did not return an access token")

        user_info = await github_oauth.get_user_info(github_token)
        if not user_info:
            raise Exception("Failed to fetch GitHub user info")

        email = user_info.get("email")
        if not email:
            email = await github_oauth.get_primary_email(github_token)

        if not email:
            raise Exception("Could not obtain email from GitHub")

        github_user_id = str(user_info.get("id") or "")
        if not github_user_id:
            raise Exception("GitHub did not return a stable user id")

        github_username = (
            user_info.get("login")
            or user_info.get("name")
            or email.split("@")[0]
        )

        return await self._complete_oauth_login(
            provider="github",
            provider_user_id=github_user_id,
            provider_username=github_username,
            email=email,
        )

    async def handle_google_oauth(self, code: str) -> dict:
        """Handle Google OAuth login and registration."""
        if not code:
            raise Exception("No Google authorization code supplied")

        token_data = await google_oauth.exchange_code_for_token(code)
        if not token_data:
            raise Exception("Failed to exchange Google code")

        google_token = token_data.get("access_token")
        if not google_token:
            raise Exception("Google did not return an access token")

        user_info = await google_oauth.get_user_info(google_token)
        if not user_info:
            raise Exception("Failed to fetch Google user info")

        if user_info.get("email_verified") is False:
            raise Exception("Google account email is not verified")

        email = user_info.get("email")
        if not email:
            raise Exception("Could not obtain email from Google")

        google_user_id = str(user_info.get("sub") or "")
        if not google_user_id:
            raise Exception("Google did not return a stable user id")

        google_username = (
            user_info.get("name")
            or user_info.get("given_name")
            or email.split("@")[0]
        )

        return await self._complete_oauth_login(
            provider="google",
            provider_user_id=google_user_id,
            provider_username=google_username,
            email=email,
        )

    async def _complete_oauth_login(
        self,
        *,
        provider: str,
        provider_user_id: str,
        provider_username: str,
        email: str,
    ) -> dict:
        """Find or create a Vayent user for an OAuth provider identity."""
        from app.database import get_db_context

        settings = get_settings()
        bootstrap_emails = {
            email.strip().lower()
            for email in settings.admin_bootstrap_emails
            if email.strip()
        }

        async with get_db_context() as db:
            is_new_user = False
            total_users_result = await db.execute(select(func.count()).select_from(User))
            is_first_user = (total_users_result.scalar_one() or 0) == 0
            result = await db.execute(
                select(OAuthAccount).where(
                    OAuthAccount.provider == provider,
                    OAuthAccount.provider_user_id == provider_user_id,
                )
            )
            oauth_account = result.scalar_one_or_none()

            if oauth_account:
                oauth_account.provider_username = provider_username
                oauth_account.updated_at = datetime.utcnow()
                result = await db.execute(select(User).where(User.id == oauth_account.user_id))
                user = result.scalar_one()
            else:
                result = await db.execute(select(User).where(User.email == email))
                user = result.scalar_one_or_none()

                if not user:
                    should_bootstrap_admin = is_first_user or email.lower() in bootstrap_emails
                    user = User(
                        id=str(uuid.uuid4()),
                        username=await self._build_available_username(
                            preferred=provider_username,
                            email=email,
                            db=db,
                        ),
                        email=email,
                        is_active=True,
                        plan_type="free",
                        monthly_token_usage=0,
                        reserved_token_usage=0,
                        manual_token_balance=0,
                        token_reset_date=datetime.utcnow().date(),
                        is_admin=should_bootstrap_admin,
                        is_super_admin=should_bootstrap_admin,
                        last_login_at=datetime.utcnow(),
                        last_seen_at=datetime.utcnow(),
                    )
                    db.add(user)
                    await db.flush()
                    is_new_user = True

                oauth_account = OAuthAccount(
                    id=str(uuid.uuid4()),
                    user_id=user.id,
                    provider=provider,
                    provider_user_id=provider_user_id,
                    provider_username=provider_username,
                    access_token="",
                )
                db.add(oauth_account)

            if user.email.lower() in bootstrap_emails:
                user.is_admin = True
                user.is_super_admin = True
            user.last_login_at = datetime.utcnow()
            user.last_seen_at = datetime.utcnow()

        jwt_token, expires_at = create_access_token(data={"sub": user.id})
        refresh_token, refresh_expires_at = create_refresh_token(
            data={"sub": user.id}
        )

        return {
            "user": user,
            "is_new_user": is_new_user,
            "access_token": jwt_token,
            "refresh_token": refresh_token,
            "expires_in": int((expires_at - datetime.utcnow()).total_seconds()),
            "refresh_expires_in": int(
                (refresh_expires_at - datetime.utcnow()).total_seconds()
            ),
        }

    async def _build_available_username(
        self,
        *,
        preferred: str | None,
        email: str | None,
        db: AsyncSession,
    ) -> str:
        """Generate a unique username for a new OAuth-backed user."""
        base_username = build_oauth_username_base(preferred, email)
        candidate = base_username
        suffix = 2

        while not await self._is_username_available(candidate, db):
            suffix_text = f"-{suffix}"
            trimmed_base = base_username[: MAX_USERNAME_LENGTH - len(suffix_text)].rstrip(
                "._-"
            )
            candidate = f"{trimmed_base or 'user'}{suffix_text}"
            suffix += 1

        return candidate

    async def _is_username_available(self, username: str, db: AsyncSession) -> bool:
        """Check whether a username is currently unused."""
        result = await db.execute(
            select(User.id).where(func.lower(User.username) == username.lower())
        )
        return result.scalar_one_or_none() is None

    async def get_user_by_id(self, user_id: str, db: AsyncSession) -> User:
        """Get user by ID."""
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def update_username(
        self,
        *,
        user_id: str,
        username: str,
        db: AsyncSession,
    ) -> User:
        """Update the current user's username with uniqueness checks."""
        cleaned_username = username.strip()
        if not cleaned_username:
            raise ValueError("Username cannot be empty.")

        duplicate_result = await db.execute(
            select(User).where(
                User.id != user_id,
                func.lower(User.username) == cleaned_username.lower(),
            )
        )
        if duplicate_result.scalar_one_or_none():
            raise ValueError("That username is already taken. Please choose another one.")

        user = await self.get_user_by_id(user_id, db)
        if not user:
            raise ValueError("User not found.")

        user.username = cleaned_username
        user.updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(user)
        return user


auth_service = AuthService()
