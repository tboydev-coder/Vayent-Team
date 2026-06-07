#!/usr/bin/env python3
"""
List active connected sources for a user.
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

from app.database import get_db_context, init_db  # noqa: E402
from app.models import DatabaseConnection, SpreadsheetSource  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", required=True)
    args = parser.parse_args()

    await init_db()

    async with get_db_context() as db:
        db_result = await db.execute(
            select(DatabaseConnection)
            .where(
                DatabaseConnection.user_id == args.user_id,
                DatabaseConnection.is_active == True,  # noqa: E712
            )
            .order_by(DatabaseConnection.created_at.desc())
        )
        spreadsheet_result = await db.execute(
            select(SpreadsheetSource)
            .where(
                SpreadsheetSource.user_id == args.user_id,
                SpreadsheetSource.is_active == True,  # noqa: E712
            )
            .order_by(SpreadsheetSource.created_at.desc())
        )

        payload = {
            "databases": [
                {
                    "id": item.id,
                    "name": item.name,
                    "db_type": item.db_type.value,
                    "database_name": item.database_name,
                    "created_at": str(item.created_at),
                }
                for item in db_result.scalars().all()
            ],
            "spreadsheets": [
                {
                    "id": item.id,
                    "name": item.name,
                    "file_type": item.file_type,
                    "source_kind": item.source_kind.value if hasattr(item.source_kind, "value") else str(item.source_kind),
                    "created_at": str(item.created_at),
                }
                for item in spreadsheet_result.scalars().all()
            ],
        }
        print(json.dumps(payload, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
