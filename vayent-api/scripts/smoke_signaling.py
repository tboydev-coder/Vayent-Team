#!/usr/bin/env python3
"""
Smoke signaling test: call `voice.create_remote_voice_session` directly
(using the repo AETHEX_API_KEY / AETHEX_AGENT_ID) to verify Aethex
session creation works and returns session metadata.
"""
import os
import sys
import json
import asyncio
from pathlib import Path


def load_env_from_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass


if not os.environ.get("AETHEX_API_KEY") or not os.environ.get("AETHEX_AGENT_ID"):
    repo_root = Path(__file__).resolve().parents[1]
    env_path = repo_root / ".env"
    load_env_from_file(env_path)

# Import after loading env
from app.config import get_settings
from app.routers import voice
from types import SimpleNamespace

# Reload settings if cached and update voice module reference
try:
    get_settings.cache_clear()
except Exception:
    pass

settings = get_settings()
voice.settings = settings

async def main():
    fake_user = SimpleNamespace(id="user-123")
    try:
        resp = await voice.create_remote_voice_session(agent_id=None, current_user=fake_user)
        print(json.dumps(resp))
    except Exception as e:
        print("ERROR:", str(e), file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    asyncio.run(main())
