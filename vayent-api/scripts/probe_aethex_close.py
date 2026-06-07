#!/usr/bin/env python3
"""
Create an Aethex conversation session and probe the undocumented /close endpoint.
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

if not API_KEY or not AGENT_ID:
    print("Missing AETHEX_API_KEY or AETHEX_AGENT_ID", file=sys.stderr)
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
    status, connect_payload = request_json("/conversation/connect", method="POST", payload={"agent_id": AGENT_ID})
    print(f"connect status={status}")
    print(json.dumps(connect_payload, indent=2))
    if status >= 400:
        sys.exit(1)

    sid = (
        connect_payload.get("session_id")
        or connect_payload.get("sessionId")
        or connect_payload.get("id")
    )
    if not isinstance(sid, str) or not sid.strip():
        print("No session id returned", file=sys.stderr)
        sys.exit(1)

    close_status, close_payload = request_json(f"/conversation/{sid}/close", method="POST")
    print(f"close status={close_status}")
    print(json.dumps(close_payload, indent=2))


if __name__ == "__main__":
    main()
