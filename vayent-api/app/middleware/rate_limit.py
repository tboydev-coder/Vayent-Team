"""Simple in-memory rate limiting middleware."""
from __future__ import annotations

from collections import defaultdict, deque
from time import monotonic

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Apply per-client request throttling."""

    def __init__(self, app):
        super().__init__(app)
        self.settings = get_settings()
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._exempt_paths = {"/health", "/status", "/docs", "/openapi.json", "/redoc"}

    async def dispatch(self, request: Request, call_next):
        if not self.settings.rate_limit_enabled or request.url.path in self._exempt_paths:
            return await call_next(request)

        client_host = request.client.host if request.client else "unknown"
        key = f"{client_host}:{request.url.path}"
        now = monotonic()
        bucket = self._buckets[key]
        window_start = now - self.settings.rate_limit_window_seconds

        while bucket and bucket[0] < window_start:
            bucket.popleft()

        if len(bucket) >= self.settings.rate_limit_requests:
            retry_after = max(1, int(bucket[0] + self.settings.rate_limit_window_seconds - now))
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded. Please retry shortly.",
                    "status_code": 429,
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(self.settings.rate_limit_requests),
                    "X-RateLimit-Remaining": "0",
                },
            )

        bucket.append(now)
        response = await call_next(request)
        remaining = max(0, self.settings.rate_limit_requests - len(bucket))
        response.headers["X-RateLimit-Limit"] = str(self.settings.rate_limit_requests)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response
