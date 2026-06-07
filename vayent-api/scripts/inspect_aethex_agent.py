#!/usr/bin/env python3
"""
Inspect the configured Aethex agent using the API key and agent id from env or .env.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def load_env_from_file(path: Path) -> None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception:
        pass


if not os.environ.get("AETHEX_API_KEY") or not os.environ.get("AETHEX_AGENT_ID"):
    repo_root = Path(__file__).resolve().parents[1]
    load_env_from_file(repo_root / ".env")


API_KEY = os.environ.get("AETHEX_API_KEY")
AGENT_ID = os.environ.get("AETHEX_AGENT_ID")
BASE_URL = os.environ.get("AETHEX_BASE_URL", "https://api.aethexai.com/api/v1").rstrip("/")

if not API_KEY:
    print("AETHEX_API_KEY not set", file=sys.stderr)
    sys.exit(1)

if not AGENT_ID:
    print("AETHEX_AGENT_ID not set", file=sys.stderr)
    sys.exit(1)


def request_json(path: str, method: str = "GET", payload: dict | None = None) -> tuple[int, object]:
    url = f"{BASE_URL}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"X-API-Key": API_KEY, "Content-Type": "application/json"}
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.getcode(), json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {"raw": body}
        return exc.code, parsed


def main() -> None:
    for path in (f"/agents/{AGENT_ID}", "/agents"):
        status, payload = request_json(path)
        print(f"=== {path} ({status}) ===")
        print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
