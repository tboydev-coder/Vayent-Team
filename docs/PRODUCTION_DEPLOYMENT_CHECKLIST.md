# Production Deployment Checklist

Use this checklist before every production push. The goal is to make production configuration explicit so a deploy does not fail because a new setting was only changed locally.

## Files And Secret Sources

Backend production template:

- `vayent-api/.env.production`

Frontend production template:

- `vayent-web/.env.production`

Docker Compose API environment:

- `vayent-api/docker-compose.yml`

For hosted production, prefer the host secret manager. Use the same variable names as the templates.

## Backend Environment Must Match Settings

`vayent-api/.env.example`, `vayent-api/.env.production`, and the API environment in `docker-compose.yml` should include every setting from `vayent-api/app/config.py`.

Current required production-sensitive values:

- `APP_ENV=production`
- `DEBUG=False`
- `DATABASE_URL`
- `SECRET_KEY`
- `CREDENTIAL_ENCRYPTION_KEY`
- `API_DOCS_ENABLED=False`
- `TRUSTED_HOSTS`
- `AUTO_CREATE_TABLES=False`
- `FRONTEND_LOGIN_URI`
- `FRONTEND_APP_URI`
- `ALLOWED_ORIGINS`
- `REFRESH_COOKIE_SECURE=True`
- `REFRESH_COOKIE_SAMESITE`
- `REFRESH_COOKIE_DOMAIN`
- at least one complete OAuth provider pair
- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `CHROMA_DB_PATH`
- `LOG_FILE`
- `ACTIVITY_LOG_FILE`

Optional but important production knobs:

- `ADMIN_BOOTSTRAP_EMAILS`
- `DATABASE_POOL_SIZE`
- `DATABASE_MAX_OVERFLOW`
- `DATABASE_POOL_RECYCLE`
- `OPENAI_BASE_URL`
- `OPENAI_TIMEOUT_SECONDS`
- `OPENAI_CONNECT_TIMEOUT_SECONDS`
- `OPENAI_MAX_RETRIES`
- `FREE_DAILY_TOKEN_LIMIT`
- `PAID_DAILY_TOKEN_LIMIT`
- `FREE_MONTHLY_TOKEN_LIMIT`
- `PAID_MONTHLY_TOKEN_LIMIT`
- `CHAT_COMPLETION_TOKEN_BUDGET`
- `EMAIL_NOTIFICATIONS_ENABLED`
- all `SMTP_*` settings when email is enabled
- `RATE_LIMIT_ENABLED`
- `RATE_LIMIT_REQUESTS`
- `RATE_LIMIT_WINDOW_SECONDS`
- `QUERY_TIMEOUT_SECONDS`
- `MAX_QUERY_LENGTH`
- `MAX_RESULT_ROWS`
- `ALLOW_DESTRUCTIVE_QUERIES`
- `CONNECTED_DATABASE_READ_ONLY`
- `REQUIRE_CONNECTED_DATABASE_TLS`
- `ALLOW_PRIVATE_DATABASE_HOSTS`
- `ALLOWED_DATABASE_HOST_SUFFIXES`
- `BLOCKED_DATABASE_HOSTS`
- `PRODUCTION_WRITE_ACKNOWLEDGEMENT`
- `METRIC_MONITORING_ENABLED`
- `HEALTH_CHECK_TIMEOUT_SECONDS`

Do not leave integer variables blank. Use explicit numeric values, for example `FREE_MONTHLY_TOKEN_LIMIT=50000` and `PAID_MONTHLY_TOKEN_LIMIT=0`.

## Generate Secrets

Use different values for:

- `SECRET_KEY`
- `CREDENTIAL_ENCRYPTION_KEY`

Both must be strong and at least 32 characters. Do not reuse the development defaults.

## OAuth Checklist

GitHub:

- `GITHUB_CLIENT_ID`
- `GITHUB_CLIENT_SECRET`
- `GITHUB_REDIRECT_URI=https://api.yourdomain.com/auth/github/callback`

Google:

- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REDIRECT_URI=https://api.yourdomain.com/auth/google/callback`

The provider console must contain the exact callback URL used in the backend env.

## CORS And Cookie Checklist

Set:

```env
FRONTEND_APP_URI=https://app.yourdomain.com
FRONTEND_LOGIN_URI=https://app.yourdomain.com/login
ALLOWED_ORIGINS=https://app.yourdomain.com,https://api.yourdomain.com
TRUSTED_HOSTS=api.yourdomain.com,app.yourdomain.com
REFRESH_COOKIE_SECURE=True
REFRESH_COOKIE_SAMESITE=lax
REFRESH_COOKIE_DOMAIN=yourdomain.com
```

If frontend and API are on different registrable domains, such as separate
`vercel.app` hosts, use `REFRESH_COOKIE_SAMESITE=none` with
`REFRESH_COOKIE_SECURE=True` and leave `REFRESH_COOKIE_DOMAIN` empty.

## API Exposure Checklist

Set:

```env
API_DOCS_ENABLED=False
DEBUG=False
```

Production starts should fail if interactive API docs are enabled, wildcard trusted hosts are used, wildcard CORS is used, or an HTTP origin is configured. Keep `/docs`, `/redoc`, and `/openapi.json` available only in local development or behind a protected internal route.

## Frontend Build Checklist

Set `vayent-web/.env.production` before building:

```env
VITE_API_BASE_URL=https://api.yourdomain.com
VITE_METRIC_MONITORING_ENABLED=false
```

Then run:

```powershell
cd vayent-web
npm ci
npm run lint
npm run typecheck
npm run build
npm audit --omit=dev
```

Because Vite embeds `VITE_API_BASE_URL` at build time, changing the env after the build is not enough. Rebuild the frontend whenever the API origin changes.

Use env values for live changes. Production URLs, OAuth IDs/secrets, OpenAI credentials, SMTP credentials, bootstrap admins, database URLs, query safety posture, and feature flags should be changed in env files or the host secret manager, not in source code.

Do not run `npm audit fix --force` during a release without testing the resulting major upgrades.

## Frontend Security Headers

Configure these at the static host or CDN for the frontend origin:

- `Content-Security-Policy` with `default-src 'self'`, a restricted `connect-src` that includes only the production API origin, `frame-ancestors 'none'`, `object-src 'none'`, and no broad wildcard script sources.
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: no-referrer`
- `Permissions-Policy` disabling unused browser capabilities such as camera, microphone, geolocation, and payment.

Keep source maps private or upload them only to the intended error-reporting service.

## Backend Validation Checklist

Run config validation before starting production:

```powershell
cd vayent-api
$env:VAYENT_ENV_FILE=".env.production"
.\.venv\Scripts\python.exe -c "from app.config import get_settings; s=get_settings(); print(f'config ok: {s.app_env}')"
```

Then run checks:

```powershell
.\.venv\Scripts\python.exe -m compileall app
.\.venv\Scripts\python.exe -m pytest
```

## Database Checklist

Before first production start:

1. Create the production PostgreSQL database.
2. Set `DATABASE_URL` to the production database.
3. Set `AUTO_CREATE_TABLES=False`.
4. Run migrations against production:

```powershell
cd vayent-api
$env:VAYENT_ENV_FILE=".env.production"
alembic upgrade head
```

The app no longer runs runtime schema patching when `AUTO_CREATE_TABLES=False`; migrations are the production source of truth.

## Connected Production Database Safety

Default production posture should be read-only:

```env
ALLOW_DESTRUCTIVE_QUERIES=False
CONNECTED_DATABASE_READ_ONLY=True
REQUIRE_CONNECTED_DATABASE_TLS=True
ALLOW_PRIVATE_DATABASE_HOSTS=False
BLOCKED_DATABASE_HOSTS=169.254.169.254,metadata.google.internal,metadata,100.100.100.200
METRIC_MONITORING_ENABLED=False
```

Use a least-privilege database account for every connected source. Prefer a read-only account for production databases and grant write permissions only for a separate, tightly controlled connection.

If the deployment must connect to private VPC database addresses, set `ALLOW_PRIVATE_DATABASE_HOSTS=True` only for a private deployment where user-created connections are trusted. For a hosted multi-customer deployment, keep it `False` and use `ALLOWED_DATABASE_HOST_SUFFIXES` to restrict connections to approved database domains when possible.

Production write queries require all of the following explicit settings:

```env
ALLOW_DESTRUCTIVE_QUERIES=True
CONNECTED_DATABASE_READ_ONLY=False
PRODUCTION_WRITE_ACKNOWLEDGEMENT=I_ACCEPT_PRODUCTION_DATABASE_WRITE_RISK
```

Do not enable this unless backups, restore tests, audit review, role approvals, and an incident rollback plan are already in place.

## Storage And Logs

Ensure these paths exist and are writable by the API process:

- `CHROMA_DB_PATH`
- directory containing `LOG_FILE`
- directory containing `ACTIVITY_LOG_FILE`

For Docker Compose, the repo maps:

- `./logs:/app/logs`
- `./chroma_data:/app/chroma_data`

## Docker Compose Deploy

From `vayent-api`:

```powershell
docker compose --env-file .env.production up -d --build
```

Confirm the API container receives every variable in `docker-compose.yml`. Add new `Settings` fields to the Compose environment whenever they are introduced.

## Smoke Tests After Deploy

1. Open `https://api.yourdomain.com/health`.
2. Confirm `checks.database` is true.
3. Confirm `checks.openai_configured` is true.
4. Confirm `checks.openai_reachable` is true or investigate the reported reachability error.
5. Open the frontend and sign in.
6. Create or sync a test database connection.
7. Ask a read-only chat question.
8. Open **Logs** and confirm the query appears.
9. Open **Admin** as an admin and confirm dashboard data loads.
10. Confirm `/docs`, `/redoc`, and `/openapi.json` are not publicly reachable in production.
11. Confirm a destructive prompt is blocked unless the production write override is intentionally configured.

## When Adding New Configuration

Every new backend setting should be added in all of these places:

- `vayent-api/app/config.py`
- `vayent-api/.env.example`
- `vayent-api/.env.production`
- `vayent-api/docker-compose.yml`
- `docs/PRODUCTION_DEPLOYMENT_CHECKLIST.md`
- tests under `vayent-api/tests/` when validation rules change

Every new frontend env value should be added in:

- `vayent-web/.env.example`
- `vayent-web/.env.production`
- this checklist
- build/deploy documentation
