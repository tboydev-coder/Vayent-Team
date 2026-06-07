"""Middleware that enriches logs with request and user context."""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timedelta

from sqlalchemy import or_, update
from starlette.middleware.base import BaseHTTPMiddleware

from app.auth.jwt import extract_user_id_from_token
from app.database import get_db_context
from app.logging_context import bind_logging_context, clear_logging_context
from app.models import ActivityLog, User

logger = logging.getLogger("app.request")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind request metadata into the log context and log request outcomes."""

    _LAST_SEEN_UPDATE_INTERVAL_SECONDS = 60

    def __init__(self, app):
        super().__init__(app)
        self._skip_request_logs = {
            "/",
            "/health",
            "/status",
            "/docs",
            "/openapi.json",
            "/redoc",
        }
        self._last_seen_updates: dict[str, float] = {}

    async def dispatch(self, request, call_next):
        request_id = uuid.uuid4().hex[:12]
        client_ip = request.client.host if request.client else "-"
        user_context = await self._resolve_user_context(request)

        bind_logging_context(
            request_id=request_id,
            request_method=request.method,
            request_path=request.url.path,
            client_ip=client_ip,
            **user_context,
        )

        started_at = time.perf_counter()

        if request.url.path not in self._skip_request_logs:
            logger.info("Request started")

        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started_at) * 1000)
            user_context = self._state_user_context(request, fallback=user_context)
            bind_logging_context(**user_context)
            logger.exception("Request failed after %sms", duration_ms)
            await self._persist_request_activity(
                request=request,
                user_context=user_context,
                duration_ms=duration_ms,
                status_code=500,
                error_trace=str(exc),
            )
            clear_logging_context()
            raise

        duration_ms = int((time.perf_counter() - started_at) * 1000)
        user_context = self._state_user_context(request, fallback=user_context)
        bind_logging_context(**user_context)
        logger.info(
            "Request completed with status %s in %sms",
            response.status_code,
            duration_ms,
        )
        await self._persist_request_activity(
            request=request,
            user_context=user_context,
            duration_ms=duration_ms,
            status_code=response.status_code,
        )
        clear_logging_context()
        return response

    async def _resolve_user_context(self, request) -> dict[str, object]:
        token = self._extract_bearer_token(request)
        if not token:
            return {
                "user_id": "-",
                "username": "-",
                "user_email": "-",
                "authenticated": False,
            }

        user_id = extract_user_id_from_token(token, expected_token_type="access")
        if not user_id:
            return {
                "user_id": "-",
                "username": "-",
                "user_email": "-",
                "authenticated": False,
            }

        return {
            "user_id": user_id,
            "username": "-",
            "user_email": "-",
            "authenticated": False,
        }

    @staticmethod
    def _extract_bearer_token(request) -> str | None:
        authorization = request.headers.get("Authorization", "").strip()
        if not authorization.lower().startswith("bearer "):
            return None
        token = authorization[7:].strip()
        return token or None

    @staticmethod
    def _state_user_context(
        request,
        *,
        fallback: dict[str, object],
    ) -> dict[str, object]:
        context = getattr(request.state, "current_user_context", None)
        if not isinstance(context, dict):
            return fallback

        return {
            "user_id": context.get("user_id") or fallback.get("user_id", "-"),
            "username": context.get("username") or "-",
            "user_email": context.get("user_email") or "-",
            "authenticated": bool(context.get("authenticated")),
        }

    def _should_update_last_seen(self, user_id: str, now: float) -> bool:
        last_update = self._last_seen_updates.get(user_id)
        if (
            last_update is not None
            and now - last_update < self._LAST_SEEN_UPDATE_INTERVAL_SECONDS
        ):
            return False

        self._last_seen_updates[user_id] = now
        if len(self._last_seen_updates) > 5000:
            expiry = now - (self._LAST_SEEN_UPDATE_INTERVAL_SECONDS * 10)
            self._last_seen_updates = {
                key: value
                for key, value in self._last_seen_updates.items()
                if value >= expiry
            }
        return True

    async def _persist_request_activity(
        self,
        *,
        request,
        user_context: dict[str, object],
        duration_ms: int,
        status_code: int,
        error_trace: str | None = None,
    ) -> None:
        """Store request-level support telemetry for admin dashboards."""
        if request.url.path in self._skip_request_logs:
            return

        severity = "info"
        status_value = "success"
        if status_code >= 500:
            severity = "error"
            status_value = "error"
        elif status_code in {401, 403, 429} or status_code >= 400:
            severity = "warning"
            status_value = "warning"

        user_id = user_context.get("user_id")
        actor_user_id = (
            user_id
            if user_context.get("authenticated") and user_id and user_id != "-"
            else None
        )
        query_payload = dict(request.query_params)

        try:
            now = datetime.utcnow()
            async with get_db_context() as db:
                if actor_user_id and self._should_update_last_seen(
                    actor_user_id,
                    time.monotonic(),
                ):
                    stale_before = now - timedelta(
                        seconds=self._LAST_SEEN_UPDATE_INTERVAL_SECONDS
                    )
                    await db.execute(
                        update(User)
                        .where(
                            User.id == actor_user_id,
                            or_(
                                User.last_seen_at.is_(None),
                                User.last_seen_at < stale_before,
                            ),
                        )
                        .values(last_seen_at=now)
                    )

                db.add(
                    ActivityLog(
                        id=str(uuid.uuid4()),
                        actor_user_id=actor_user_id,
                        actor_username=user_context.get("username") or None,
                        actor_email=user_context.get("user_email") or None,
                        action="request.completed",
                        status=status_value,
                        severity=severity,
                        resource_type="api_request",
                        resource_id=request.url.path,
                        endpoint=request.url.path,
                        method=request.method,
                        ip_address=request.client.host if request.client else None,
                        user_agent=request.headers.get("user-agent"),
                        request_payload={"query": query_payload},
                        response_status_code=status_code,
                        response_time_ms=duration_ms,
                        error_trace=error_trace,
                        session_id=request.cookies.get("vayent_session_present"),
                        details={
                            "duration_ms": duration_ms,
                            "path": request.url.path,
                        },
                    )
                )
        except Exception:
            logger.debug("Could not persist request activity", exc_info=True)
