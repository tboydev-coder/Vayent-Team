"""
Vayent Backend API Application.

Production-grade API for AI-powered database interaction using natural language.
"""
import logging
import logging.config
import os
from pathlib import Path
from fastapi import FastAPI, Request, status, HTTPException as FastAPIHTTPException
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from contextlib import asynccontextmanager

from app.config import get_settings, is_serverless_runtime
from app.database import init_db, close_db
from app.logging_context import RequestContextFilter
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.request_context import RequestContextMiddleware
from app.routers import admin, auth, connections, chat, copilot, health, voice

settings = get_settings()


def _prepare_writable_directory(path: Path) -> bool:
    """Create the directory if needed and confirm file handlers can write there."""
    try:
        if path.exists():
            return path.is_dir() and os.access(path, os.W_OK)

        path.mkdir(parents=True, exist_ok=True)
        return path.is_dir() and os.access(path, os.W_OK)
    except OSError:
        return False


def _can_use_file_logging(*paths: Path) -> bool:
    if is_serverless_runtime():
        return False

    directories = {path.parent for path in paths}
    return all(_prepare_writable_directory(directory) for directory in directories)


def _build_logging_config() -> dict:
    log_level = settings.log_level.upper()
    sqlalchemy_log_level = (
        log_level
        if settings.log_sql_statements
        else "WARNING"
    )

    # -------------------------------------------------
    # SAFE FORMATTER
    # -------------------------------------------------
    class SafeFormatter(logging.Formatter):
        DEFAULTS = {
            "request_id": "-",
            "user_id": "-",
            "request_method": "-",
            "request_path": "-",
            "client_ip": "-",
            "username": "-",
            "user_email": "-",
        }

        def format(self, record):
            for key, value in self.DEFAULTS.items():
                if not hasattr(record, key):
                    setattr(record, key, value)

            return super().format(record)

    # -------------------------------------------------
    # PATHS
    # -------------------------------------------------
    log_file_path = Path(settings.log_file)
    activity_log_file = settings.activity_log_file.strip()

    activity_log_path = (
        Path(activity_log_file)
        if activity_log_file
        else log_file_path.with_name("activity.log")
    )
    file_logging_enabled = _can_use_file_logging(log_file_path, activity_log_path)

    # -------------------------------------------------
    # HANDLERS
    # -------------------------------------------------
    handlers = {
        "console": {
            "class": "logging.StreamHandler",
            "level": log_level,
            "formatter": "standard",
            "filters": ["request_context"],
            "stream": "ext://sys.stdout",
        }
    }

    if file_logging_enabled:
        handlers["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "level": log_level,
            "formatter": "readable",
            "filters": ["request_context"],
            "filename": str(log_file_path),
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 5,
            "encoding": "utf-8",
            "delay": True,
        }

        handlers["activity_file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "level": log_level,
            "formatter": "activity",
            "filters": ["request_context"],
            "filename": str(activity_log_path),
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 5,
            "encoding": "utf-8",
            "delay": True,
        }

    # -------------------------------------------------
    # ROOT HANDLERS
    # -------------------------------------------------
    root_handlers = ["console"]

    if file_logging_enabled:
        root_handlers.append("file")

    # -------------------------------------------------
    # APP ACTIVITY HANDLERS
    # -------------------------------------------------
    activity_handlers = (
        ["activity_file"]
        if file_logging_enabled
        else ["console"]
    )

    return {
        "version": 1,
        "disable_existing_loggers": False,

        # -------------------------------------------------
        # FILTERS
        # -------------------------------------------------
        "filters": {
            "request_context": {
                "()": RequestContextFilter,
            }
        },

        # -------------------------------------------------
        # FORMATTERS
        # -------------------------------------------------
        "formatters": {
            "standard": {
                "()": SafeFormatter,
                "format": (
                    "%(asctime)s [%(levelname)s] %(name)s "
                    "req=%(request_id)s user=%(user_id)s "
                    "method=%(request_method)s "
                    "path=%(request_path)s "
                    "ip=%(client_ip)s: %(message)s"
                ),
            },
            "readable": {
                "()": SafeFormatter,
                "format": (
                    "%(asctime)s [%(levelname)s] %(name)s "
                    "req=%(request_id)s user=%(user_id)s "
                    "method=%(request_method)s "
                    "path=%(request_path)s: %(message)s"
                ),
            },
            "activity": {
                "()": SafeFormatter,
                "format": (
                    "%(asctime)s [%(levelname)s] %(message)s"
                ),
            },
        },

        # -------------------------------------------------
        # HANDLERS
        # -------------------------------------------------
        "handlers": handlers,

        # -------------------------------------------------
        # ROOT LOGGER
        # -------------------------------------------------
        "root": {
            "handlers": root_handlers,
            "level": log_level,
        },

        # -------------------------------------------------
        # THIRD-PARTY LOGGERS
        # -------------------------------------------------
        "loggers": {
            "sqlalchemy.engine": {
                "level": sqlalchemy_log_level,
                "propagate": True,
            },
            "sqlalchemy.pool": {
                "level": sqlalchemy_log_level,
                "propagate": True,
            },
            "uvicorn.access": {
                "level": "WARNING"
            },
            "httpx": {
                "level": "WARNING"
            },
            "httpcore": {
                "level": "WARNING"
            },
            "aiomysql": {
                "level": "WARNING"
            },
            "asyncpg": {
                "level": "WARNING"
            },
            "app.activity": {
                "handlers": activity_handlers,
                "level": log_level,
                "propagate": False,
            },
        },
    }


logging.config.dictConfig(_build_logging_config())
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for startup and shutdown events.
    """
    # Startup
    logger.info("Starting Vayent API...")
    if not settings.openai_configured:
        logger.warning(
            "OPENAI_API_KEY is not configured. Chat, copilot, and schema embedding "
            "features will return a configuration error until it is set."
        )
    try:
        await init_db()
        logger.info("Database initialized")
    except Exception as e:
        if not is_serverless_runtime():
            logger.error(f"Failed to initialize database: {e}")
            raise
        logger.warning("Database unavailable at startup: %s", e)
        logger.warning(
            "Continuing startup in degraded mode; database-backed routes "
            "will return 503 until DATABASE_URL is fixed."
        )

    yield

    # Shutdown
    logger.info("Shutting down Vayent API...")
    try:
        await close_db()
        logger.info("Database connections closed")
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")


# Create FastAPI app
app = FastAPI(
    title="Vayent API",
    description="AI-powered natural language database interaction system",
    version="1.0.0",
    docs_url="/docs" if settings.api_docs_enabled else None,
    redoc_url="/redoc" if settings.api_docs_enabled else None,
    openapi_url="/openapi.json" if settings.api_docs_enabled else None,
    lifespan=lifespan,
)

if settings.trusted_hosts:
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=settings.trusted_hosts,
    )

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestContextMiddleware)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Attach browser security headers for API responses."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=(), payment=()",
    )
    if settings.is_production:
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
        )
    return response


# Custom exception handler
@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Handle unexpected exceptions.

    Allow HTTPException instances to return their original status and detail
    so clients receive meaningful error messages instead of a generic 500.
    """
    # Pass through HTTP exceptions with their original details
    if isinstance(exc, (FastAPIHTTPException, StarletteHTTPException)):
        try:
            detail = exc.detail
        except Exception:
            detail = str(exc)
        status_code = getattr(exc, "status_code", status.HTTP_500_INTERNAL_SERVER_ERROR)
        return JSONResponse(
            status_code=status_code,
            content={"detail": detail, "status_code": status_code},
        )

    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "Internal server error",
            "status_code": 500,
        },
    )


# Include routers
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(connections.router)
app.include_router(chat.router)
app.include_router(copilot.router)
app.include_router(admin.router)
app.include_router(voice.router)


# Root endpoint
@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "message": "Welcome to Vayent API",
        "version": "1.0.0",
        "docs": "/docs" if settings.api_docs_enabled else None,
        "redoc": "/redoc" if settings.api_docs_enabled else None,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
        log_level=settings.log_level.lower(),
    )
