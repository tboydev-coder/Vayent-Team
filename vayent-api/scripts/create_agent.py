#!/usr/bin/env python3
"""
Create an Aethex agent using the AETHEX_API_KEY env var.
Run: set AETHEX_API_KEY in the environment and run this script.
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
        # best-effort: ignore errors reading local .env
        pass


# If the key isn't already in the environment, try to load it from the
# repository .env file (useful when running locally without exporting vars).
if not os.environ.get("AETHEX_API_KEY"):
    repo_root = Path(__file__).resolve().parents[1]
    env_path = repo_root / ".env"
    load_env_from_file(env_path)

API_KEY = os.environ.get("AETHEX_API_KEY")
if not API_KEY:
    print("AETHEX_API_KEY environment variable not set (or .env missing)", file=sys.stderr)
    sys.exit(1)

BASE_URL = "https://api.aethexai.com/api/v1"
HEADERS = {"X-API-Key": API_KEY, "Content-Type": "application/json"}

SYSTEM_PROMPT = """You are Vayent AI, an intelligent data copilot for connected business data.

Speak in short, clear sentences. Be warm, confident, and concise.
Introduce yourself as Vayent, not as another brand or company.

You help users explore connected databases, spreadsheets, CSV files,
and analytics sources. When a data source is selected for a live
session, treat that selected source as the active context. Do not use
demo personas, banking scenarios, or placeholder company names.

If the user asks about available data, summarize the selected source
metadata that Vayent provides for the session. If the user asks for
analysis, filtering, comparisons, verification, or drill-down records,
use the session's connected retrieval tool and ground the answer in
the selected source.

Never say you cannot access the selected source when Vayent has already
opened an active session. Never ask the user to upload a file they have
already selected for the session. Never mention old demo personas,
banking examples, or unrelated demo knowledge.
"""


def http_get(path: str):
    url = BASE_URL + path
    req = urllib.request.Request(url, headers=HEADERS, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def http_post(path: str, payload: dict):
    url = BASE_URL + path
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def main():
    try:
        voices = http_get("/voices?language=english")
    except Exception as e:
        print("Failed to fetch voices:", e, file=sys.stderr)
        sys.exit(1)

    voices = [v for v in voices if not v.get("is_cloned")]
    if not voices:
        print("No voices found. Check your API key and try again.", file=sys.stderr)
        sys.exit(1)

    voice_id = voices[0].get("id")
    if not voice_id:
        print("No voice id found in response", file=sys.stderr)
        sys.exit(1)

    payload = {
        "name": "Vayent Voice",
        "system_prompt": SYSTEM_PROMPT,
        "first_message": "Hi, I am Vayent. How can I help you with your selected data source?",
        "voice_id": voice_id,
        "language": "english",
    }

    try:
        agent = http_post("/agents", payload)
    except Exception as e:
        print("Failed to create agent:", e, file=sys.stderr)
        sys.exit(1)

    agent_id = agent.get("id")
    if not agent_id:
        print("Agent creation response did not include an id:", agent, file=sys.stderr)
        sys.exit(1)

    print(f"Agent created: {agent_id}")


if __name__ == "__main__":
    main()
