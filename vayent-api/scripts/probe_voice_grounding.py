#!/usr/bin/env python3
"""
Probe the selected-source voice grounding path against a real user source.

Usage:
  python scripts/probe_voice_grounding.py --user-id <uuid> [--source-id <uuid>] [--question "..."]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
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


repo_root = Path(__file__).resolve().parents[1]
load_env_from_file(repo_root / ".env")

if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.database import get_db_context, init_db  # noqa: E402
from app.models import DatabaseConnection, SpreadsheetSource, User  # noqa: E402
from app.routers import voice  # noqa: E402
from app.routers.voice import CreateRemoteVoiceSessionRequest, VoiceToolInvocationRequest  # noqa: E402


def _summarize_source(source: DatabaseConnection | SpreadsheetSource) -> dict[str, str]:
    if isinstance(source, DatabaseConnection):
        return {
            "id": source.id,
            "name": source.name,
            "kind": f"database:{source.db_type.value}",
        }

    return {
        "id": source.id,
        "name": source.name,
        "kind": f"spreadsheet:{source.file_type}",
    }


async def _select_source(user_id: str, source_id: str | None) -> DatabaseConnection | SpreadsheetSource | None:
    async with get_db_context() as db:
        if source_id:
            connection = await db.get(DatabaseConnection, source_id)
            if connection and connection.user_id == user_id and connection.is_active:
                return connection
            spreadsheet = await db.get(SpreadsheetSource, source_id)
            if spreadsheet and spreadsheet.user_id == user_id and spreadsheet.is_active:
                return spreadsheet
            return None

        connection_result = await db.execute(
            select(DatabaseConnection)
            .where(
                DatabaseConnection.user_id == user_id,
                DatabaseConnection.is_active == True,  # noqa: E712
            )
            .order_by(DatabaseConnection.created_at.desc())
        )
        connection = connection_result.scalars().first()
        if connection:
            return connection

        spreadsheet_result = await db.execute(
            select(SpreadsheetSource)
            .where(
                SpreadsheetSource.user_id == user_id,
                SpreadsheetSource.is_active == True,  # noqa: E712
            )
            .order_by(SpreadsheetSource.created_at.desc())
        )
        return spreadsheet_result.scalars().first()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--source-id")
    parser.add_argument(
        "--question",
        default="What data do you have access to?",
    )
    args = parser.parse_args()

    try:
        get_settings.cache_clear()
    except Exception:
        pass
    settings = get_settings()
    voice.settings = settings

    await init_db()

    async with get_db_context() as db:
        user = await db.get(User, args.user_id)
        if not user:
            print(f"User not found: {args.user_id}", file=sys.stderr)
            return 1

    source = await _select_source(args.user_id, args.source_id)
    if not source:
        print("No active source found for the given user/source.", file=sys.stderr)
        return 1

    source_summary = _summarize_source(source)
    print("Selected source:")
    print(json.dumps(source_summary, indent=2))

    remote_session_id: str | None = None
    async with get_db_context() as db:
        current_user = await db.get(User, args.user_id)
        if not current_user:
            print(f"User not found while opening session: {args.user_id}", file=sys.stderr)
            return 1

        session_payload = await voice.create_remote_voice_session(
            body=CreateRemoteVoiceSessionRequest(source_id=source_summary["id"]),
            current_user=current_user,
            db=db,
        )
        remote_session_id = (
            session_payload.get("session_id")
            or session_payload.get("sessionId")
            or session_payload.get("id")
        )
        print("Remote voice session created:")
        print(
            json.dumps(
                {
                    "session_id": remote_session_id,
                    "provider_agent_id": session_payload.get("vayent_context", {}).get("provider_agent_id"),
                    "connection_name": session_payload.get("vayent_context", {}).get("connection_name"),
                    "greeting": session_payload.get("vayent_context", {}).get("greeting"),
                },
                indent=2,
            )
        )

        if not remote_session_id:
            print("No remote session id returned.", file=sys.stderr)
            return 1

        tool_response = await voice.query_selected_source_for_live_voice(
            sid=remote_session_id,
            body=VoiceToolInvocationRequest(
                question=args.question,
                call_id="probe-call-1",
                tool_name=voice.VOICE_QUERY_TOOL_NAME,
            ),
            current_user=current_user,
            db=db,
        )
        print("Grounded tool response:")
        print(
            json.dumps(
                {
                    "output_text": tool_response.get("output_text"),
                    "grounding": tool_response.get("grounding"),
                },
                indent=2,
                default=str,
            )
        )

        close_response = await voice.proxy_close_session(
            sid=remote_session_id,
            current_user=current_user,
        )
        print("Close response:")
        print(json.dumps(close_response, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
