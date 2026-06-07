# Vayent Developer Guide

This guide explains how Vayent is built, which technologies power each feature, and where to make changes safely.

## Repository Layout

```text
relix app/
|-- .github/workflows/ci.yml
|-- docs/
|-- vayent-api/
|   |-- app/
|   |   |-- ai/
|   |   |-- auth/
|   |   |-- db_connectors/
|   |   |-- middleware/
|   |   |-- models/
|   |   |-- rag/
|   |   |-- routers/
|   |   |-- safety/
|   |   |-- schemas/
|   |   `-- services/
|   |-- migrations/
|   |-- tests/
|   |-- .env.example
|   `-- .env.production
`-- vayent-web/
    |-- public/
    |-- src/
    |   |-- components/
    |   |-- layouts/
    |   |-- pages/
    |   |-- services/
    |   |-- store/
    |   |-- styles/
    |   |-- types/
    |   `-- utils/
    |-- .env.example
    `-- .env.production
```

## Tech Stack

Frontend:

- React 18
- TypeScript
- Vite
- React Router
- TanStack React Query
- Zustand
- Axios
- Framer Motion
- CSS modules by page-level stylesheet files

Backend:

- FastAPI
- Pydantic and Pydantic Settings
- SQLAlchemy async ORM
- AsyncPG for the Vayent application database
- Alembic migrations
- AioMySQL and AsyncPG-style connectors for customer databases
- OpenAI SDK for AI planning, SQL generation, and copilot outputs
- ChromaDB and LangChain text splitters for schema retrieval
- JWT access tokens plus HTTP-only refresh cookies
- GitHub and Google OAuth
- Uvicorn
- Docker Compose for API and PostgreSQL deployment

## Backend Startup Flow

`vayent-api/app/main.py` creates the FastAPI app.

Startup flow:

1. Load settings from `app/config.py`.
2. Build logging handlers and activity log output.
3. Start FastAPI lifespan.
4. Initialize the SQLAlchemy async engine in `app/database.py`.
5. Create or validate runtime schema.
6. Register middleware.
7. Include routers for health, auth, connections, chat, copilot, and admin.

Settings load from `vayent-api/.env` by default. Set `VAYENT_ENV_FILE=.env.production` or use `start_prod.sh` / `start_prod.bat` for production-style launches.

## Frontend Startup Flow

`vayent-web/src/main.tsx` mounts the React app.

`src/App.tsx` defines routes:

- `/` landing page
- `/login`
- `/dashboard`
- `/copilot`
- `/connections`
- `/connections/:id/schema`
- `/workspace`
- `/chat`
- `/chat/:sessionId`
- `/logs`
- `/admin/*`

`src/services/api.ts` creates Axios clients. It reads `VITE_API_BASE_URL` at build time and falls back to `http://localhost:8000` for local development.

Authentication state lives in `src/store/auth.ts`. The app stores the access token in Zustand state and uses the backend refresh cookie to restore sessions.

## Feature Architecture

### Authentication

Backend:

- `app/routers/auth.py`
- `app/services/auth_service.py`
- `app/auth/github_oauth.py`
- `app/auth/google_oauth.py`
- `app/auth/jwt.py`
- `app/auth/dependencies.py`

Frontend:

- `src/pages/LoginPage.tsx`
- `src/store/auth.ts`
- `src/services/api.ts`
- `src/components/TopNav.tsx`

The backend handles OAuth redirects and code exchange, issues short-lived access tokens, and sets a refresh cookie. The frontend calls `/auth/me` after token refresh to hydrate the user profile.

### Connections And Schema Sync

Backend:

- `app/routers/connections.py`
- `app/services/db_connection_service.py`
- `app/services/schema_discovery_service.py`
- `app/db_connectors/connector.py`
- `app/rag/rag_service.py`

Frontend:

- `src/pages/ConnectionsPage.tsx`
- `src/pages/SchemaPage.tsx`
- `src/components/ConnectionCard.tsx`
- `src/components/SchemaErd.tsx`

Connection credentials are encrypted by `EncryptionService`. Schema sync introspects the target database, persists tables/columns/relationships, and adds schema text into Chroma for retrieval.

### Schema Annotations

Backend:

- `SchemaAnnotation` model in `app/models/models.py`
- annotation request/response types in `app/schemas/schemas.py`
- annotation logic in `app/services/schema_discovery_service.py`
- route handler in `app/routers/connections.py`

Frontend:

- annotation forms in `src/pages/SchemaPage.tsx`

Annotations enrich AI prompts with business descriptions, table notes, and column nicknames.

### Chat

Backend:

- `app/routers/chat.py`
- `app/ai/sql_generation.py`
- `app/services/query_execution_service.py`
- `app/safety/query_validator.py`
- `app/services/token_usage_service.py`

Frontend:

- `src/pages/ChatStartPage.tsx`
- `src/pages/ChatPage.tsx`
- `src/components/ChatMessage.tsx`
- `src/components/QueryResultTable.tsx`

Chat creates a session for one connection. The AI planner decides whether the prompt needs SQL, clarification, or business guidance. Safe read-only queries execute immediately. Destructive queries are disabled by default in production; when explicitly enabled, they create a confirmation token scoped to the user and connection and require `/chat/confirm-query`.

### Workspace

Backend:

- `/chat/workspace/message` in `app/routers/chat.py`
- workspace planning in `app/ai/sql_generation.py`

Frontend:

- `src/pages/WorkspacePage.tsx`
- `src/utils/activeConnection.ts`

Workspace allows multi-connection prompts. The frontend sends selected connection IDs, an active fallback connection, and recent history. The backend chooses target connections and returns generated queries, results, warnings, and explanation.

### Copilot

Backend:

- `app/routers/copilot.py`
- `app/services/copilot_service.py`
- `CopilotArtifact`, `CopilotMemory`, and `CopilotWatchlist` models

Frontend:

- `src/pages/CopilotPage.tsx`

Copilot creates persisted analytical artifacts: investigations, briefings, recommendations, scenarios, dashboards, and memories. Metric monitoring/watchlists are behind `METRIC_MONITORING_ENABLED` and are disabled for the live release. It uses schema context, saved business memory, OpenAI structured JSON, read-only evidence queries, and token metering.

### Query Safety And Logs

Backend:

- `app/safety/query_validator.py`
- `app/services/query_execution_service.py`
- `QueryLog` and `QueryConfirmation` models
- `/chat/query-logs`, `/chat/query-logs/paginated`, and `/chat/query-stats`

Frontend:

- `src/pages/LogsPage.tsx`
- dashboard recent query widgets

The safety validator detects destructive SQL, multiple statements, and risky patterns. Query logs store status, timing, row count, errors, and execution time.

### Admin Dashboard

Backend:

- `app/routers/admin.py`
- `app/services/admin_dashboard_service.py`
- admin models in `app/models/models.py`
- admin schemas in `app/schemas/schemas.py`
- migration `migrations/versions/004_admin_dashboard.py`

Frontend:

- `src/pages/AdminDashboardPage.tsx`
- `src/styles/adminDashboard.css`
- admin TypeScript interfaces in `src/types/index.ts`

Admin dashboard data is aggregated by `AdminDashboardService.dashboard`. The frontend polls every 30 seconds and uses `/admin/ws` for live monthly dashboard snapshots when a token is available.

Mutating admin APIs require the `X-Admin-CSRF: 1` header. Super-admin-only operations include role changes and feature flag updates.

### Notifications

Backend:

- `app/services/notification_service.py`
- SMTP settings in `app/config.py`
- auth and connection routes queue notification tasks

Email notifications are optional. If `EMAIL_NOTIFICATIONS_ENABLED=True`, all required SMTP values must be present or settings validation fails.

### Activity Logging

Backend:

- `app/middleware/request_context.py`
- `app/logging_context.py`
- `app/services/activity_service.py`
- `ActivityLog` model

Activity logs power the admin dashboard, security view, support view, and CSV export.

## Database Model Overview

Core application tables:

- `users`
- `oauth_accounts`
- `database_connections`
- `database_schemas`
- `table_metadata`
- `column_metadata`
- `schema_annotations`
- `chat_sessions`
- `chat_messages`
- `query_logs`
- `query_confirmations`
- `token_usage_logs`
- `copilot_artifacts`
- `copilot_memories`
- `copilot_watchlists`
- `activity_logs`
- `token_adjustment_logs`
- `admin_notifications`
- `feature_flags`

Production deployments should use Alembic migrations. `AUTO_CREATE_TABLES` must be `False` in production.

## How To Modify The Admin Dashboard

Backend steps:

1. If the change needs stored data, add or update the SQLAlchemy model in `app/models/models.py`.
2. Add an Alembic migration under `vayent-api/migrations/versions/`.
3. Add request/response schemas in `app/schemas/schemas.py`.
4. Add query or mutation logic in `app/services/admin_dashboard_service.py`.
5. Expose the endpoint in `app/routers/admin.py`.
6. For writes, use `Depends(require_admin_csrf)`.
7. Use `get_current_admin_user` for admin actions and `get_current_super_admin_user` for super-admin-only actions.
8. Log sensitive admin changes through `admin_dashboard_service.log_admin_action` or `activity_service`.
9. Add or update backend tests in `vayent-api/tests/`.

Frontend steps:

1. Add or update TypeScript interfaces in `vayent-web/src/types/index.ts`.
2. Add a React Query `useQuery` or `useMutation` in `src/pages/AdminDashboardPage.tsx`.
3. For admin writes, include `adminHeaders` so `X-Admin-CSRF: 1` is sent.
4. Add the UI section or controls in `AdminDashboardPage.tsx`.
5. Style the section in `src/styles/adminDashboard.css`.
6. Invalidate admin queries after successful writes.
7. Run `npm run typecheck` and `npm run build`.

## Environment Configuration

Backend settings live in `vayent-api/app/config.py`.

Tracked templates:

- `vayent-api/.env.example`
- `vayent-api/.env.production`
- `vayent-web/.env.example`
- `vayent-web/.env.production`

The backend templates intentionally include every `Settings` field. When a new setting is added to `app/config.py`, add it to both backend env templates and to `docker-compose.yml` if the API container needs it.

Frontend env variables are build-time values. If `VITE_API_BASE_URL` changes, rebuild the frontend before deploying.

## Local Development

Backend:

```powershell
cd vayent-api
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
copy .env.example .env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Frontend:

```powershell
cd vayent-web
npm install
npm run dev
```

## Verification Commands

Backend:

```powershell
cd vayent-api
.\.venv\Scripts\python.exe -m compileall app
.\.venv\Scripts\python.exe -m pytest
```

Frontend:

```powershell
cd vayent-web
npm run lint
npm run typecheck
npm run build
```

Production config validation:

```powershell
cd vayent-api
$env:VAYENT_ENV_FILE=".env.production"
.\.venv\Scripts\python.exe -c "from app.config import get_settings; s=get_settings(); print(f'config ok: {s.app_env}')"
```
