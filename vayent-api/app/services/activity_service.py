"""Consistent application activity logging helpers."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.models import User

logger = logging.getLogger("app.activity")

SENSITIVE_DETAIL_KEYWORDS = {
    "access_token",
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "encrypted_password",
    "encrypted_username",
    "password",
    "refresh_token",
    "secret",
    "session_cookie",
    "token",
}


class ActivityService:
    """Emit structured activity log lines for important product events."""

    @staticmethod
    def preview_text(value: Any, limit: int = 180) -> str | None:
        """Collapse whitespace and trim verbose text for log readability."""
        if value is None:
            return None

        normalized = re.sub(r"\s+", " ", str(value)).strip()
        if not normalized:
            return None

        if len(normalized) <= limit:
            return normalized

        return normalized[: max(limit - 3, 1)].rstrip() + "..."

    def preview_sql(self, value: Any, limit: int = 280) -> str | None:
        """Generate a compact preview of SQL for support logs."""
        return self.preview_text(value, limit=limit)

    @staticmethod
    def _is_sensitive_key(key: str | None) -> bool:
        lowered = (key or "").strip().lower()
        return any(keyword in lowered for keyword in SENSITIVE_DETAIL_KEYWORDS)

    def _sanitize_value(self, value: Any, *, key: str | None = None) -> Any:
        if self._is_sensitive_key(key):
            return "[redacted]"

        if isinstance(value, dict):
            return {
                str(item_key): self._sanitize_value(item_value, key=str(item_key))
                for item_key, item_value in value.items()
            }

        if isinstance(value, (list, tuple, set)):
            values = list(value)
            sanitized = [self._sanitize_value(item, key=key) for item in values[:20]]
            if len(values) > 20:
                sanitized.append(f"... ({len(values) - 20} more)")
            return sanitized

        if isinstance(value, str):
            return self.preview_text(value, limit=320)

        return value

    def sanitize_details(self, details: dict[str, Any] | None) -> dict[str, Any]:
        """Scrub secrets and trim payloads before they hit support logs."""
        return self._sanitize_value(details or {})  # type: ignore[return-value]

    @staticmethod
    def exception_details(exc: Exception) -> dict[str, Any]:
        """Create a compact error payload for support traces."""
        return {
            "error_type": type(exc).__name__,
            "error": ActivityService.preview_text(str(exc), limit=320),
        }

    def log_event(
        self,
        *,
        action: str,
        status: str = "success",
        user: User | None = None,
        user_id: str | None = None,
        username: str | None = None,
        user_email: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        actor_id = getattr(user, "id", None) or user_id or "-"
        actor_username = getattr(user, "username", None) or username or "-"
        actor_email = getattr(user, "email", None) or user_email or "-"
        safe_details = json.dumps(
            self.sanitize_details(details),
            default=str,
            sort_keys=True,
        )

        log_method = logger.info
        if status.lower() in {"warning", "warn"}:
            log_method = logger.warning
        elif status.lower() in {"error", "failed", "failure"}:
            log_method = logger.error

        log_method(
            "action=%s status=%s actor_id=%s actor_username=%s actor_email=%s resource_type=%s resource_id=%s details=%s",
            action,
            status,
            actor_id,
            actor_username,
            actor_email,
            resource_type or "-",
            resource_id or "-",
            safe_details,
        )


activity_service = ActivityService()
