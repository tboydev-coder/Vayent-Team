# Vayent - Quick Start Guide

## 5-Minute Setup

### 1. Prerequisites

- Python 3.9+
- PostgreSQL running on localhost:5432
- OpenAI API key
- GitHub OAuth credentials

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Create .env File

```bash
cp .env.example .env
```

Edit `.env` and set:

```env
DATABASE_URL=postgresql://postgres:password@localhost:5432/relix
OPENAI_API_KEY=sk-...
GITHUB_CLIENT_ID=your_id
GITHUB_CLIENT_SECRET=your_secret
SECRET_KEY=your-secret-key
```

### 4. Start the Server

```bash
# Windows
start_dev.bat

# Linux/Mac
bash start_dev.sh
```

Server runs at: **http://localhost:8000**

### 5. Access Documentation

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

## Example Usage

### 1. Authenticate

```bash
curl -X POST "http://localhost:8000/auth/github" \
  -H "Content-Type: application/json" \
  -d '{"code": "github_code", "state": "state"}'
```

\*If you already possess a **GitHub** access token (for example a
personal access token or a previously obtained OAuth token) you can pass
it directly instead of the code. **Do not accidentally POST the JWT you
receive from the API – the field must contain the GitHub token, not the
Vayent token returned by a previous login.\***

```bash
curl -X POST "http://localhost:8000/auth/github" \
  -H "Content-Type: application/json" \
  -d '{"access_token": "gho_XXXXXXXXXXXXXXXX"}'
```

### 2. Create Database Connection

```bash
curl -X POST "http://localhost:8000/connections" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My Database",
    "db_type": "postgresql",
    "host": "localhost",
    "port": 5432,
    "database_name": "mydb",
    "username": "user",
    "password": "pass"
  }'
```

### 3. Create Chat Session

```bash
curl -X POST "http://localhost:8000/chat/sessions" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "connection_id": "connection_uuid",
    "title": "My Chat"
  }'
```

### 4. Send Query

```bash
curl -X POST "http://localhost:8000/chat/sessions/session_uuid/messages" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "user_prompt": "Show me all users created in the last month"
  }'
```

Response includes:

- Generated SQL
- Query results
- Natural language explanation
- Confirmation token (if destructive)

## Key Features

### Read Queries (Auto-Execute)

- SELECT statements
- Auto-executed and results returned
- Natural language explanation generated

### Destructive Queries (Production-Blocked By Default)

- INSERT, UPDATE, DELETE
- Blocked by default in production
- Return confirmation token only when server write policy is explicitly enabled
- Execute with token confirmation when writes are enabled

### Safety Features

- All queries validated
- SQL injection detection
- Query logging for audit
- Read-only production defaults for connected databases
- User confirmation required for data modifications when writes are enabled

## File Structure

```
app/
├── main.py              # FastAPI application
├── config.py            # Configuration
├── database.py          # Database setup
├── auth/                # Authentication
├── models/              # Database models
├── schemas/             # Request/response schemas
├── services/            # Business logic
├── ai/                  # AI/LLM integration
├── rag/                 # Vector database
├── db_connectors/       # Database connectors
├── safety/              # Query validation
└── routers/             # API endpoints
```

## Configuration

All settings in `.env`:

- `DATABASE_URL`: PostgreSQL connection string
- `OPENAI_API_KEY`: OpenAI API key
- `GITHUB_CLIENT_ID/SECRET`: GitHub OAuth credentials
- `SECRET_KEY`: JWT signing key
- `CHROMA_DB_PATH`: Vector database directory
- `LOG_LEVEL`: DEBUG, INFO, WARNING, ERROR

## Troubleshooting

### Port already in use

```bash
# Change port in startup script or use:
python -m uvicorn app.main:app --port 8001
```

### Database connection failed

```bash
# Check PostgreSQL is running:
psql -U postgres -d relix

# Or verify connection string in .env
```

### Missing dependencies

```bash
pip install -r requirements.txt --upgrade
```

## Next Steps

1. Deploy to cloud (AWS, Azure, GCP)
2. Set up monitoring and logging
3. Configure Redis for caching
4. Add frontend application
5. Set up CI/CD pipeline

## Support

See main README.md for detailed documentation and API reference.
