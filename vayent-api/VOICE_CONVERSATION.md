Voice Conversation feature

Server environment variables (minimum):

- AETHEX_API_KEY: your Aethex secret (DO NOT commit or expose)
- AETHEX_AGENT_ID: Aethex agent id to use (optional if agent chosen on create)
- AETHEX_BASE_URL: (optional override) default: https://api.aethexai.com/api/v1

Quick local run (development):

PowerShell:

```powershell
$env:APP_ENV='development'
# If you store secrets in a .env file, ensure you're not in production mode.
uvicorn "app.main:app" --host 0.0.0.0 --port 8000 --reload
```

Test the voice endpoints (JSON fallback):

```powershell
# returns capabilities
curl -H "Authorization: Bearer <token>" http://localhost:8000/voice/capabilities

# prepare a session for a connection
curl -X POST -H "Authorization: Bearer <token>" "http://localhost:8000/voice/start-session?connection_id=<conn-id>"

# send a text message (STT fallback)
curl -X POST -H "Authorization: Bearer <token> -H 'Content-Type: application/json' -d '{"connection_id":"<conn-id>","text":"how many orders"}' http://localhost:8000/voice/message
```

Notes:
- The server proxies Aethex session creation and SDP offers; the frontend must not hold Aethex API keys.
- Voice sessions are read-only by default; destructive SQL is rejected.
- For low-latency audio, use the WebRTC flow via `/voice/session` and `/voice/session/{sid}/offer` and let Aethex handle STT/TTS over the media channel.
