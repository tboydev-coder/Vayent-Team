#!/usr/bin/env python3
"""
Check Aethex /conversation/connect using AETHEX_API_KEY and AETHEX_AGENT_ID from .env or env.
"""
import os
import sys
import json
import urllib.request
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

API_KEY = os.environ.get("AETHEX_API_KEY")
AGENT_ID = os.environ.get("AETHEX_AGENT_ID")
BASE_URL = os.environ.get("AETHEX_BASE_URL", "https://api.aethexai.com/api/v1").rstrip('/')

if not API_KEY:
    print("AETHEX_API_KEY not set", file=sys.stderr)
    sys.exit(1)
if not AGENT_ID:
    print("AETHEX_AGENT_ID not set", file=sys.stderr)
    sys.exit(1)

url = f"{BASE_URL}/conversation/connect"
headers = {"X-API-Key": API_KEY, "Content-Type": "application/json"}
payload = {"agent_id": AGENT_ID}

data = json.dumps(payload).encode('utf-8')
req = urllib.request.Request(url, data=data, headers=headers, method='POST')

try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        out = json.load(resp)
        print(json.dumps(out, indent=2))
except Exception as e:
    import traceback
    traceback.print_exc()
    print("ERROR:", str(e), file=sys.stderr)
    sys.exit(1)
