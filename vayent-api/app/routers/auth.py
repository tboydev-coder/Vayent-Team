"""Authentication API routes."""
import base64
import logging
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, BackgroundTasks, Cookie, Depends, HTTPException, Query, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    get_current_active_user,
    has_admin_access,
    has_super_admin_access,
)
from app.auth.jwt import extract_user_id_from_token
from app.config import get_settings
from app.database import ensure_db_initialized, get_db_session
from app.models import User
from app.schemas import (
    GithubAuthRequest,
    GoogleAuthRequest,
    TokenResponse,
    UserResponse,
    UserUpdateRequest,
)
from app.services.auth_service import auth_service
from app.services.activity_service import activity_service
from app.services.notification_service import notification_service
from app.services.token_usage_service import token_usage_service

router = APIRouter(prefix="/auth", tags=["Authentication"])
settings = get_settings()
logger = logging.getLogger(__name__)
SESSION_MARKER_COOKIE = "vayent_session_present"
GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    """Attach the refresh token cookie to a response."""
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=refresh_token,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite=settings.refresh_cookie_samesite,
        max_age=settings.refresh_token_expiration_days * 24 * 60 * 60,
        domain=settings.refresh_cookie_domain or None,
        path="/",
    )
    response.set_cookie(
        key=SESSION_MARKER_COOKIE,
        value="1",
        httponly=False,
        secure=settings.refresh_cookie_secure,
        samesite=settings.refresh_cookie_samesite,
        max_age=settings.refresh_token_expiration_days * 24 * 60 * 60,
        domain=settings.refresh_cookie_domain or None,
        path="/",
    )


def _clear_refresh_cookie(response: Response) -> None:
    """Clear the refresh token cookie."""
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        domain=settings.refresh_cookie_domain or None,
        path="/",
        secure=settings.refresh_cookie_secure,
        samesite=settings.refresh_cookie_samesite,
    )
    response.delete_cookie(
        key=SESSION_MARKER_COOKIE,
        domain=settings.refresh_cookie_domain or None,
        path="/",
        secure=settings.refresh_cookie_secure,
        samesite=settings.refresh_cookie_samesite,
    )


def _encode_oauth_state(redirect_uri: str) -> str:
    return base64.urlsafe_b64encode(redirect_uri.encode()).decode()


def _decode_oauth_state(state: str | None) -> str | None:
    if not state:
        return None

    try:
        return base64.urlsafe_b64decode(state.encode()).decode()
    except Exception:
        return None


def _normalize_frontend_redirect_uri(candidate: str | None) -> str:
    target = candidate or settings.frontend_login_uri
    parsed = urlparse(target)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    allowed = {item.rstrip("/") for item in settings.allowed_origins}

    if origin in allowed:
        return target

    return settings.frontend_login_uri


def _build_frontend_redirect(target: str, query_params: dict[str, str] | None = None) -> str:
    if not query_params:
        return target

    separator = "&" if "?" in target else "?"
    return f"{target}{separator}{urlencode(query_params)}"


def _provider_enabled(provider: str) -> bool:
    if provider == "github":
        return settings.github_oauth_enabled
    if provider == "google":
        return settings.google_oauth_enabled
    raise ValueError(f"Unsupported OAuth provider: {provider}")


def _provider_not_configured_message(provider_label: str) -> str:
    return f"{provider_label} sign-in is not configured."


def _database_unavailable_message() -> str:
    return "Sign-in is temporarily unavailable. Please try again later."


def _oauth_database_unavailable_redirect(redirect_uri: str) -> RedirectResponse:
    return RedirectResponse(
        url=_build_frontend_redirect(
            redirect_uri,
            {"error": _database_unavailable_message()},
        ),
        status_code=status.HTTP_302_FOUND,
    )


async def _database_available_for_auth() -> bool:
    return await ensure_db_initialized()


def _log_auth_event(
    *,
    action: str,
    status: str = "success",
    user: User | None = None,
    user_id: str | None = None,
    provider: str | None = None,
    details: dict | None = None,
) -> None:
    payload = dict(details or {})
    if provider:
        payload["provider"] = provider

    activity_service.log_event(
        action=action,
        status=status,
        user=user,
        user_id=user_id,
        resource_type="auth",
        resource_id=provider or getattr(user, "id", None),
        details=payload,
    )


def _oauth_not_configured_redirect(provider_label: str, redirect_uri: str) -> RedirectResponse:
    return RedirectResponse(
        url=_build_frontend_redirect(
            redirect_uri,
            {"error": _provider_not_configured_message(provider_label)},
        ),
        status_code=status.HTTP_302_FOUND,
    )


def _build_token_response(result: dict, response: Response) -> TokenResponse:
    _set_refresh_cookie(response, result["refresh_token"])
    return TokenResponse(
        access_token=result["access_token"],
        token_type="bearer",
        expires_in=result["expires_in"],
    )


async def _send_auth_notification(result: dict, provider: str) -> None:
    user = result.get("user")
    if not user:
        return

    try:
        if result.get("is_new_user"):
            await notification_service.send_signup_email(user, provider)
        else:
            await notification_service.send_login_email(user, provider)
    except Exception:
        logger.exception(
            "Failed to send %s auth notification email for user_id=%s",
            provider,
            getattr(user, "id", "unknown"),
        )


def _queue_auth_notification(
    *,
    background_tasks: BackgroundTasks | None,
    provider: str,
    result: dict,
) -> None:
    if background_tasks is None or not result.get("user"):
        return

    background_tasks.add_task(_send_auth_notification, result, provider)


async def _exchange_provider_code(
    *,
    provider: str,
    provider_label: str,
    code: str | None,
    response: Response,
    background_tasks: BackgroundTasks | None = None,
) -> TokenResponse:
    if not _provider_enabled(provider):
        _log_auth_event(
            action="auth.oauth_exchange",
            status="warning",
            provider=provider,
            details={"reason": "provider_not_configured"},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_provider_not_configured_message(provider_label),
        )

    if not await _database_available_for_auth():
        _log_auth_event(
            action="auth.oauth_exchange",
            status="blocked",
            provider=provider,
            details={"reason": "database_unavailable"},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_database_unavailable_message(),
        )

    if not code:
        _log_auth_event(
            action="auth.oauth_exchange",
            status="warning",
            provider=provider,
            details={"reason": "missing_authorization_code"},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Missing {provider_label} authorization code",
        )

    try:
        if provider == "github":
            result = await auth_service.handle_github_oauth(code=code)
        else:
            result = await auth_service.handle_google_oauth(code=code)
    except Exception as exc:
        _log_auth_event(
            action="auth.oauth_exchange",
            status="warning",
            provider=provider,
            details=activity_service.exception_details(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"{provider_label} authentication failed: {str(exc)}",
        ) from exc

    _log_auth_event(
        action="auth.oauth_exchange",
        status="success",
        user=result.get("user"),
        provider=provider,
        details={"is_new_user": bool(result.get("is_new_user"))},
    )
    _queue_auth_notification(
        background_tasks=background_tasks,
        provider=provider,
        result=result,
    )
    return _build_token_response(result, response)


async def _handle_provider_callback(
    *,
    provider: str,
    provider_label: str,
    code: str | None,
    state: str | None,
    error: str | None,
    error_description: str | None,
    background_tasks: BackgroundTasks | None = None,
) -> RedirectResponse:
    frontend_redirect_uri = _normalize_frontend_redirect_uri(_decode_oauth_state(state))

    if not _provider_enabled(provider):
        _log_auth_event(
            action="auth.oauth_callback",
            status="warning",
            provider=provider,
            details={"reason": "provider_not_configured"},
        )
        return _oauth_not_configured_redirect(provider_label, frontend_redirect_uri)

    if not await _database_available_for_auth():
        _log_auth_event(
            action="auth.oauth_callback",
            status="blocked",
            provider=provider,
            details={"reason": "database_unavailable"},
        )
        return _oauth_database_unavailable_redirect(frontend_redirect_uri)

    query_params: dict[str, str] = {}

    if error:
        _log_auth_event(
            action="auth.oauth_callback",
            status="warning",
            provider=provider,
            details={
                "reason": "provider_error",
                "oauth_error": error_description or error,
            },
        )
        query_params["error"] = error_description or error
    elif not code:
        _log_auth_event(
            action="auth.oauth_callback",
            status="warning",
            provider=provider,
            details={"reason": "missing_authorization_code"},
        )
        query_params["error"] = f"Missing {provider_label} authorization code"
    else:
        try:
            if provider == "github":
                result = await auth_service.handle_github_oauth(code=code)
            else:
                result = await auth_service.handle_google_oauth(code=code)

            _queue_auth_notification(
                background_tasks=background_tasks,
                provider=provider,
                result=result,
            )
            response = RedirectResponse(
                url=_build_frontend_redirect(frontend_redirect_uri, {"authenticated": "1"}),
                status_code=status.HTTP_302_FOUND,
                background=background_tasks,
            )
            _set_refresh_cookie(response, result["refresh_token"])
            _log_auth_event(
                action="auth.oauth_callback",
                status="success",
                user=result.get("user"),
                provider=provider,
                details={"is_new_user": bool(result.get("is_new_user"))},
            )
            return response
        except Exception as exc:
            _log_auth_event(
                action="auth.oauth_callback",
                status="warning",
                provider=provider,
                details=activity_service.exception_details(exc),
            )
            query_params["error"] = f"{provider_label} authentication failed"

    return RedirectResponse(
        url=_build_frontend_redirect(frontend_redirect_uri, query_params),
        status_code=status.HTTP_302_FOUND,
    )


@router.get("/github/login")
async def github_login_redirect(
    redirect_uri: str | None = Query(default=None),
):
    """Redirect the browser into the GitHub OAuth flow."""
    frontend_redirect_uri = _normalize_frontend_redirect_uri(redirect_uri)
    if not settings.github_oauth_enabled:
        _log_auth_event(
            action="auth.oauth_redirect",
            status="warning",
            provider="github",
            details={"reason": "provider_not_configured"},
        )
        return _oauth_not_configured_redirect("GitHub", frontend_redirect_uri)

    if not await _database_available_for_auth():
        _log_auth_event(
            action="auth.oauth_redirect",
            status="blocked",
            provider="github",
            details={"reason": "database_unavailable"},
        )
        return _oauth_database_unavailable_redirect(frontend_redirect_uri)

    _log_auth_event(
        action="auth.oauth_redirect",
        status="success",
        provider="github",
        details={"redirect_uri": frontend_redirect_uri},
    )
    params = {
        "client_id": settings.github_client_id,
        "redirect_uri": settings.github_redirect_uri,
        "scope": "read:user user:email",
        "state": _encode_oauth_state(frontend_redirect_uri),
    }
    return RedirectResponse(
        url=f"{GITHUB_AUTHORIZE_URL}?{urlencode(params)}",
        status_code=status.HTTP_302_FOUND,
    )


@router.get("/github/callback")
async def github_callback(
    background_tasks: BackgroundTasks,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    """Handle GitHub callback, then redirect back to the frontend login route."""
    return await _handle_provider_callback(
        provider="github",
        provider_label="GitHub",
        code=code,
        state=state,
        error=error,
        error_description=error_description,
        background_tasks=background_tasks,
    )


@router.post("/github")
async def github_auth(
    request: GithubAuthRequest,
    response: Response,
    background_tasks: BackgroundTasks,
):
    """Authenticate with GitHub OAuth using an authorization code."""
    return await _exchange_provider_code(
        provider="github",
        provider_label="GitHub",
        code=request.code,
        response=response,
        background_tasks=background_tasks,
    )


@router.get("/github", response_model=TokenResponse)
async def github_auth_get(
    background_tasks: BackgroundTasks,
    response: Response,
    code: str | None = None,
):
    """Convenience GET version of the GitHub code-exchange endpoint."""
    return await _exchange_provider_code(
        provider="github",
        provider_label="GitHub",
        code=code,
        response=response,
        background_tasks=background_tasks,
    )


@router.get("/google/login")
async def google_login_redirect(
    redirect_uri: str | None = Query(default=None),
):
    """Redirect the browser into the Google OAuth flow."""
    frontend_redirect_uri = _normalize_frontend_redirect_uri(redirect_uri)
    if not settings.google_oauth_enabled:
        _log_auth_event(
            action="auth.oauth_redirect",
            status="warning",
            provider="google",
            details={"reason": "provider_not_configured"},
        )
        return _oauth_not_configured_redirect("Google", frontend_redirect_uri)

    if not await _database_available_for_auth():
        _log_auth_event(
            action="auth.oauth_redirect",
            status="blocked",
            provider="google",
            details={"reason": "database_unavailable"},
        )
        return _oauth_database_unavailable_redirect(frontend_redirect_uri)

    _log_auth_event(
        action="auth.oauth_redirect",
        status="success",
        provider="google",
        details={"redirect_uri": frontend_redirect_uri},
    )
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": _encode_oauth_state(frontend_redirect_uri),
        "include_granted_scopes": "true",
        "prompt": "select_account",
    }
    return RedirectResponse(
        url=f"{GOOGLE_AUTHORIZE_URL}?{urlencode(params)}",
        status_code=status.HTTP_302_FOUND,
    )


@router.get("/google/callback")
async def google_callback(
    background_tasks: BackgroundTasks,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    """Handle Google callback, then redirect back to the frontend login route."""
    return await _handle_provider_callback(
        provider="google",
        provider_label="Google",
        code=code,
        state=state,
        error=error,
        error_description=error_description,
        background_tasks=background_tasks,
    )


@router.post("/google")
async def google_auth(
    request: GoogleAuthRequest,
    response: Response,
    background_tasks: BackgroundTasks,
):
    """Authenticate with Google OAuth using an authorization code."""
    return await _exchange_provider_code(
        provider="google",
        provider_label="Google",
        code=request.code,
        response=response,
        background_tasks=background_tasks,
    )


@router.get("/google", response_model=TokenResponse)
async def google_auth_get(
    background_tasks: BackgroundTasks,
    response: Response,
    code: str | None = None,
):
    """Convenience GET version of the Google code-exchange endpoint."""
    return await _exchange_provider_code(
        provider="google",
        provider_label="Google",
        code=code,
        response=response,
        background_tasks=background_tasks,
    )


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Get current authenticated user information."""
    refreshed_user = await token_usage_service.sync_user_usage_window(current_user.id, db)
    response_user = refreshed_user or current_user
    response_user.is_admin = bool(response_user.is_admin or has_admin_access(response_user))
    response_user.is_super_admin = bool(
        response_user.is_super_admin or has_super_admin_access(response_user)
    )
    return UserResponse.model_validate(response_user)


@router.patch("/me", response_model=UserResponse)
async def update_current_user_info(
    request: UserUpdateRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Update editable profile details for the current user."""
    try:
        previous_username = current_user.username
        updated_user = await auth_service.update_username(
            user_id=current_user.id,
            username=request.username,
            db=db,
        )
        refreshed_user = await token_usage_service.sync_user_usage_window(updated_user.id, db)
        _log_auth_event(
            action="auth.profile_updated",
            status="success",
            user=updated_user,
            details={
                "previous_username": previous_username,
                "new_username": updated_user.username,
            },
        )
        return UserResponse.model_validate(refreshed_user or updated_user)
    except ValueError as exc:
        detail = str(exc)
        _log_auth_event(
            action="auth.profile_update_failed",
            status="warning",
            user=current_user,
            details={"error": detail},
        )
        status_code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in detail.lower()
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.post("/refresh")
async def refresh_token(
    response: Response,
    db: AsyncSession = Depends(get_db_session),
    refresh_token: str | None = Cookie(default=None, alias=settings.refresh_cookie_name),
):
    """Refresh access token."""
    from datetime import datetime

    from app.auth.jwt import create_access_token, create_refresh_token

    if not refresh_token:
        _log_auth_event(
            action="auth.refresh",
            status="warning",
            details={"reason": "missing_refresh_token"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing refresh token",
        )

    user_id = extract_user_id_from_token(
        refresh_token,
        expected_token_type="refresh",
    )
    if not user_id:
        _clear_refresh_cookie(response)
        _log_auth_event(
            action="auth.refresh",
            status="warning",
            details={"reason": "invalid_or_expired_refresh_token"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    current_user = await auth_service.get_user_by_id(user_id, db)
    if not current_user or not current_user.is_active:
        _clear_refresh_cookie(response)
        _log_auth_event(
            action="auth.refresh",
            status="warning",
            user_id=user_id,
            details={"reason": "user_not_found_or_inactive"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    jwt_token, expires_at = create_access_token(data={"sub": current_user.id})
    new_refresh_token, _ = create_refresh_token(data={"sub": current_user.id})
    _set_refresh_cookie(response, new_refresh_token)
    _log_auth_event(
        action="auth.refresh",
        status="success",
        user=current_user,
    )

    return TokenResponse(
        access_token=jwt_token,
        token_type="bearer",
        expires_in=int((expires_at - datetime.utcnow()).total_seconds()),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    refresh_token: str | None = Cookie(default=None, alias=settings.refresh_cookie_name),
):
    """Clear the refresh cookie and end the browser session."""
    user_id = None
    if refresh_token:
        user_id = extract_user_id_from_token(
            refresh_token,
            expected_token_type="refresh",
        )

    _clear_refresh_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    _log_auth_event(
        action="auth.logout",
        status="success",
        user_id=user_id,
    )
    return response
