# Vayent API

Vayent API is a FastAPI backend that lets users connect PostgreSQL/MySQL databases and interact with them using natural language.
It plans database-aware responses, generates SQL when needed, executes safe queries, and logs query activity.

## Features

- GitHub and Google OAuth authentication
- PostgreSQL and MySQL connection management
- Schema discovery and schema-aware retrieval (RAG via Chroma)
- AI-assisted SQL planning and generation (OpenAI)
- Query safety checks, read-only production defaults, and scoped destructive-query confirmation
- Session-based chat tied to a specific connection
- Query logs with status, timing, and row counts

## Repository Layout

```text
vayent-api/
  app/
    ai/            AI planning + narration
    auth/          JWT and OAuth helpers
    db_connectors/ DB execution adapters
    models/        SQLAlchemy models
    rag/           Chroma-backed schema retrieval
    routers/       FastAPI routes
    safety/        Query validation
    services/      Business logic
    config.py      Settings (env-driven)
    database.py    Async SQLAlchemy engine/session
    main.py        FastAPI app
  tests/
  .env.example
  requirements.txt
  start_dev.bat
```

## Prerequisites

- Python 3.11+ (the repo is tested locally on newer versions too)
- PostgreSQL (application database)
- Node.js (only if you are running the web app)
- OpenAI API key (for AI features)
- GitHub/Google OAuth credentials (for sign-in)

## Setup (Windows)

From `vayent-api`:

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Edit `.env` for your environment.

Start the API:

```powershell
cmd /c start_dev.bat
```

The API runs at `http://localhost:8000`.

Useful endpoints:

- `http://localhost:8000/health`
- `http://localhost:8000/docs`

## Environment Variables

The `Settings` class in `app/config.py` loads configuration from `vayent-api/.env` by default. For production launches, set `VAYENT_ENV_FILE=.env.production` or use the included `start_prod.sh`/`start_prod.bat` scripts, which do that by default.

Required (local dev):

```env
DATABASE_URL=postgresql://user:pass@localhost:5432/relix
SECRET_KEY=your-secret-key
CREDENTIAL_ENCRYPTION_KEY=your-credential-encryption-key
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5.5
```

Optional OpenAI networking controls (useful behind proxies/firewalls):

```env
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_TIMEOUT_SECONDS=30
OPENAI_CONNECT_TIMEOUT_SECONDS=3
OPENAI_MAX_RETRIES=2
CHAT_COMPLETION_TOKEN_BUDGET=100
```

Token limits:

```env
FREE_DAILY_TOKEN_LIMIT=50000
PAID_DAILY_TOKEN_LIMIT=0
FREE_MONTHLY_TOKEN_LIMIT=50000
PAID_MONTHLY_TOKEN_LIMIT=0
```

Logging:

```env
# Defaults to ./logs/app.log, but this repo commonly uses ./vayent_logs/app.log for local runs
LOG_FILE=./vayent_logs/app.log
LOG_LEVEL=INFO
```

Production URLs and OAuth callbacks:

```env
APP_ENV=production
DEBUG=False
AUTO_CREATE_TABLES=False
API_DOCS_ENABLED=False
TRUSTED_HOSTS=api.yourdomain.com,app.yourdomain.com
ADMIN_BOOTSTRAP_EMAILS=admin@yourdomain.com
FRONTEND_APP_URI=https://app.yourdomain.com
FRONTEND_LOGIN_URI=https://app.yourdomain.com/login
ALLOWED_ORIGINS=https://app.yourdomain.com,https://api.yourdomain.com
GITHUB_REDIRECT_URI=https://api.yourdomain.com/auth/github/callback
GOOGLE_REDIRECT_URI=https://api.yourdomain.com/auth/google/callback
REFRESH_COOKIE_SECURE=True
REFRESH_COOKIE_SAMESITE=lax
REFRESH_COOKIE_DOMAIN=yourdomain.com
ALLOW_DESTRUCTIVE_QUERIES=False
CONNECTED_DATABASE_READ_ONLY=True
REQUIRE_CONNECTED_DATABASE_TLS=True
CONNECTED_DATABASE_SSL_MODE=require
METRIC_MONITORING_ENABLED=False
```

Use `REFRESH_COOKIE_SAMESITE=none` and leave `REFRESH_COOKIE_DOMAIN` empty
when the deployed frontend and API are on separate `vercel.app` hosts.

The first OAuth user is promoted to super admin automatically on a fresh database.
For existing databases, set `ADMIN_BOOTSTRAP_EMAILS` to one or more comma-separated admin emails before sign-in.

For the full production environment checklist, see
`../docs/PRODUCTION_DEPLOYMENT_CHECKLIST.md`.

## Testing

From `vayent-api`:

```powershell
venv\Scripts\python.exe -m pytest
```

## Troubleshooting

### AI service could not be reached

If `/health` reports `openai_reachable: false`, the machine running the API cannot reach the configured OpenAI endpoint.
Common causes are firewall/VPN/proxy rules blocking outbound HTTPS (port 443).

### Schema not discovered

- Verify database credentials in the saved connection
- Ensure the database has tables
- Re-run schema sync from the Connections page

### PostgreSQL server rejected SSL upgrade

For connected source databases, keep `CONNECTED_DATABASE_SSL_MODE=require` when
the provider supports TLS. If you are testing against a trusted database endpoint
that rejects Postgres SSL negotiation, choose `SSL disabled` in the Connections
form or set `CONNECTED_DATABASE_SSL_MODE=disable` for local testing only.
