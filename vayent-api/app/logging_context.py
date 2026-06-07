"""Helpers for binding request-scoped values into log records."""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any


_DEFAULT_CONTEXT = {
    "request_id": "-",
    "user_id": "-",
    "username": "-",
    "user_email": "-",
    "request_method": "-",
    "request_path": "-",
    "client_ip": "-",
}

_context: dict[str, ContextVar[str]] = {
    key: ContextVar(key, default=value)
    for key, value in _DEFAULT_CONTEXT.items()
}


def bind_logging_context(**values: Any) -> None:
    """Bind request-local values for subsequent log records."""
    for key, value in values.items():
        if key in _context:
            _context[key].set(str(value or "-"))


def clear_logging_context() -> None:
    """Reset the logging context back to placeholder values."""
    for key, value in _DEFAULT_CONTEXT.items():
        _context[key].set(value)


class RequestContextFilter:
    """Populate log records with the active request context."""

    def filter(self, record) -> bool:
        for key, var in _context.items():
            setattr(record, key, var.get())
        return True
