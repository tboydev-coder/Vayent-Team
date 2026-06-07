"""Voice conversation endpoints."""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_active_user
from app.config import get_settings
from app.database import get_db_session
from app.models import User
from app.schemas import WorkspaceChatMessageCreate, WorkspaceHistoryMessage
from app.services.db_connection_service import db_connection_service
from app.services.schema_discovery_service import schema_discovery_service
from app.services.spreadsheet_service import spreadsheet_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/voice", tags=["Voice Conversation"])
settings = get_settings()

VOICE_QUERY_TOOL_NAME = "query_selected_source"
VOICE_REMOTE_SESSION_CONTEXTS: dict[str, "VoiceRemoteSessionContext"] = {}
VOICE_LIVE_AGENT_CACHE: dict[str, dict[str, Any]] = {}
VOICE_HISTORY_LIMIT = 10


def _supported_sources() -> list[str]:
    return [
        "postgresql",
        "mysql",
        "excel",
        "csv",
        "spreadsheet",
    ]


def _truncate_list(values: list[str], *, limit: int) -> list[str]:
    cleaned = [value.strip() for value in values if isinstance(value, str) and value.strip()]
    return cleaned[:limit]


def _format_source_greeting(connection_name: str) -> str:
    return f"Hi, I am Vayent. How can I help you with {connection_name}?"


def _format_preview_list(values: list[str], *, empty_fallback: str) -> str:
    preview = _truncate_list(values, limit=6)
    if not preview:
        return empty_fallback
    if len(values) > len(preview):
        preview.append("and more")
    return ", ".join(preview)


def _build_database_source_metadata(
    *,
    connection_name: str,
    schema_kind: str,
    schema_payload: dict[str, Any] | None,
) -> tuple[dict[str, Any], str, int | None]:
    schema_payload = schema_payload or {}
    tables = list(schema_payload.get("tables") or [])
    table_summaries: list[dict[str, Any]] = []

    for table in tables[:12]:
        if not isinstance(table, dict):
            continue
        columns = list(table.get("columns") or [])
        column_names = _truncate_list(
            [
                str(column.get("nickname") or column.get("column_name") or "").strip()
                for column in columns
                if isinstance(column, dict)
            ],
            limit=10,
        )
        table_summaries.append(
            {
                "name": table.get("table_name"),
                "row_count": table.get("row_count"),
                "columns": column_names,
                "description": table.get("table_description"),
            }
        )

    relationships = list(schema_payload.get("relationships") or [])
    metadata = {
        "schema_name": schema_payload.get("schema_name"),
        "schema_description": schema_payload.get("schema_description"),
        "source_type": "database",
        "schema_kind": schema_kind,
        "table_count": len(tables),
        "tables": table_summaries,
        "relationships": relationships[:12],
    }
    table_names = [
        str(table.get("table_name") or "").strip()
        for table in tables
        if isinstance(table, dict)
    ]
    overview = (
        f"I am connected to {connection_name}. "
        f"It contains {len(tables)} tables"
        f"{': ' + _format_preview_list(table_names, empty_fallback='') if table_names else '.'}"
    )
    if not overview.endswith("."):
        overview = f"{overview}."
    return metadata, overview, len(tables)


def _build_spreadsheet_source_metadata(
    *,
    connection_name: str,
    schema_kind: str,
    dataset_payload: dict[str, Any] | None,
) -> tuple[dict[str, Any], str, int, int]:
    dataset_payload = dataset_payload or {}
    tables = list(dataset_payload.get("tables") or [])
    row_count = sum(
        int(table.get("row_count") or 0)
        for table in tables
        if isinstance(table, dict)
    )
    table_summaries: list[dict[str, Any]] = []

    for table in tables[:12]:
        if not isinstance(table, dict):
            continue
        columns = list(table.get("columns") or [])
        column_names = _truncate_list(
            [
                str(column.get("name") or "").strip()
                for column in columns
                if isinstance(column, dict)
            ],
            limit=10,
        )
        table_summaries.append(
            {
                "name": table.get("name"),
                "row_count": table.get("row_count"),
                "columns": column_names,
            }
        )

    metadata = {
        "source_type": "spreadsheet",
        "schema_kind": schema_kind,
        "table_count": len(tables),
        "row_count": row_count,
        "tables": table_summaries,
    }
    sheet_names = [
        str(table.get("name") or "").strip()
        for table in tables
        if isinstance(table, dict)
    ]
    overview = (
        f"I am connected to {connection_name}. "
        f"It contains {len(tables)} sheets"
        f"{': ' + _format_preview_list(sheet_names, empty_fallback='') if sheet_names else ''}"
        f" and about {row_count:,} rows of connected data."
    )
    return metadata, overview, len(tables), row_count


def _build_live_session_instructions(context: VoicePreparedSourceContext) -> str:
    table_or_sheet_items = list(context.source_metadata.get("tables") or [])
    labels = [
        str(item.get("name") or "").strip()
        for item in table_or_sheet_items
        if isinstance(item, dict)
    ]
    scope_summary = _format_preview_list(
        labels,
        empty_fallback="the connected source metadata already loaded in this session",
    )

    return (
        "You are Vayent AI, an intelligent data copilot. "
        f"You are currently connected to the user's selected data source: {context.connection_name}. "
        f"The source type is {context.source_type} and the source kind is {context.schema_kind}. "
        f"Use the selected source as your only business-data context for this live session. "
        f"The connected source currently includes {scope_summary}. "
        "If the user asks what data is available, describe the connected source using the loaded metadata and source overview. "
        "If the user asks for analysis, totals, comparisons, filtering, verification, drill-down records, or any answer that depends on live data, call the query_selected_source tool. "
        "Only answer data questions using this selected source. "
        "Never mention placeholder personas, unrelated companies, or ask the user to upload a file they already selected. "
        "Do not claim you lack access to the source when this session is active."
    )


def _build_live_bootstrap_events(context: VoicePreparedSourceContext) -> list[dict[str, Any]]:
    instructions = _build_live_session_instructions(context)
    source_scope = [
        f"Selected source id: {context.source_id}",
        f"Selected source name: {context.source_name}",
        f"Selected source type: {context.source_type}",
        f"Schema kind: {context.schema_kind}",
    ]
    if context.table_count:
        source_scope.append(f"Table or sheet count: {context.table_count}")
    if context.row_count:
        source_scope.append(f"Row count: {context.row_count}")

    return [
        {
            "type": "session.update",
            "session": {
                "instructions": instructions,
                "metadata": {
                    "source_id": context.source_id,
                    "source_name": context.source_name,
                    "connection_name": context.connection_name,
                    "source_type": context.source_type,
                    "schema_kind": context.schema_kind,
                    "scope": source_scope,
                    "source_overview": context.source_overview,
                    "source_metadata": context.source_metadata,
                },
                "tools": [
                    {
                        "type": "function",
                        "name": VOICE_QUERY_TOOL_NAME,
                        "description": "Query the selected Vayent source and return grounded evidence-backed answers.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "question": {
                                    "type": "string",
                                    "description": "The user's natural language question about the selected source.",
                                }
                            },
                            "required": ["question"],
                        },
                    }
                ],
            },
        }
    ]


def _is_source_awareness_question(question: str) -> bool:
    normalized = question.strip().lower()
    if not normalized:
        return False

    cues = [
        "what data do you have",
        "what data do you have access to",
        "what do you have access to",
        "tell me about this database",
        "tell me about this source",
        "what tables do you have",
        "what sheets do you have",
        "describe this database",
        "describe this source",
        "what is in this database",
        "what is in this source",
    ]
    return any(cue in normalized for cue in cues)


def _metadata_grounding_for_context(session_context: VoiceRemoteSessionContext) -> dict[str, Any]:
    table_items = list(session_context.source_metadata.get("tables") or [])
    matched = [
        str(item.get("name") or "").strip()
        for item in table_items
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]
    columns: list[str] = []
    for item in table_items:
        if not isinstance(item, dict):
            continue
        for column in item.get("columns") or []:
            column_name = str(column).strip()
            if column_name and column_name not in columns:
                columns.append(column_name)

    return {
        "source_id": session_context.source_id,
        "query_intent": "respond",
        "matched_tables_or_sheets": matched[:12],
        "columns_used": columns[:24],
        "executed_operations": [],
        "result_rows": [],
        "final_answer": session_context.source_overview,
    }


def _respond_from_source_metadata(
    *,
    session_context: VoiceRemoteSessionContext,
) -> dict[str, Any]:
    table_items = list(session_context.source_metadata.get("tables") or [])
    table_names = [
        str(item.get("name") or "").strip()
        for item in table_items
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]
    table_preview = _format_preview_list(
        table_names,
        empty_fallback="the connected source metadata already loaded in this session",
    )

    source_label = (
        "database" if session_context.source_type == "database" else "spreadsheet source"
    )
    response_text = (
        f"I am connected to {session_context.connection_name}. "
        f"It is a {source_label} with {table_preview}. "
        f"{session_context.source_overview}"
    )

    return {
        "success": True,
        "tool_name": VOICE_QUERY_TOOL_NAME,
        "output_text": response_text,
        "grounding": {
            **_metadata_grounding_for_context(session_context),
            "final_answer": response_text,
        },
        "result": {
            "success": True,
            "response": response_text,
            "voice_response": response_text,
            "execution_status": "answered",
            "active_source_id": session_context.source_id,
            "targeted_source_ids": [session_context.source_id],
            "targeted_connection_ids": [session_context.source_id],
            "generated_queries": [],
            "query_results": [],
            "warnings": [],
            "workspace_message": None,
            "grounding": {
                **_metadata_grounding_for_context(session_context),
                "final_answer": response_text,
            },
        },
    }


def _serialize_live_history(
    history: list["VoiceHistoryItem"],
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for item in history[-VOICE_HISTORY_LIMIT:]:
        if item.role in {"user", "assistant"} and item.content.strip():
            messages.append({"role": item.role, "content": item.content.strip()})
    return messages


def _append_live_history(
    session_context: VoiceRemoteSessionContext,
    *,
    question: str,
    answer: str,
) -> None:
    if question.strip():
        session_context.history.append(VoiceHistoryItem(role="user", content=question.strip()))
    if answer.strip():
        session_context.history.append(VoiceHistoryItem(role="assistant", content=answer.strip()))
    if len(session_context.history) > VOICE_HISTORY_LIMIT * 2:
        session_context.history = session_context.history[-VOICE_HISTORY_LIMIT * 2 :]


def _build_live_query_timeout_response(
    *,
    session_context: VoiceRemoteSessionContext,
    question: str,
) -> dict[str, Any]:
    response_text = (
        f"I am still connected to {session_context.connection_name}, but that live lookup is taking too long. "
        "Ask for a smaller table, a shorter time range, or a more specific metric and I will try again."
    )
    _append_live_history(
        session_context,
        question=question,
        answer=response_text,
    )
    return {
        "success": False,
        "tool_name": VOICE_QUERY_TOOL_NAME,
        "output_text": response_text,
        "grounding": {
            **_metadata_grounding_for_context(session_context),
            "query_intent": "clarify",
            "final_answer": response_text,
        },
        "result": {
            "success": False,
            "response": response_text,
            "voice_response": response_text,
            "execution_status": "clarify",
            "active_source_id": session_context.source_id,
            "targeted_source_ids": [session_context.source_id],
            "targeted_connection_ids": [session_context.source_id],
            "generated_queries": [],
            "query_results": [],
            "warnings": ["live_query_timeout"],
            "workspace_message": None,
            "grounding": {
                **_metadata_grounding_for_context(session_context),
                "query_intent": "clarify",
                "final_answer": response_text,
            },
        },
    }


def _build_runtime_agent_name(context: VoicePreparedSourceContext) -> str:
    return f"Vayent Voice - {context.connection_name}"[:120]


def _build_runtime_agent_prompt(context: VoicePreparedSourceContext) -> str:
    source_tables = list(context.source_metadata.get("tables") or [])
    table_lines: list[str] = []
    for item in source_tables[:10]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        columns = item.get("columns") or []
        column_text = ", ".join(str(column).strip() for column in columns[:10] if str(column).strip())
        row_count = item.get("row_count")
        details = []
        if row_count not in (None, ""):
            details.append(f"rows={row_count}")
        if column_text:
            details.append(f"columns={column_text}")
        suffix = f" ({'; '.join(details)})" if details else ""
        table_lines.append(f"- {name}{suffix}")

    schema_overview = "\n".join(table_lines) if table_lines else "- Source metadata is attached in session metadata."

    return (
        "You are Vayent AI, an AI-powered data intelligence copilot that helps users interact with their connected data sources.\n\n"
        f"You are currently connected to this selected source: {context.connection_name}.\n"
        f"Source id: {context.source_id}\n"
        f"Source type: {context.source_type}\n"
        f"Source kind: {context.schema_kind}\n"
        f"Source overview: {context.source_overview}\n\n"
        "Known tables or sheets:\n"
        f"{schema_overview}\n\n"
        "Behavior rules:\n"
        f"- Introduce yourself as Vayent, not as any other company or persona.\n"
        f"- The opening greeting for this session is exactly: \"{context.greeting}\"\n"
        "- Answer source-awareness questions using the selected source metadata.\n"
        "- For analysis, filtering, verification, comparisons, drill-down records, or other data-dependent answers, use the live query_selected_source tool and ground the answer in the selected Vayent source.\n"
        "- Never mention demo assistants or unrelated company names.\n"
        "- Never tell the user to upload a file or say you do not have access to the selected source while this session is active.\n"
    )


def _live_agent_cache_key(
    *,
    template_agent_id: str,
    context: VoicePreparedSourceContext,
) -> str:
    raw = "|".join(
        [
            template_agent_id,
            context.source_id,
            context.connection_name,
            context.source_type,
            context.schema_kind,
            context.source_overview,
            repr(context.source_metadata),
        ]
    )
    return sha256(raw.encode("utf-8")).hexdigest()


async def _aethex_request(
    *,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = f"{settings.aethex_base_url.rstrip('/')}{path}"
    headers = {"X-API-Key": settings.aethex_api_key, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=settings.aethex_timeout_seconds) as client:
        response = await client.request(method, url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()


async def _ensure_runtime_live_agent(
    *,
    template_agent_id: str,
    context: VoicePreparedSourceContext,
) -> dict[str, Any]:
    cache_key = _live_agent_cache_key(template_agent_id=template_agent_id, context=context)
    cached_agent = VOICE_LIVE_AGENT_CACHE.get(cache_key)
    if cached_agent:
        return cached_agent

    template_agent = await _aethex_request(method="GET", path=f"/agents/{template_agent_id}")
    create_payload = {
        "name": _build_runtime_agent_name(context),
        "system_prompt": _build_runtime_agent_prompt(context),
        "first_message": context.greeting,
        "voice_id": template_agent.get("voice_id"),
        "language": template_agent.get("language") or "english",
        "dialect_style": template_agent.get("dialect_style") or "formal",
        "recording_enabled": bool(template_agent.get("recording_enabled", True)),
        "transcription_enabled": bool(template_agent.get("transcription_enabled", True)),
        "interruption_enabled": bool(template_agent.get("interruption_enabled", True)),
        "script_adherence": template_agent.get("script_adherence") or "strict",
        "metadata": {
            "managed_by": "vayent",
            "template_agent_id": template_agent_id,
            "source_id": context.source_id,
            "connection_name": context.connection_name,
            "source_type": context.source_type,
            "schema_kind": context.schema_kind,
        },
    }
    create_payload = {
        key: value for key, value in create_payload.items() if value is not None
    }
    live_agent = await _aethex_request(method="POST", path="/agents", payload=create_payload)
    VOICE_LIVE_AGENT_CACHE[cache_key] = live_agent
    return live_agent


@dataclass
class VoicePreparedSourceContext:
    session_id: str
    source_id: str
    source_type: Literal["database", "spreadsheet"]
    source_name: str
    connection_name: str
    schema_kind: str
    greeting: str
    source_overview: str
    source_metadata: dict[str, Any]
    schema_context: str | None = None
    row_count: int | None = None
    table_count: int | None = None


@dataclass
class VoiceRemoteSessionContext:
    remote_session_id: str
    local_session_id: str
    live_agent_id: str
    source_id: str
    source_type: Literal["database", "spreadsheet"]
    source_name: str
    connection_name: str
    user_id: str
    source_metadata: dict[str, Any]
    source_overview: str
    created_at: datetime
    schema_kind: str | None = None
    schema_context: str | None = None
    history: list["VoiceHistoryItem"] = field(default_factory=list)


class VoiceHistoryItem(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class VoiceMessageRequest(BaseModel):
    text: str
    source_ids: list[str] | None = None
    active_source_id: str | None = None
    connection_id: str | None = None
    history: list[VoiceHistoryItem] = Field(default_factory=list)
    session_id: str | None = None


class CreateRemoteVoiceSessionRequest(BaseModel):
    source_id: str = Field(..., min_length=1)
    local_session_id: str | None = None
    agent_id: str | None = None


class VoiceToolInvocationRequest(BaseModel):
    question: str | None = None
    text: str | None = None
    user_question: str | None = None
    call_id: str | None = None
    tool_name: str | None = None
    arguments: dict[str, Any] | str | None = None
    event: dict[str, Any] | None = None


async def _prepare_source_context(
    *,
    source_id: str | None,
    connection_id: str | None,
    current_user: User,
    db: AsyncSession,
) -> VoicePreparedSourceContext:
    selected_source_id = (source_id or connection_id or "").strip()
    if not selected_source_id:
        raise HTTPException(status_code=400, detail="Source ID not provided")

    connection = await db_connection_service.get_connection(selected_source_id, db)
    if connection and connection.user_id == current_user.id:
        schema = await schema_discovery_service.get_connection_schema(connection.id, db)
        schema_payload = await schema_discovery_service.get_connection_schema_response(
            connection.id,
            db,
        )
        schema_context = (
            await schema_discovery_service.get_schema_for_rag(schema.id, db)
            if schema
            else "No synced schema is currently available for this connection."
        )
        source_metadata, source_overview, table_count = _build_database_source_metadata(
            connection_name=connection.name,
            schema_kind=connection.db_type.value,
            schema_payload=schema_payload,
        )
        return VoicePreparedSourceContext(
            session_id=f"voice-{connection.id}",
            source_id=connection.id,
            source_type="database",
            source_name=connection.name,
            connection_name=connection.name,
            schema_kind=connection.db_type.value,
            greeting=_format_source_greeting(connection.name),
            source_overview=source_overview,
            source_metadata=source_metadata,
            schema_context=schema_context,
            row_count=None,
            table_count=table_count,
        )

    source = await spreadsheet_service.get_source(selected_source_id, db)
    if source and source.user_id == current_user.id and source.is_active:
        source_metadata, source_overview, table_count, row_count = _build_spreadsheet_source_metadata(
            connection_name=source.name,
            schema_kind=source.file_type,
            dataset_payload=source.dataset_payload,
        )
        return VoicePreparedSourceContext(
            session_id=f"voice-{source.id}",
            source_id=source.id,
            source_type="spreadsheet",
            source_name=source.name,
            connection_name=source.name,
            schema_kind=source.file_type,
            greeting=_format_source_greeting(source.name),
            source_overview=source_overview,
            source_metadata=source_metadata,
            schema_context=spreadsheet_service.format_source_for_ai(source),
            row_count=row_count,
            table_count=table_count,
        )

    raise HTTPException(status_code=404, detail="Source not found")


def _extract_sql_tables(sql: str | None) -> list[str]:
    if not sql:
        return []
    matches = re.findall(r"\b(?:from|join)\s+([a-zA-Z0-9_\.\"`]+)", sql, flags=re.IGNORECASE)
    cleaned = []
    for match in matches:
        table_name = match.strip().strip('"').strip("`")
        if table_name not in cleaned:
            cleaned.append(table_name)
    return cleaned


def _build_grounding_payload(
    *,
    payload: dict[str, Any],
    source_id: str,
) -> dict[str, Any]:
    query_results = list(payload.get("query_results") or [])
    generated_queries = list(payload.get("generated_queries") or [])

    matched_tables_or_sheets: list[str] = []
    columns_used: list[str] = []
    structured_operations: list[dict[str, Any]] = []

    for result in query_results:
        rows = list(result.get("rows") or [])
        for row in rows[:12]:
            if isinstance(row, dict):
                sheet_name = row.get("sheet")
                if isinstance(sheet_name, str) and sheet_name and sheet_name not in matched_tables_or_sheets:
                    matched_tables_or_sheets.append(sheet_name)
                for key in row.keys():
                    if key not in columns_used:
                        columns_used.append(str(key))

    for generated in generated_queries:
        sql_text = generated.get("sql")
        for table_name in _extract_sql_tables(sql_text if isinstance(sql_text, str) else None):
            if table_name not in matched_tables_or_sheets:
                matched_tables_or_sheets.append(table_name)
        structured_operations.append(
            {
                "source_id": generated.get("source_id") or generated.get("connection_id"),
                "source_type": generated.get("source_type"),
                "connection_name": generated.get("connection_name"),
                "query": generated.get("sql"),
                "row_count": generated.get("row_count"),
                "status": generated.get("status"),
                "error": generated.get("error"),
            }
        )

    return {
        "source_id": source_id,
        "query_intent": "query" if generated_queries else "respond",
        "matched_tables_or_sheets": matched_tables_or_sheets,
        "columns_used": columns_used[:24],
        "executed_operations": structured_operations,
        "result_rows": query_results,
        "final_answer": payload.get("ai_explanation"),
    }


async def _run_source_grounded_voice_query(
    *,
    text: str,
    source_ids: list[str],
    active_source_id: str,
    history: list[VoiceHistoryItem],
    current_user: User,
    db: AsyncSession,
) -> dict[str, Any]:
    from app.routers.chat import send_workspace_message

    workspace_request = WorkspaceChatMessageCreate(
        user_prompt=text,
        source_ids=source_ids,
        active_source_id=active_source_id,
        connection_ids=source_ids,
        active_connection_id=active_source_id,
        history=[
            WorkspaceHistoryMessage(role=item.role, content=item.content)
            for item in history
        ],
    )
    workspace_response = await send_workspace_message(
        data=workspace_request,
        current_user=current_user,
        db=db,
    )
    payload = workspace_response.model_dump(mode="json")
    return {
        "success": workspace_response.execution_status != "error",
        "response": workspace_response.ai_explanation,
        "voice_response": workspace_response.ai_explanation,
        "execution_status": workspace_response.execution_status,
        "active_source_id": workspace_response.active_source_id,
        "targeted_source_ids": workspace_response.targeted_source_ids,
        "targeted_connection_ids": workspace_response.targeted_connection_ids,
        "generated_queries": payload.get("generated_queries", []),
        "query_results": payload.get("query_results", []),
        "warnings": payload.get("warnings", []),
        "workspace_message": payload,
        "grounding": _build_grounding_payload(
            payload=payload,
            source_id=workspace_response.active_source_id or active_source_id,
        ),
    }


async def _run_live_database_voice_query(
    *,
    text: str,
    session_context: VoiceRemoteSessionContext,
    current_user: User,
    db: AsyncSession,
) -> dict[str, Any]:
    from app.ai.sql_generation import sql_generation_service
    from app.routers import chat as chat_router
    from app.services.error_message_service import error_message_service
    from app.services.query_execution_service import query_execution_service

    connection = await db_connection_service.get_connection(session_context.source_id, db)
    if not connection or connection.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Selected database source not found")

    schema_context = (
        session_context.schema_context
        or "No synced schema is currently available for this connection."
    )
    conversation_history = _serialize_live_history(session_context.history)

    async def _execute() -> dict[str, Any]:
        plan = await sql_generation_service.plan_response(
            user_query=text,
            schema_context=schema_context,
            database_type=connection.db_type.value,
            conversation_history=conversation_history,
        )
        warnings = list(plan.get("warnings") or [])
        action = plan.get("action", "clarify")
        planned_response = (plan.get("response") or "").strip()

        if plan.get("error") and not planned_response:
            response_text = error_message_service.ai_failed_message(plan["error"])
            _append_live_history(session_context, question=text, answer=response_text)
            return {
                "success": False,
                "response": response_text,
                "voice_response": response_text,
                "execution_status": "error",
                "active_source_id": session_context.source_id,
                "targeted_source_ids": [session_context.source_id],
                "targeted_connection_ids": [session_context.source_id],
                "generated_queries": [],
                "query_results": [],
                "warnings": warnings,
                "workspace_message": None,
                "grounding": {
                    **_metadata_grounding_for_context(session_context),
                    "query_intent": "respond",
                    "final_answer": response_text,
                },
            }

        if action in {"respond", "clarify", "refuse"}:
            response_text = (
                planned_response
                or "I need a bit more detail about the metric, table, or time range to answer that from the selected database."
            )
            _append_live_history(session_context, question=text, answer=response_text)
            return {
                "success": action != "refuse",
                "response": response_text,
                "voice_response": response_text,
                "execution_status": "answered" if action == "respond" else action,
                "active_source_id": session_context.source_id,
                "targeted_source_ids": [session_context.source_id],
                "targeted_connection_ids": [session_context.source_id],
                "generated_queries": [],
                "query_results": [],
                "warnings": warnings,
                "workspace_message": None,
                "grounding": {
                    **_metadata_grounding_for_context(session_context),
                    "query_intent": action,
                    "final_answer": response_text,
                },
            }

        generated_sql = (plan.get("sql") or "").strip()
        if not generated_sql:
            response_text = "I couldn't form a safe database query from that request."
            _append_live_history(session_context, question=text, answer=response_text)
            return {
                "success": False,
                "response": response_text,
                "voice_response": response_text,
                "execution_status": "error",
                "active_source_id": session_context.source_id,
                "targeted_source_ids": [session_context.source_id],
                "targeted_connection_ids": [session_context.source_id],
                "generated_queries": [],
                "query_results": [],
                "warnings": warnings,
                "workspace_message": None,
                "grounding": {
                    **_metadata_grounding_for_context(session_context),
                    "query_intent": "query",
                    "final_answer": response_text,
                },
            }

        safety_check = await query_execution_service.validate_and_prepare(generated_sql)
        if not safety_check["is_safe"] or safety_check["is_destructive"]:
            response_text = (
                "I couldn't safely run that request against the selected database. "
                f"{safety_check.get('error') or 'Please rephrase the question.'}"
            )
            _append_live_history(session_context, question=text, answer=response_text)
            return {
                "success": False,
                "response": response_text,
                "voice_response": response_text,
                "execution_status": "error",
                "active_source_id": session_context.source_id,
                "targeted_source_ids": [session_context.source_id],
                "targeted_connection_ids": [session_context.source_id],
                "generated_queries": [
                    {
                        "source_id": connection.id,
                        "source_type": "database",
                        "connection_id": connection.id,
                        "connection_name": connection.name,
                        "database_name": connection.database_name,
                        "sql": generated_sql,
                        "status": "blocked",
                        "row_count": 0,
                        "error": safety_check.get("error"),
                    }
                ],
                "query_results": [],
                "warnings": warnings,
                "workspace_message": None,
                "grounding": {
                    "source_id": session_context.source_id,
                    "query_intent": "query",
                    "matched_tables_or_sheets": _extract_sql_tables(generated_sql),
                    "columns_used": [],
                    "executed_operations": [],
                    "result_rows": [],
                    "final_answer": response_text,
                },
            }

        username, password = db_connection_service.decrypt_credentials(connection)
        execution_result = await query_execution_service.execute_query(
            connection=connection,
            query=generated_sql,
            username_decrypted=username,
            password_decrypted=password,
            user_id=current_user.id,
            db=db,
            safety_check=safety_check,
        )

        generated_query = {
            "source_id": connection.id,
            "source_type": "database",
            "connection_id": connection.id,
            "connection_name": connection.name,
            "database_name": connection.database_name,
            "sql": generated_sql,
            "status": "executed" if execution_result.get("success") else "error",
            "row_count": execution_result.get("row_count") or 0,
            "error": execution_result.get("error"),
        }

        if execution_result.get("success"):
            rows = chat_router._sanitize_result_payload(execution_result.get("result", []) or [])
            row_count = execution_result.get("row_count", 0) or 0
            truncated = bool(execution_result.get("truncated"))
            response_text = chat_router._fallback_query_response(
                rows,
                row_count,
                truncated,
                user_prompt=text,
            )
            query_run = {
                "source_id": connection.id,
                "source_type": "database",
                "connection_id": connection.id,
                "connection_name": connection.name,
                "database_name": connection.database_name,
                "sql": generated_sql,
                "row_count": row_count,
                "truncated": truncated,
                "rows": rows,
                "error": None,
            }
            payload = {
                "ai_explanation": response_text,
                "generated_queries": [generated_query],
                "query_results": [query_run],
                "warnings": warnings,
            }
            _append_live_history(session_context, question=text, answer=response_text)
            return {
                "success": True,
                "response": response_text,
                "voice_response": response_text,
                "execution_status": "executed",
                "active_source_id": session_context.source_id,
                "targeted_source_ids": [session_context.source_id],
                "targeted_connection_ids": [session_context.source_id],
                "generated_queries": [generated_query],
                "query_results": [query_run],
                "warnings": warnings,
                "workspace_message": payload,
                "grounding": _build_grounding_payload(
                    payload=payload,
                    source_id=session_context.source_id,
                ),
            }

        response_text = execution_result.get("error") or "I couldn't complete that database lookup."
        payload = {
            "ai_explanation": response_text,
            "generated_queries": [generated_query],
            "query_results": [
                {
                    "source_id": connection.id,
                    "source_type": "database",
                    "connection_id": connection.id,
                    "connection_name": connection.name,
                    "database_name": connection.database_name,
                    "sql": generated_sql,
                    "row_count": 0,
                    "truncated": False,
                    "rows": [],
                    "error": response_text,
                }
            ],
            "warnings": warnings,
        }
        _append_live_history(session_context, question=text, answer=response_text)
        return {
            "success": False,
            "response": response_text,
            "voice_response": response_text,
            "execution_status": "error",
            "active_source_id": session_context.source_id,
            "targeted_source_ids": [session_context.source_id],
            "targeted_connection_ids": [session_context.source_id],
            "generated_queries": payload["generated_queries"],
            "query_results": payload["query_results"],
            "warnings": warnings,
            "workspace_message": payload,
            "grounding": _build_grounding_payload(
                payload=payload,
                source_id=session_context.source_id,
            ),
        }

    try:
        return await asyncio.wait_for(
            _execute(),
            timeout=max(5, int(getattr(settings, "voice_query_timeout_seconds", 18) or 18)),
        )
    except asyncio.TimeoutError:
        logger.warning(
            "Live voice database query timed out for source %s",
            session_context.source_id,
        )
        return _build_live_query_timeout_response(
            session_context=session_context,
            question=text,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Live voice database query failed for source %s: %s",
            session_context.source_id,
            exc,
            exc_info=True,
        )
        response_text = (
            f"I hit a problem while checking {session_context.connection_name}. "
            "Please try the question again with a narrower scope."
        )
        _append_live_history(session_context, question=text, answer=response_text)
        return {
            "success": False,
            "response": response_text,
            "voice_response": response_text,
            "execution_status": "error",
            "active_source_id": session_context.source_id,
            "targeted_source_ids": [session_context.source_id],
            "targeted_connection_ids": [session_context.source_id],
            "generated_queries": [],
            "query_results": [],
            "warnings": [str(exc)],
            "workspace_message": None,
            "grounding": {
                **_metadata_grounding_for_context(session_context),
                "query_intent": "respond",
                "final_answer": response_text,
            },
        }


def _tool_instruction_payload(context: VoicePreparedSourceContext) -> dict[str, Any]:
    return _build_live_bootstrap_events(context)[0]


def _extract_tool_question(body: VoiceToolInvocationRequest) -> str:
    for candidate in (body.question, body.text, body.user_question):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()

    arguments = body.arguments
    if isinstance(arguments, dict):
        for key in ("question", "text", "user_question", "query", "prompt"):
            value = arguments.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    if isinstance(arguments, str) and arguments.strip():
        return arguments.strip()

    event = body.event or {}
    if isinstance(event, dict):
        nested_arguments = event.get("arguments")
        if isinstance(nested_arguments, dict):
            for key in ("question", "text", "user_question", "query", "prompt"):
                value = nested_arguments.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

    raise HTTPException(status_code=400, detail="Tool question is missing")


@router.get("/capabilities")
async def voice_capabilities(current_user: User = Depends(get_current_active_user)):
    """Return runtime capabilities for the frontend voice experience."""
    return {
        "supports_voice": True,
        "supported_sources": _supported_sources(),
        "aethex_configured": settings.aethex_configured,
        "conversation_mode": "source_first",
        "transport_modes": ["aethex_webrtc"] if settings.aethex_configured else [],
        "live_tool_name": VOICE_QUERY_TOOL_NAME,
    }


@router.post("/start-session")
async def start_voice_session(
    source_id: str | None = None,
    connection_id: str | None = None,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Prepare a voice session for the selected source."""
    context = await _prepare_source_context(
        source_id=source_id,
        connection_id=connection_id,
        current_user=current_user,
        db=db,
    )
    return {
        "session_id": context.session_id,
        "source_id": context.source_id,
        "source_type": context.source_type,
        "connection_id": context.source_id,
        "connection_name": context.connection_name,
        "source_name": context.source_name,
        "greeting": context.greeting,
        "source_overview": context.source_overview,
        "source_metadata": context.source_metadata,
        "schema_loaded": True,
        "schema_kind": context.schema_kind,
        "table_count": context.table_count,
        "row_count": context.row_count,
        "live_tool_name": VOICE_QUERY_TOOL_NAME,
    }


@router.post("/session")
async def create_remote_voice_session(
    body: CreateRemoteVoiceSessionRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Create a remote agent session with Aethex and attach source context."""
    if not settings.aethex_configured:
        raise HTTPException(status_code=503, detail="Voice service not configured on server")

    context = await _prepare_source_context(
        source_id=body.source_id,
        connection_id=None,
        current_user=current_user,
        db=db,
    )
    chosen_agent = (body.agent_id or settings.aethex_agent_id or "").strip()
    if not chosen_agent:
        raise HTTPException(status_code=400, detail="Agent ID not provided")

    try:
        live_agent = await _ensure_runtime_live_agent(
            template_agent_id=chosen_agent,
            context=context,
        )
        payload = await _aethex_request(
            method="POST",
            path="/conversation/connect",
            payload={"agent_id": live_agent["id"]},
        )
    except httpx.HTTPError as exc:
        logger.error("Aethex create session error: %s", exc)
        raise HTTPException(status_code=502, detail="Failed to create voice session") from exc
    except KeyError as exc:
        logger.error("Aethex live agent response missing required fields: %s", exc)
        raise HTTPException(status_code=502, detail="Voice agent setup failed") from exc

    remote_session_id = (
        payload.get("session_id") or payload.get("sessionId") or payload.get("id")
    )
    if isinstance(remote_session_id, str) and remote_session_id.strip():
        VOICE_REMOTE_SESSION_CONTEXTS[remote_session_id] = VoiceRemoteSessionContext(
            remote_session_id=remote_session_id,
            local_session_id=body.local_session_id or context.session_id,
            live_agent_id=str(live_agent["id"]),
            source_id=context.source_id,
            source_type=context.source_type,
            source_name=context.source_name,
            connection_name=context.connection_name,
            user_id=current_user.id,
            source_metadata=context.source_metadata,
            source_overview=context.source_overview,
            schema_kind=context.schema_kind,
            schema_context=context.schema_context,
            created_at=datetime.utcnow(),
            history=[VoiceHistoryItem(role="assistant", content=context.greeting)],
        )

    payload["vayent_context"] = {
        "source_id": context.source_id,
        "source_name": context.source_name,
        "connection_name": context.connection_name,
        "source_type": context.source_type,
        "schema_kind": context.schema_kind,
        "local_session_id": body.local_session_id or context.session_id,
        "greeting": context.greeting,
        "source_overview": context.source_overview,
        "source_metadata": context.source_metadata,
        "live_tool_name": VOICE_QUERY_TOOL_NAME,
        "provider_agent_id": live_agent.get("id"),
        "bootstrap_events": _build_live_bootstrap_events(context),
        "tool_instruction": _tool_instruction_payload(context),
    }
    return payload


@router.post("/session/{sid}/offer")
async def proxy_offer_to_remote(
    sid: str,
    body: dict = Body(...),
    current_user: User = Depends(get_current_active_user),
):
    """Proxy an SDP offer to the Aethex conversation endpoint and return the answer."""
    if not settings.aethex_configured:
        raise HTTPException(status_code=503, detail="Voice service not configured on server")

    url = f"{settings.aethex_base_url.rstrip('/')}/conversation/{sid}/offer"
    headers = {"X-API-Key": settings.aethex_api_key, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=settings.aethex_timeout_seconds) as client:
        try:
            resp = await client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            logger.error("Aethex offer proxy error for %s: %s", sid, exc)
            raise HTTPException(status_code=502, detail="Failed to proxy SDP offer") from exc


@router.post("/session/{sid}/candidate")
async def proxy_candidate_to_remote(
    sid: str,
    body: dict = Body(...),
    current_user: User = Depends(get_current_active_user),
):
    """Proxy an ICE candidate when supported by the remote voice provider."""
    if not settings.aethex_configured:
        raise HTTPException(status_code=503, detail="Voice service not configured on server")

    url = f"{settings.aethex_base_url.rstrip('/')}/conversation/{sid}/candidate"
    headers = {"X-API-Key": settings.aethex_api_key, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=settings.aethex_timeout_seconds) as client:
        try:
            resp = await client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                logger.warning(
                    "Aethex candidate endpoint unavailable for %s; continuing without trickle ICE",
                    sid,
                )
                return {
                    "accepted": False,
                    "ignored": True,
                    "reason": "candidate_endpoint_unavailable",
                }
            logger.error("Aethex candidate proxy error for %s: %s", sid, exc)
            raise HTTPException(status_code=502, detail="Failed to proxy ICE candidate") from exc
        except httpx.HTTPError as exc:
            logger.error("Aethex candidate proxy error for %s: %s", sid, exc)
            raise HTTPException(status_code=502, detail="Failed to proxy ICE candidate") from exc


@router.post("/session/{sid}/close")
async def proxy_close_session(
    sid: str,
    current_user: User = Depends(get_current_active_user),
):
    """End the local live session and best-effort notify Aethex when supported."""
    if not settings.aethex_configured:
        raise HTTPException(status_code=503, detail="Voice service not configured on server")

    url = f"{settings.aethex_base_url.rstrip('/')}/conversation/{sid}/close"
    headers = {"X-API-Key": settings.aethex_api_key, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=settings.aethex_timeout_seconds) as client:
        try:
            resp = await client.post(url, headers=headers)
            resp.raise_for_status()
            payload = resp.json()
            return {
                "closed": True,
                "provider_notified": True,
                "provider_response": payload,
            }
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                logger.warning(
                    "Aethex close endpoint unavailable for %s; treating session close as local-only",
                    sid,
                )
                return {
                    "closed": True,
                    "provider_notified": False,
                    "reason": "close_endpoint_unavailable",
                }
            logger.warning("Aethex close proxy error for %s: %s", sid, exc)
            return {
                "closed": True,
                "provider_notified": False,
                "reason": "provider_close_failed",
            }
        except httpx.HTTPError as exc:
            logger.warning("Aethex close proxy error for %s: %s", sid, exc)
            return {
                "closed": True,
                "provider_notified": False,
                "reason": "provider_close_failed",
            }
        finally:
            VOICE_REMOTE_SESSION_CONTEXTS.pop(sid, None)


@router.post("/session/{sid}/tool/query-selected-source")
async def query_selected_source_for_live_voice(
    sid: str,
    body: VoiceToolInvocationRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Bridge a live Aethex session to Vayent's grounded retrieval pipeline."""
    session_context = VOICE_REMOTE_SESSION_CONTEXTS.get(sid)
    if not session_context or session_context.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Live voice session not found")

    question = _extract_tool_question(body)
    if _is_source_awareness_question(question):
        metadata_response = _respond_from_source_metadata(session_context=session_context)
        _append_live_history(
            session_context,
            question=question,
            answer=metadata_response["output_text"],
        )
        metadata_response["call_id"] = body.call_id
        return metadata_response

    if session_context.source_type == "database":
        result = await _run_live_database_voice_query(
            text=question,
            session_context=session_context,
            current_user=current_user,
            db=db,
        )
    else:
        result = await _run_source_grounded_voice_query(
            text=question,
            source_ids=[session_context.source_id],
            active_source_id=session_context.source_id,
            history=list(session_context.history),
            current_user=current_user,
            db=db,
        )
        _append_live_history(
            session_context,
            question=question,
            answer=result["voice_response"],
        )

    return {
        "success": result["success"],
        "tool_name": VOICE_QUERY_TOOL_NAME,
        "call_id": body.call_id,
        "output_text": result["voice_response"],
        "grounding": result["grounding"],
        "result": result,
    }


@router.post("/message")
async def handle_voice_message(
    body: VoiceMessageRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Handle a grounded voice turn using the workspace retrieval pipeline."""
    selected_source_ids = [
        source_id.strip()
        for source_id in (body.source_ids or [])
        if source_id and source_id.strip()
    ]
    if not selected_source_ids and body.connection_id:
        selected_source_ids = [body.connection_id.strip()]

    if not selected_source_ids:
        raise HTTPException(status_code=400, detail="Select at least one source.")

    active_source_id = (body.active_source_id or body.connection_id or "").strip()
    if not active_source_id:
        active_source_id = selected_source_ids[0]

    return await _run_source_grounded_voice_query(
        text=body.text,
        source_ids=selected_source_ids,
        active_source_id=active_source_id,
        history=body.history,
        current_user=current_user,
        db=db,
    )
