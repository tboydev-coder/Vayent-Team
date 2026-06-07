"""Helpers for validating OpenAI configuration."""
from __future__ import annotations

import logging
import socket
import time
from urllib.parse import urlparse

from openai import AsyncOpenAI

from app.config import Settings

MISSING_OPENAI_API_KEY_MESSAGE = (
    "OPENAI_API_KEY is not configured. Set it in vayent-api/.env or the process "
    "environment and restart the API."
)


def build_async_openai_client(settings: Settings) -> AsyncOpenAI | None:
    """Create an async OpenAI client when the API key is available."""
    api_key = settings.openai_api_key.strip()
    if not api_key:
        return None
    return AsyncOpenAI(
        api_key=api_key,
        base_url=settings.openai_base_url.strip() or None,
        timeout=float(settings.openai_timeout_seconds),
        max_retries=int(settings.openai_max_retries),
    )


def build_chat_completion_controls(
    model: str,
    max_completion_tokens: int,
    *,
    reasoning_effort: str | None = None,
) -> dict[str, int | str]:
    """Return completion controls that work well with current chat models."""
    controls: dict[str, int | str] = {
        "max_completion_tokens": max_completion_tokens,
    }

    if model.strip().lower().startswith("gpt-5"):
        controls["reasoning_effort"] = reasoning_effort or "low"

    return controls


_OPENAI_REACHABILITY_CACHE: dict[tuple[str, int], tuple[float, str | None]] = {}
_OPENAI_REACHABILITY_TTL_SECONDS = 15.0
OPENAI_REACHABILITY_ERROR_MESSAGE = "AI service unreachable"


class OpenAIReachabilityError(RuntimeError):
    """Raised when the configured AI endpoint cannot be reached."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(OPENAI_REACHABILITY_ERROR_MESSAGE)


def _parse_base_url_host_port(base_url: str) -> tuple[str, int]:
    parsed = urlparse(base_url.strip() or "https://api.openai.com/v1")
    host = parsed.hostname or "api.openai.com"
    port = parsed.port or (443 if (parsed.scheme or "https").lower() == "https" else 80)
    return host, port


def get_openai_reachability_error(settings: Settings) -> str | None:
    """
    Return a short error string when the configured OpenAI base URL is not reachable.

    This is a fast TCP check and is cached briefly to avoid repeated socket work.
    """
    host, port = _parse_base_url_host_port(settings.openai_base_url)
    cache_key = (host, port)
    now = time.time()

    cached = _OPENAI_REACHABILITY_CACHE.get(cache_key)
    if cached and (now - cached[0]) < _OPENAI_REACHABILITY_TTL_SECONDS:
        return cached[1]

    timeout = float(getattr(settings, "openai_connect_timeout_seconds", 3) or 3)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            error = None
    except OSError as exc:
        # Keep details compact for logs and /health diagnostics.
        error = f"Cannot connect to {host}:{port} ({exc.__class__.__name__}: {exc})"

    _OPENAI_REACHABILITY_CACHE[cache_key] = (now, error)
    return error


def require_openai_reachable(
    settings: Settings,
    logger: logging.Logger | None = None,
) -> None:
    error = get_openai_reachability_error(settings)
    if not error:
        return

    if logger:
        logger.error("OpenAI endpoint unreachable: %s", error)

    raise OpenAIReachabilityError(error)


def require_openai_api_key(
    settings: Settings,
    logger: logging.Logger | None = None,
) -> str:
    """Return the configured API key or raise a clear configuration error."""
    api_key = settings.openai_api_key.strip()
    if api_key:
        return api_key

    if logger:
        logger.error(MISSING_OPENAI_API_KEY_MESSAGE)

    raise RuntimeError(MISSING_OPENAI_API_KEY_MESSAGE)
