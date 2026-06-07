"""Application configuration module."""
import os
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
CONNECTED_DATABASE_SSL_MODES = {
    "disable",
    "allow",
    "prefer",
    "require",
    "verify-ca",
    "verify-full",
}
CONNECTED_DATABASE_TLS_MODES = {"require", "verify-ca", "verify-full"}


def _resolve_env_file() -> Path:
    configured_env_file = os.getenv("VAYENT_ENV_FILE")
    if not configured_env_file:
        return BASE_DIR / ".env"

    env_file = Path(configured_env_file).expanduser()
    if not env_file.is_absolute():
        env_file = BASE_DIR / env_file
    return env_file


ENV_FILE = _resolve_env_file()


def _has_env_value(name: str) -> bool:
    return bool(os.getenv(name, "").strip())


def _env_flag_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def normalize_connected_database_ssl_mode(value: str | None) -> str | None:
    """Normalize asyncpg/libpq-style SSL mode names."""
    if value is None:
        return None

    normalized = str(value).strip().lower().replace("_", "-")
    if not normalized:
        return None
    if normalized not in CONNECTED_DATABASE_SSL_MODES:
        modes = ", ".join(sorted(CONNECTED_DATABASE_SSL_MODES))
        raise ValueError(f"Connected database SSL mode must be one of: {modes}")
    return normalized


def _is_path_under(path: Path, parent: Path) -> bool:
    try:
        resolved_path = path.resolve(strict=False)
        resolved_parent = parent.resolve(strict=False)
        return resolved_path == resolved_parent or resolved_parent in resolved_path.parents
    except OSError:
        return False


def is_serverless_runtime() -> bool:
    """Detect runtimes where the deployed application directory is read-only."""
    cwd = Path.cwd()
    return (
        _env_flag_enabled("VERCEL")
        or _has_env_value("VERCEL_ENV")
        or _has_env_value("VERCEL_URL")
        or _has_env_value("AWS_LAMBDA_FUNCTION_NAME")
        or _has_env_value("LAMBDA_TASK_ROOT")
        or _is_path_under(cwd, Path("/var/task"))
    )


def _serverless_tmp_path(name: str) -> str:
    safe_name = name.strip() or "vayent_data"
    return str(Path(tempfile.gettempdir()) / safe_name)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    # Application
    app_name: str = "Vayent"
    app_env: str = "development"
    debug: bool = False
    secret_key: str = "dev-only-change-me-before-deploy"
    credential_encryption_key: str = ""
    algorithm: str = "HS256"
    access_token_expiration_minutes: int = 15
    refresh_token_expiration_days: int = 7
    frontend_login_uri: str = "http://localhost:3000/login"
    frontend_app_uri: str = "http://localhost:3000"
    api_docs_enabled: bool = True
    trusted_hosts: List[str] | str = [
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
    ]
    auto_create_tables: bool = True
    admin_bootstrap_emails: List[str] | str = []

    # Refresh cookie
    refresh_cookie_name: str = "vayent_refresh_token"
    refresh_cookie_secure: bool = False
    refresh_cookie_samesite: str = "lax"
    refresh_cookie_domain: str = ""

    # Database
    database_url: str = Field(
        "postgresql://postgres:postgres@localhost:5432/relix",
        validation_alias=AliasChoices(
            "database_url",
            "DATABASE_URL",
            "POSTGRES_URL",
            "POSTGRES_PRISMA_URL",
            "POSTGRES_URL_NON_POOLING",
        ),
    )
    database_pool_size: int = 20
    database_max_overflow: int = 40
    database_pool_recycle: int = 3600

    # GitHub OAuth
    github_client_id: str = ""
    github_client_secret: str = ""
    github_redirect_uri: str = "http://localhost:8000/auth/github/callback"

    # Google OAuth
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/auth/google/callback"

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-5.5"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_timeout_seconds: int = 30
    openai_connect_timeout_seconds: int = 3
    openai_max_retries: int = 2
    free_daily_token_limit: int = 50000
    paid_daily_token_limit: int = 0
    free_monthly_token_limit: Optional[int] = None
    paid_monthly_token_limit: Optional[int] = None
    chat_completion_token_budget: int = 100

    # Email notifications
    email_notifications_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_from_name: str = "Vayent"
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False

    # Chroma Vector Database
    chroma_db_path: str = "./chroma_data"
    chroma_collection_name: str = "vayent_schemas"

    # Aethex Voice Assistant
    aethex_api_key: str = ""
    aethex_agent_id: str = ""
    aethex_base_url: str = "https://api.aethexai.com/api/v1"
    aethex_timeout_seconds: int = 30
    voice_query_timeout_seconds: int = 18

    # Logging
    log_level: str = "INFO"
    log_file: str = "./logs/app.log"
    activity_log_file: str = ""
    log_sql_statements: bool = False

    # CORS
    allowed_origins: List[str] | str = [
        "http://localhost:3000",
        "http://localhost:8000",
    ]

    # Rate Limiting
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 100
    rate_limit_window_seconds: int = 60

    # Query safety
    query_timeout_seconds: int = 30
    max_query_length: int = 10000
    max_result_rows: int = 500
    allow_destructive_queries: bool = False
    connected_database_read_only: bool = True
    require_connected_database_tls: bool = False
    connected_database_ssl_mode: str = ""
    allow_private_database_hosts: bool = False
    allowed_database_host_suffixes: List[str] | str = []
    blocked_database_hosts: List[str] | str = [
        "169.254.169.254",
        "metadata.google.internal",
        "metadata",
        "100.100.100.200",
    ]
    production_write_acknowledgement: str = ""
    metric_monitoring_enabled: bool = False

    # Spreadsheet sources
    spreadsheet_max_file_size_mb: int = 20
    spreadsheet_max_rows: int = 5000
    spreadsheet_preview_rows: int = 500
    spreadsheet_link_timeout_seconds: int = 20
    allow_private_spreadsheet_urls: bool = False
    blocked_spreadsheet_hosts: List[str] | str = [
        "169.254.169.254",
        "metadata.google.internal",
        "metadata",
        "100.100.100.200",
    ]

    # Health and operations
    health_check_timeout_seconds: int = 20

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() == "production"

    @property
    def github_oauth_enabled(self) -> bool:
        return bool(self.github_client_id.strip() and self.github_client_secret.strip())

    @property
    def google_oauth_enabled(self) -> bool:
        return bool(self.google_client_id.strip() and self.google_client_secret.strip())

    @property
    def openai_configured(self) -> bool:
        return bool(self.openai_api_key.strip())

    @property
    def aethex_configured(self) -> bool:
        return bool(self.aethex_api_key.strip())

    @property
    def default_connected_database_ssl_mode(self) -> str:
        mode = normalize_connected_database_ssl_mode(self.connected_database_ssl_mode)
        if mode:
            return mode
        return "require" if self.require_connected_database_tls else "prefer"

    @field_validator("debug", mode="before")
    @classmethod
    def parse_debug_value(cls, value):
        """Accept common string values from loose env files."""
        if isinstance(value, bool):
            return value

        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on", "debug", "development"}:
                return True
            if normalized in {"0", "false", "no", "off", "release", "production"}:
                return False

        return value

    @field_validator(
        "allowed_origins",
        "admin_bootstrap_emails",
        "trusted_hosts",
        "allowed_database_host_suffixes",
        "blocked_database_hosts",
        "blocked_spreadsheet_hosts",
        mode="before",
    )
    @classmethod
    def parse_csv_list(cls, value):
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("connected_database_ssl_mode", mode="before")
    @classmethod
    def normalize_connected_database_ssl_setting(cls, value):
        return normalize_connected_database_ssl_mode(value) or ""

    @field_validator("refresh_cookie_samesite", mode="before")
    @classmethod
    def normalize_same_site(cls, value):
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"lax", "strict", "none"}:
                return normalized
        return value

    @model_validator(mode="after")
    def validate_runtime_settings(self):
        """Apply stronger production validation rules."""
        chroma_path = Path(self.chroma_db_path)
        if is_serverless_runtime() and (
            not chroma_path.is_absolute()
            or _is_path_under(chroma_path, Path("/var/task"))
        ):
            self.chroma_db_path = _serverless_tmp_path(chroma_path.name or "chroma_data")

        if self.free_monthly_token_limit is not None:
            self.free_daily_token_limit = self.free_monthly_token_limit

        if self.paid_monthly_token_limit is not None:
            self.paid_daily_token_limit = self.paid_monthly_token_limit

        if bool(self.github_client_id.strip()) != bool(self.github_client_secret.strip()):
            raise ValueError(
                "GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET must be configured together"
            )

        if bool(self.google_client_id.strip()) != bool(self.google_client_secret.strip()):
            raise ValueError(
                "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be configured together"
            )

        if self.email_notifications_enabled:
            missing_email_settings = [
                name
                for name, value in (
                    ("SMTP_HOST", self.smtp_host),
                    ("SMTP_USERNAME", self.smtp_username),
                    ("SMTP_PASSWORD", self.smtp_password),
                    ("SMTP_FROM_EMAIL", self.smtp_from_email),
                )
                if not str(value).strip()
            ]
            if missing_email_settings:
                raise ValueError(
                    "Email notifications require the following settings: "
                    + ", ".join(missing_email_settings)
                )

        if self.smtp_use_ssl and self.smtp_use_tls:
            raise ValueError("SMTP_USE_SSL and SMTP_USE_TLS cannot both be enabled")

        if self.is_production:
            if self.debug:
                raise ValueError("DEBUG must be disabled in production")

            if self.api_docs_enabled:
                raise ValueError("API_DOCS_ENABLED must be disabled in production")

            if self.secret_key == "dev-only-change-me-before-deploy" or len(self.secret_key) < 32:
                raise ValueError(
                    "SECRET_KEY must be set to a strong production value")

            default_database_url = type(self).model_fields["database_url"].default
            if (
                len(self.database_url.strip()) == 0
                or self.database_url.strip() == default_database_url
            ):
                raise ValueError(
                    "DATABASE_URL must be configured in production")

            if not self.github_oauth_enabled and not self.google_oauth_enabled:
                raise ValueError(
                    "At least one OAuth provider must be configured in production"
                )

            if not self.openai_configured:
                raise ValueError(
                    "OPENAI_API_KEY must be configured in production")

            if not self.credential_encryption_key or len(self.credential_encryption_key) < 32:
                raise ValueError(
                    "CREDENTIAL_ENCRYPTION_KEY must be configured with at least 32 characters in production"
                )

            if self.auto_create_tables:
                raise ValueError(
                    "AUTO_CREATE_TABLES must be disabled in production")

            if not self.refresh_cookie_secure:
                raise ValueError(
                    "REFRESH_COOKIE_SECURE must be enabled in production")

            if "*" in self.allowed_origins:
                raise ValueError(
                    "ALLOWED_ORIGINS must list exact origins in production")

            insecure_origins = [
                origin
                for origin in self.allowed_origins
                if origin.startswith("http://")
            ]
            if insecure_origins:
                raise ValueError(
                    "ALLOWED_ORIGINS must use https origins in production: "
                    + ", ".join(insecure_origins)
                )

            if "*" in self.trusted_hosts:
                raise ValueError(
                    "TRUSTED_HOSTS must list exact hostnames in production")

            if not self.trusted_hosts:
                raise ValueError("TRUSTED_HOSTS must be configured in production")

            if self.default_connected_database_ssl_mode not in CONNECTED_DATABASE_TLS_MODES:
                raise ValueError(
                    "CONNECTED_DATABASE_SSL_MODE must require TLS in production")

            if self.allow_destructive_queries:
                if self.connected_database_read_only:
                    raise ValueError(
                        "CONNECTED_DATABASE_READ_ONLY must be disabled before enabling destructive queries"
                    )
                if (
                    self.production_write_acknowledgement
                    != "I_ACCEPT_PRODUCTION_DATABASE_WRITE_RISK"
                ):
                    raise ValueError(
                        "PRODUCTION_WRITE_ACKNOWLEDGEMENT must be set before enabling production write queries"
                    )

            if self.refresh_cookie_samesite == "none" and not self.refresh_cookie_secure:
                raise ValueError(
                    "REFRESH_COOKIE_SECURE must be enabled when REFRESH_COOKIE_SAMESITE is 'none'"
                )

        return self


@lru_cache()
def get_settings() -> Settings:
    """Get application settings (cached)."""
    return Settings()
