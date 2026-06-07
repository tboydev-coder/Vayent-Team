"""Chat and query API routes."""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime
from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.ai.sql_generation import sql_generation_service
from app.auth.dependencies import get_current_active_user
from app.database import get_db_session
from app.models import ChatMessage, ChatSession, DatabaseConnection, User, SpreadsheetSourceKind
from app.schemas import (
    ChatMessageCreate,
    ChatMessageResponse,
    ChatSessionCreate,
    ChatSessionResponse,
    ChatSessionSummaryResponse,
    ExecuteQueryRequest,
    ExecuteQueryResponse,
    QueryConfirmationRequest,
    QueryLogResponse,
    QueryLogPageResponse,
    QueryStatsResponse,
    QueryValidationResponse,
    WorkspaceChatMessageCreate,
    WorkspaceChatMessageResponse,
)
from app.services.db_connection_service import db_connection_service
from app.services.activity_service import activity_service
from app.services.error_message_service import error_message_service
from app.services.query_execution_service import query_execution_service
from app.services.schema_discovery_service import schema_discovery_service
from app.services.spreadsheet_service import spreadsheet_service
from app.services.token_usage_service import (
    TokenLimitExceededError,
    token_usage_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["Chat & Queries"])

CHAT_HISTORY_MESSAGE_LIMIT = 6
CHAT_PLANNING_COMPLETION_ALLOWANCE = 240
WORKSPACE_HISTORY_MESSAGE_LIMIT = 10
WORKSPACE_PLANNING_COMPLETION_ALLOWANCE = 360
SENSITIVE_RESULT_FIELD_KEYWORDS = (
    "password",
    "hash",
    "token",
    "secret",
    "salt",
    "otp",
    "pin",
    "biometric",
)

BUSINESS_ADVICE_KEYWORDS = (
    "retention",
    "churn",
    "segment",
    "cohort",
    "activation",
    "conversion",
    "engagement",
    "renewal",
    "upgrade",
    "downgrade",
    "pricing",
    "revenue",
    "ltv",
    "arpu",
    "arr",
    "mrr",
)


def _looks_like_business_advice(user_prompt: str) -> bool:
    lowered = (user_prompt or "").strip().lower()
    return any(keyword in lowered for keyword in BUSINESS_ADVICE_KEYWORDS)


def _offline_business_advice_fallback(user_prompt: str) -> str:
    """Provide a useful, non-data answer when the AI service is unavailable."""
    if "retention" in (user_prompt or "").lower():
        return (
            "Premium retention usually gets pressured by one of a few patterns: weak activation (customers never reach the “aha”), "
            "value decay after the first use-case, poor renewal timing/communications, or a product-fit mismatch in a specific segment. "
            "Start by slicing churn by (1) tenure/cohort, (2) plan and price point, (3) acquisition channel, (4) geography, and (5) "
            "usage intensity in the 7–14 days before churn. Fix first the segment with the highest revenue at risk (high share of premium "
            "revenue × high churn lift) and a clear leading indicator you can move quickly (activation, key feature adoption, or renewal experience)."
        )

    return (
        "I can still help at a high level: define the decision (what outcome matters), identify 2–3 plausible drivers, "
        "segment the problem (who/when/where), pick the highest-impact segment (size × severity × controllability), and then "
        "run one focused experiment to validate the driver."
    )


def _extract_row_count_for_logs(query_result: Any) -> int | None:
    if not isinstance(query_result, dict):
        return None

    row_count = query_result.get("row_count")
    if isinstance(row_count, int):
        return row_count

    rows = query_result.get("rows")
    if isinstance(rows, list):
        return len(rows)

    result = query_result.get("result")
    if isinstance(result, list):
        return len(result)

    return None


def _log_chat_event(
    *,
    action: str,
    status: str,
    resource_id: str | None,
    user: User | None = None,
    user_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    activity_service.log_event(
        action=action,
        status=status,
        user=user,
        user_id=user_id,
        resource_type="chat_session",
        resource_id=resource_id,
        details=details,
    )


def _log_workspace_event(
    *,
    action: str,
    status: str,
    user: User,
    details: dict[str, Any] | None = None,
) -> None:
    activity_service.log_event(
        action=action,
        status=status,
        user=user,
        resource_type="workspace_chat",
        details=details,
    )


def _serialize_preview(value) -> str:
    try:
        return json.dumps(value, default=str)
    except Exception:
        return str(value)


def _json_safe(value: Any) -> Any:
    """Convert values into JSON-safe structures for storage and responses."""
    try:
        return json.loads(json.dumps(value, default=str))
    except Exception:
        return str(value)


def _is_identity_query(user_prompt: str) -> bool:
    normalized = user_prompt.strip().lower()
    patterns = [
        r"\bwho are you\b",
        r"\bwhat are you\b",
        r"\bwho created you\b",
        r"\bwho made you\b",
        r"\btell me about yourself\b",
        r"\bwhat is (?:vayent|valent)\b",
    ]
    return any(re.search(pattern, normalized) for pattern in patterns)


def _identity_response() -> str:
    return (
        "I am Vayent, a database analyst assistant AI created by Oluwatumininu Owolabi. "
        "I help bridge technical data work with business insight, planning, visualization, "
        "and schema understanding."
    )


def _analytical_follow_up_response() -> str:
    return (
        "I can help with strategy and business analysis, and I can ground that advice in your data "
        "when needed. If you want, I can inspect the most relevant tables, metrics, and time-based "
        "fields first so the next answer is more specific."
    )


def _mutation_clarification_response() -> str:
    return (
        "I can help make that database change. If the target record or required fields are not "
        "fully clear from the schema yet, I need the missing details before I can prepare the query."
    )


def _should_salvage_analytical_follow_up(user_prompt: str) -> bool:
    normalized = user_prompt.lower()
    keywords = [
        "increase",
        "decrease",
        "trend",
        "growth",
        "forecast",
        "projection",
        "improve",
        "competitor",
        "competition",
        "strategy",
        "business",
        "pricing",
        "retention",
        "conversion",
        "revenue",
        "profit",
        "margin",
        "market",
        "segment",
        "kpi",
        "metric",
        "dashboard",
        "ceo",
        "statistics",
        "statistical",
        "app",
        "how long",
        "how can",
        "why",
    ]
    return any(keyword in normalized for keyword in keywords)


def _looks_like_mutation_request(user_prompt: str) -> bool:
    normalized = user_prompt.lower()
    keywords = [
        "add ",
        "insert ",
        "create ",
        "register ",
        "update ",
        "edit ",
        "change ",
        "rename ",
        "delete ",
        "remove ",
        "set ",
    ]
    return any(keyword in normalized for keyword in keywords)


def _looks_like_record_lookup(user_prompt: str) -> bool:
    normalized = re.sub(r"\s+", " ", user_prompt.strip().lower())
    patterns = [
        r"^(?:gimme|give me|show me|tell me|get)\s+(?:the\s+)?(?:details|detail|info|information|record|records|profile|summary)\s+(?:on|for|about)\s+\S+",
        r"^(?:tell me about|details about|who is|look up)\s+\S+",
        r"^(?:find|get|show me)\s+(?:student|user|customer|person|member|employee|teacher|parent|client|patient|learner)\b.*\S",
    ]
    return any(re.search(pattern, normalized) for pattern in patterns)


def _schema_supports_record_lookup(schema_context: str) -> bool:
    lowered = schema_context.lower()
    table_keywords = (
        "student",
        "user",
        "customer",
        "person",
        "people",
        "member",
        "employee",
        "staff",
        "teacher",
        "parent",
        "contact",
        "client",
        "patient",
        "learner",
        "profile",
    )
    column_keywords = (
        "name",
        "first_name",
        "last_name",
        "full_name",
        "surname",
        "email",
        "username",
    )
    return any(keyword in lowered for keyword in table_keywords) and any(
        keyword in lowered for keyword in column_keywords
    )


def _normalize_result_value(value: Any) -> Any:
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()
    return value


def _is_sensitive_result_field(field_name: str) -> bool:
    lowered = field_name.strip().lower()
    return any(keyword in lowered for keyword in SENSITIVE_RESULT_FIELD_KEYWORDS)


def _sanitize_result_payload(value: Any) -> Any:
    """Remove sensitive keys and trim display values before storing/showing results."""
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if _is_sensitive_result_field(str(key)):
                continue
            sanitized[key] = _sanitize_result_payload(item)
        return sanitized

    if isinstance(value, list):
        return [_sanitize_result_payload(item) for item in value]

    return _normalize_result_value(value)


def _humanize_field_name(field_name: str) -> str:
    cleaned = re.sub(r"[_-]?id$", "", field_name.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", cleaned)
    cleaned = re.sub(r"[_-]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "ID"


def _coerce_datetime_label(value: Any) -> str | None:
    if not isinstance(value, str):
        return None

    candidate = value.strip()
    if not candidate:
        return None

    normalized = candidate.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.hour == 0 and parsed.minute == 0 and parsed.second == 0:
        return f"{parsed.strftime('%b')} {parsed.day}, {parsed.year}"

    hour = parsed.strftime("%I").lstrip("0") or "0"
    minute = parsed.strftime("%M")
    meridiem = parsed.strftime("%p")
    return f"{parsed.strftime('%b')} {parsed.day}, {parsed.year} at {hour}:{minute} {meridiem}"


def _format_record_value(value: Any) -> str:
    datetime_label = _coerce_datetime_label(value)
    if datetime_label:
        return datetime_label

    if isinstance(value, bool):
        return "yes" if value else "no"

    return str(value)


def _join_with_and(parts: list[str]) -> str:
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return f"{', '.join(parts[:-1])}, and {parts[-1]}"


def _record_subject(row: dict[str, Any]) -> str | None:
    full_name = row.get("full_name") or row.get("name")
    if isinstance(full_name, str) and full_name.strip():
        return full_name.strip()

    first_name = row.get("first_name")
    last_name = row.get("last_name")
    if isinstance(first_name, str) and first_name.strip():
        if isinstance(last_name, str) and last_name.strip():
            return f"{first_name.strip()} {last_name.strip()}"
        return first_name.strip()

    for key in ("email", "username", "matric_no", "student_id", "id"):
        value = row.get(key)
        if value not in (None, ""):
            return str(value)

    return None


def _summarize_single_record(row: dict[str, Any]) -> str:
    subject = _record_subject(row)
    intro = (
        f"I found 1 matching record for {subject}."
        if subject
        else "I found 1 matching record."
    )

    detail_field_candidates = [
        (("matric_no",), "matric number"),
        (("email",), "email"),
        (("phone", "phone_number"), "phone"),
        (("faculty", "faculty_name", "faculty_id"), "faculty"),
        (("department", "department_name", "department_id"), "department"),
        (("level", "level_name", "level_id"), "level"),
        (("class_name", "class"), "class"),
        (("status",), "status"),
        (("registered_at", "created_at"), "registered on"),
        (("updated_at",), "last updated"),
    ]

    details: list[str] = []
    used_keys = {"full_name", "name", "first_name", "last_name"}

    for keys, label in detail_field_candidates:
        for key in keys:
            value = row.get(key)
            if value in (None, "", [], {}):
                continue
            details.append(f"{label} {_format_record_value(value)}")
            used_keys.add(key)
            break

    if not details:
        for key, value in row.items():
            if key in used_keys or value in (None, "", [], {}):
                continue
            details.append(f"{_humanize_field_name(key)} {_format_record_value(value)}")
            if len(details) == 5:
                break

    if not details:
        return intro

    detail_label = "detail is" if len(details) == 1 else "details are"
    return f"{intro} The available {detail_label} {_join_with_and(details)}."


def _looks_like_result_format_request(user_prompt: str) -> bool:
    """Detect follow-ups that ask to re-present the previous answer."""
    normalized = re.sub(r"\s+", " ", (user_prompt or "").strip().lower())
    if not normalized:
        return False

    presentation_cues = (
        "format",
        "properly",
        "better",
        "clearer",
        "readable",
        "understand",
        "output",
        "present",
        "arrange",
        "organize",
        "table",
        "tabular",
        "bullet",
        "explain the output",
        "put it",
        "put them",
    )
    previous_result_cues = (
        "it",
        "this",
        "that",
        "result",
        "output",
        "details",
        "records",
        "rows",
        "above",
        "previous",
        "them",
        "their",
    )

    return any(cue in normalized for cue in presentation_cues) and any(
        cue in normalized for cue in previous_result_cues
    )


def _result_rows_from_payload(query_result: Any) -> tuple[list[Any], int, bool]:
    """Extract a row list from stored query_result payloads."""
    if not isinstance(query_result, dict):
        return [], 0, False

    rows = query_result.get("rows")
    if not isinstance(rows, list):
        result = query_result.get("result")
        if isinstance(result, list):
            rows = result
        elif result not in (None, ""):
            rows = [result]
        else:
            rows = []

    row_count = query_result.get("row_count")
    if not isinstance(row_count, int):
        row_count = len(rows)

    return rows, row_count, bool(query_result.get("truncated"))


def _latest_query_result_message(messages: list[ChatMessage]) -> ChatMessage | None:
    """Return the newest message with reusable query results."""
    sorted_messages = sorted(
        messages,
        key=lambda item: item.created_at or datetime.min,
        reverse=True,
    )
    for message in sorted_messages:
        rows, row_count, _ = _result_rows_from_payload(message.query_result)
        if rows or row_count == 0:
            return message
    return None


def _format_field_label(field_name: str) -> str:
    """Turn a DB column name into a compact display label."""
    label = _humanize_field_name(field_name).strip()
    if not label:
        return "Value"

    words = []
    for word in label.split():
        if word.lower() == "id":
            words.append("ID")
        elif word.lower() == "url":
            words.append("URL")
        elif word.lower() == "api":
            words.append("API")
        else:
            words.append(word[:1].upper() + word[1:])
    return " ".join(words)


def _row_heading(row: dict[str, Any]) -> tuple[str | None, set[str]]:
    """Build a readable heading for a record and return fields consumed by it."""
    used_fields: set[str] = set()

    full_name = row.get("full_name") or row.get("name")
    if isinstance(full_name, str) and full_name.strip():
        used_fields.add("full_name" if row.get("full_name") else "name")
        title = row.get("title")
        if isinstance(title, str) and title.strip():
            used_fields.add("title")
            return f"{title.strip()} {full_name.strip()}".strip(), used_fields
        return full_name.strip(), used_fields

    first_name = row.get("first_name")
    last_name = row.get("last_name")
    name_parts = []
    title = row.get("title")
    if isinstance(title, str) and title.strip():
        name_parts.append(title.strip())
        used_fields.add("title")
    if isinstance(first_name, str) and first_name.strip():
        name_parts.append(first_name.strip())
        used_fields.add("first_name")
    if isinstance(last_name, str) and last_name.strip():
        name_parts.append(last_name.strip())
        used_fields.add("last_name")
    if name_parts:
        return " ".join(name_parts), used_fields

    subject = _record_subject(row)
    if subject:
        return subject, used_fields
    return None, used_fields


def _infer_result_entity_label(rows: list[Any]) -> str:
    """Infer a small label for formatted result rows."""
    dict_rows = [row for row in rows if isinstance(row, dict)]
    if not dict_rows:
        return "records"

    roles = {
        str(row.get("role")).strip().lower()
        for row in dict_rows
        if row.get("role") not in (None, "")
    }
    if len(roles) == 1:
        role = next(iter(roles))
        return f"{role} records"

    keys = set().union(*(set(row.keys()) for row in dict_rows))
    for entity in ("lecturer", "student", "customer", "user", "employee", "staff"):
        if f"{entity}_id" in keys or entity in keys:
            return f"{entity} records"

    return "records"


def _ordered_row_items(row: dict[str, Any], used_fields: set[str]) -> list[tuple[str, Any]]:
    preferred_order = [
        "lecturer_id",
        "student_id",
        "user_id",
        "customer_id",
        "staff_id",
        "matric_no",
        "email",
        "phone",
        "faculty",
        "faculty_id",
        "department",
        "department_id",
        "level",
        "level_id",
        "role",
        "status",
        "created_at",
        "registered_at",
        "updated_at",
    ]
    ordered_keys = [
        key for key in preferred_order if key in row and key not in used_fields
    ]
    ordered_keys.extend(
        key
        for key in row.keys()
        if key not in used_fields and key not in ordered_keys
    )
    return [
        (key, row.get(key))
        for key in ordered_keys
        if row.get(key) not in (None, "", [], {})
    ]


def _format_rows_as_readable_records(
    rows: list[Any],
    row_count: int,
    truncated: bool,
    *,
    intro_prefix: str | None = None,
) -> str:
    """Format query rows in a supportable, human-readable structure."""
    if row_count == 0:
        return "I didn't find any matching records."

    sanitized_rows = _sanitize_result_payload(rows)
    if not isinstance(sanitized_rows, list):
        sanitized_rows = [sanitized_rows]

    entity_label = _infer_result_entity_label(sanitized_rows)
    intro = intro_prefix or f"I found {row_count} {entity_label}."
    lines = [intro, ""]

    for index, row in enumerate(sanitized_rows[:10], start=1):
        if isinstance(row, dict):
            heading, used_fields = _row_heading(row)
            lines.append(f"{index}. {heading or 'Record'}")
            for key, value in _ordered_row_items(row, used_fields):
                lines.append(f"   - {_format_field_label(key)}: {_format_record_value(value)}")
        else:
            lines.append(f"{index}. {_format_record_value(row)}")
        lines.append("")

    if row_count > len(sanitized_rows):
        lines.append(f"Showing {len(sanitized_rows)} of {row_count} records.")
    if truncated:
        lines.append("The result was truncated to a preview.")

    return "\n".join(lines).strip()


def _looks_like_most_active_user_prompt(user_prompt: str | None) -> bool:
    normalized = re.sub(r"\s+", " ", (user_prompt or "").strip().lower())
    return "most active" in normalized and "user" in normalized


def _count_value(row: dict[str, Any]) -> int | None:
    count_fields = (
        "activity_count",
        "action_count",
        "request_count",
        "api_request_count",
        "ai_request_count",
        "chat_count",
        "query_count",
        "total_activity",
        "total_activities",
        "total_requests",
        "total_count",
        "count",
    )
    for key in count_fields:
        value = row.get(key)
        if value in (None, ""):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _activity_subject(row: dict[str, Any]) -> str | None:
    for key in (
        "email",
        "actor_email",
        "user_email",
        "username",
        "actor_username",
        "name",
        "full_name",
    ):
        value = row.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return _record_subject(row)


def _format_count_phrase(count: int | None) -> str:
    if count is None:
        return ""
    if count == 1:
        return " with 1 recorded activity"
    return f" with {count} recorded activities"


def _format_most_active_user_response(
    rows: list[Any],
    row_count: int,
    truncated: bool,
) -> str | None:
    if row_count == 0 or not rows:
        return "I didn't find any user activity records."

    first_row = _sanitize_result_payload(rows[0])
    if not isinstance(first_row, dict):
        return None

    subject = _activity_subject(first_row)
    if not subject:
        return None

    count = _count_value(first_row)
    response = f"Your most active user is {subject}{_format_count_phrase(count)}."
    if truncated:
        response += " This is based on the returned preview."
    return response


def _is_activity_breakdown_row(row: dict[str, Any]) -> bool:
    keys = set(row.keys())
    return bool(
        {"activity_count", "action_count", "request_count", "count"} & keys
        and (
            {"action", "resource_type", "endpoint", "method"} & keys
            or any("request" in key.lower() or "chat" in key.lower() for key in keys)
        )
    )


def _activity_driver_label(row: dict[str, Any], count: int) -> str:
    endpoint = row.get("endpoint")
    method = row.get("method")
    action = row.get("action")
    resource_type = row.get("resource_type")

    if endpoint:
        method_label = f"{str(method).upper()} " if method else ""
        noun = "request" if count == 1 else "requests"
        return f"{count} {method_label}{endpoint} {noun}"

    action_label = str(action).replace("_", " ").replace(".", " ").strip()
    resource_label = str(resource_type).replace("_", " ").strip()
    label_parts = [part for part in (action_label, resource_label) if part]
    label = " / ".join(label_parts) if label_parts else "activity"
    return f"{count} {label}"


def _activity_category(row: dict[str, Any]) -> str:
    text = " ".join(
        str(row.get(key, ""))
        for key in ("action", "resource_type", "endpoint", "request_kind")
    ).lower()
    if "chat" in text or "ai" in text or "openai" in text:
        return "AI/chat"
    if "request" in text or row.get("endpoint") or row.get("method"):
        return "API"
    if "query" in text or "database" in text or "connection" in text:
        return "database"
    return "other"


def _activity_subject_phrase(user_prompt: str | None) -> str:
    compacted_prompt = re.sub(r"\s+", " ", (user_prompt or "").strip().lower())
    normalized = f" {compacted_prompt} "
    if " she " in normalized or " her " in normalized:
        return "She is"
    if " he " in normalized or " him " in normalized:
        return "He is"
    if " they " in normalized or " them " in normalized:
        return "They are"
    return "They are"


def _activity_category_phrase(category: str, count: int) -> str:
    if category == "API":
        noun = "request" if count == 1 else "requests"
    elif category == "AI/chat":
        noun = "action" if count == 1 else "actions"
    elif category == "database":
        noun = "database action" if count == 1 else "database actions"
    else:
        noun = "other action" if count == 1 else "other actions"
    return f"{count} {category} {noun}"


def _format_activity_breakdown_response(
    rows: list[Any],
    row_count: int,
    truncated: bool,
    *,
    user_prompt: str | None = None,
) -> str | None:
    dict_rows = [
        _sanitize_result_payload(row)
        for row in rows
        if isinstance(row, dict)
    ]
    dict_rows = [
        row for row in dict_rows if isinstance(row, dict) and _is_activity_breakdown_row(row)
    ]
    if not dict_rows:
        return None

    counted_rows: list[tuple[dict[str, Any], int]] = []
    for row in dict_rows:
        count = _count_value(row)
        if count is not None:
            counted_rows.append((row, count))
    if not counted_rows:
        return None

    total = sum(count for _, count in counted_rows)
    by_category: dict[str, int] = {}
    for row, count in counted_rows:
        category = _activity_category(row)
        by_category[category] = by_category.get(category, 0) + count

    category_parts = [
        _activity_category_phrase(category, count)
        for category, count in sorted(
            by_category.items(),
            key=lambda item: item[1],
            reverse=True,
        )
    ]
    top_drivers = [
        _activity_driver_label(row, count)
        for row, count in sorted(counted_rows, key=lambda item: item[1], reverse=True)[:3]
    ]

    total_label = "tracked action" if total == 1 else "tracked actions"
    response = (
        f"{_activity_subject_phrase(user_prompt)} the most active because "
        f"the returned activity breakdown shows {total} {total_label}"
    )
    if category_parts:
        response += f": {_join_with_and(category_parts)}."
    else:
        response += "."
    if top_drivers:
        response += f" Biggest drivers: {_join_with_and(top_drivers)}."
    if truncated or row_count > len(rows):
        response += " This is based on the returned preview, so the full total may be higher."
    return response


def _build_conversation_history(
    messages: list[ChatMessage],
    limit: int = CHAT_HISTORY_MESSAGE_LIMIT,
) -> list[dict[str, str]]:
    """Convert stored chat messages into assistant-readable history."""
    history: list[dict[str, str]] = []

    sorted_messages = sorted(messages, key=lambda item: item.created_at)
    if limit > 0 and len(sorted_messages) > limit:
        sorted_messages = sorted_messages[-limit:]

    for message in sorted_messages:
        history.append(
            {
                "role": "user",
                "content": message.user_prompt,
            }
        )

        assistant_parts = []
        if message.ai_explanation:
            assistant_parts.append(message.ai_explanation)
        if message.generated_sql:
            assistant_parts.append(f"SQL used: {message.generated_sql}")
        if message.query_result:
            row_count = message.query_result.get("row_count")
            if row_count is not None:
                assistant_parts.append(f"Row count: {row_count}.")
            rows = message.query_result.get("rows")
            if isinstance(rows, list) and rows:
                assistant_parts.append(
                    f"Result preview: {_serialize_preview(rows[:3])}"
                )
        if message.requires_confirmation:
            assistant_parts.append("A write query was prepared and is awaiting confirmation.")

        if assistant_parts:
            history.append(
                {
                    "role": "assistant",
                    "content": " ".join(assistant_parts),
                }
            )

    return history


def _fallback_query_response(
    results: list[dict],
    row_count: int,
    truncated: bool,
    *,
    user_prompt: str | None = None,
) -> str:
    """Provide a deterministic fallback when AI result narration fails."""
    if row_count == 0:
        return "I didn't find any matching records."

    if _looks_like_most_active_user_prompt(user_prompt):
        active_user_response = _format_most_active_user_response(
            results,
            row_count,
            truncated,
        )
        if active_user_response:
            return active_user_response

    if user_prompt and "why" in user_prompt.strip().lower():
        activity_response = _format_activity_breakdown_response(
            results,
            row_count,
            truncated,
            user_prompt=user_prompt,
        )
        if activity_response:
            return activity_response

    if row_count == 1 and results:
        first_row = _sanitize_result_payload(results[0])
        if isinstance(first_row, dict) and len(first_row) == 1:
            value = next(iter(first_row.values()))
            return f"The answer is {value}."
        if isinstance(first_row, dict):
            return _summarize_single_record(first_row)
        return f"I found 1 matching record: {_serialize_preview(first_row)}"

    if results:
        activity_response = _format_activity_breakdown_response(
            results,
            row_count,
            truncated,
            user_prompt=user_prompt,
        )
        if activity_response:
            return activity_response
        return _format_rows_as_readable_records(results, row_count, truncated)

    return f"I found {row_count} matching records."


def _build_workspace_history(
    history: list[Any],
    limit: int = WORKSPACE_HISTORY_MESSAGE_LIMIT,
) -> list[dict[str, str]]:
    """Normalize client-provided workspace history for the planner."""
    if limit > 0 and len(history) > limit:
        history = history[-limit:]

    messages: list[dict[str, str]] = []
    for item in history:
        role = getattr(item, "role", None)
        content = getattr(item, "content", None)
        if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
            messages.append({"role": role, "content": content.strip()})
    return messages


def _fallback_workspace_query_response(query_runs: list[dict[str, Any]]) -> str:
    """Provide a deterministic workspace summary when AI narration fails."""
    successful_runs = [run for run in query_runs if not run.get("error")]
    failed_runs = [run for run in query_runs if run.get("error")]

    if not successful_runs and failed_runs:
        first_failure = failed_runs[0]
        return (
            "I couldn't complete the workspace query. "
            f"{first_failure.get('connection_name', 'One source')} returned: {first_failure.get('error')}"
        )

    if len(successful_runs) == 1 and not failed_runs:
        run = successful_runs[0]
        return (
            f"For {run['connection_name']}, "
            f"{_fallback_query_response(run.get('rows', []), run.get('row_count', 0), run.get('truncated', False))}"
        )

    summary_parts = []
    for run in successful_runs:
        part = f"{run['connection_name']}: {run.get('row_count', 0)} row"
        if run.get("row_count", 0) != 1:
            part += "s"
        summary_parts.append(part)

    response = (
        "I checked multiple databases. " + _join_with_and(summary_parts) + "."
        if summary_parts
        else "I checked the selected databases."
    )

    if successful_runs:
        preview_parts = []
        for run in successful_runs[:2]:
            rows = run.get("rows", [])
            if rows:
                preview_parts.append(
                    f"{run['connection_name']} preview: {_serialize_preview(rows[:2])}"
                )
        if preview_parts:
            response += " " + " ".join(preview_parts)

    if failed_runs:
        failure_parts = [
            f"{run.get('connection_name', 'One source')} failed: {run.get('error')}"
            for run in failed_runs
        ]
        response += " " + " ".join(failure_parts)

    return response


def _mutation_success_message(row_count: int | None) -> str:
    if row_count is None:
        return "The query ran successfully."
    if row_count == 1:
        return "The query ran successfully and affected 1 row."
    return f"The query ran successfully and affected {row_count} rows."


def _should_use_ai_result_response(
    user_prompt: str,
    row_count: int,
    truncated: bool,
) -> bool:
    normalized = user_prompt.strip().lower()
    analytical_keywords = [
        "trend",
        "why",
        "improve",
        "increase",
        "decrease",
        "retention",
        "revenue",
        "growth",
        "strategy",
        "forecast",
        "segment",
        "compare",
        "analysis",
        "recommend",
        "opportunit",
        "risk",
    ]
    simple_starts = (
        "show ",
        "list ",
        "get ",
        "find ",
        "count ",
        "how many",
        "which ",
        "what is ",
        "what's ",
    )

    if any(keyword in normalized for keyword in analytical_keywords):
        return True

    if not truncated and row_count <= 3:
        return False

    if truncated or row_count > 20:
        return True

    return not normalized.startswith(simple_starts)


def _summarize_chat_title(user_prompt: str, max_chars: int = 72) -> str:
    """Create a short session title from the first user message."""
    cleaned = re.sub(r"\s+", " ", user_prompt or "").strip().strip("\"'`")
    cleaned = re.sub(r"[.!?]+$", "", cleaned)

    if not cleaned:
        return "New conversation"

    prefixes = [
        r"^(?:can|could|would)\s+you\s+",
        r"^please\s+",
        r"^help\s+me\s+",
        r"^(?:tell|show|give)\s+me\s+",
        r"^i\s+(?:want|need)\s+to\s+(?:know|understand)\s+",
        r"^what\s+is\s+",
        r"^what\s+are\s+",
        r"^what's\s+",
        r"^how\s+can\s+i\s+",
        r"^how\s+do\s+i\s+",
    ]
    updated = True
    while updated:
        updated = False
        for pattern in prefixes:
            next_cleaned = re.sub(pattern, "", cleaned, count=1, flags=re.IGNORECASE).strip()
            if next_cleaned != cleaned and next_cleaned:
                cleaned = next_cleaned
                updated = True

    clause_parts = re.split(r"[;:!?]|\s+-\s+|,\s+(?:but|so|because)\s+", cleaned, maxsplit=1)
    title = clause_parts[0].strip() if clause_parts else cleaned
    if not title:
        title = cleaned

    if len(title) > max_chars:
        truncated = title[: max_chars + 1]
        if " " in truncated:
            truncated = truncated.rsplit(" ", 1)[0]
        title = f"{truncated.rstrip(' ,.-')}..."

    return title[:1].upper() + title[1:] if title else "New conversation"


def _should_auto_title_session(
    session: ChatSession,
    connection_name: str | None = None,
) -> bool:
    raw_title = (getattr(session, "title", None) or "").strip()
    if not raw_title:
        return True
    if connection_name and raw_title.casefold() == connection_name.strip().casefold():
        return True
    return False


def _resolved_session_title(
    session: ChatSession,
    connection_name: str | None = None,
) -> str | None:
    if not _should_auto_title_session(session, connection_name):
        return (getattr(session, "title", None) or "").strip() or connection_name

    messages = list(getattr(session, "messages", []) or [])
    if messages:
        first_message = sorted(messages, key=lambda item: item.created_at)[0]
        summarized_title = _summarize_chat_title(first_message.user_prompt)
        if summarized_title:
            return summarized_title

    return connection_name or (getattr(session, "title", None) or "").strip() or "New conversation"


async def _persist_message(
    db: AsyncSession,
    session: ChatSession,
    message: ChatMessage,
) -> ChatMessage:
    session.updated_at = datetime.utcnow()
    db.add(message)
    await db.commit()
    await db.refresh(message)
    return message


async def _safe_rollback(db: AsyncSession) -> None:
    """Reset a failed transaction without masking the original error."""
    rollback = getattr(db, "rollback", None)
    if rollback is None:
        return

    in_transaction = getattr(db, "in_transaction", None)
    if callable(in_transaction):
        try:
            if not in_transaction():
                return
        except Exception:
            logger.warning("Could not determine transaction state during rollback", exc_info=True)

    try:
        await rollback()
    except Exception:
        logger.warning("Failed to rollback chat transaction cleanly", exc_info=True)


async def _release_reserved_tokens_safely(
    *,
    user_id: str,
    reserved_tokens: int,
    db: AsyncSession,
) -> None:
    """Best-effort reservation cleanup after failures."""
    if reserved_tokens <= 0:
        return

    await _safe_rollback(db)

    try:
        await token_usage_service.release_tokens(
            user_id=user_id,
            reserved_tokens=reserved_tokens,
            db=db,
        )
    except Exception:
        logger.error("Failed to release reserved tokens for user %s", user_id, exc_info=True)


def _chat_error_message(exc: Exception) -> str:
    """Map internal chat failures to safer user-facing copy."""
    message = str(exc).strip()
    lowered = message.lower()

    if not message:
        return "I couldn't complete that request because of an unexpected server error. Please try again."

    if message.startswith("Query failed:") or message.startswith("Connection failed:"):
        return message

    if message.startswith("I couldn't process that request because the AI service"):
        return error_message_service.ai_failed_message(message)

    if "ai service" in lowered or "openai" in lowered:
        return error_message_service.ai_failed_message(message)

    if "object of type datetime is not json serializable" in lowered:
        return (
            "I ran the query, but the result included data I couldn't save cleanly. "
            "Please try again."
        )

    if "transaction has been rolled back" in lowered or "pendingrollbackerror" in lowered:
        return "The request could not be completed because the previous database transaction failed. Please try again."

    return "I couldn't complete that request because of an unexpected server error. Please try again."


async def _persist_error_message(
    *,
    db: AsyncSession,
    session: ChatSession,
    message: ChatMessage,
    error_message: str,
) -> ChatMessage | None:
    """Persist a user-safe error response even after a failed flush/commit."""
    await _safe_rollback(db)

    message.query_result = None
    message.requires_confirmation = False
    message.execution_status = "error"
    message.ai_explanation = error_message
    message.created_at = message.created_at or datetime.utcnow()

    try:
        return await _persist_message(db, session, message)
    except Exception:
        logger.error("Failed to persist chat error message", exc_info=True)
        return None


def _build_chat_response(
    message: ChatMessage,
    *,
    confirmation_token: str | None = None,
) -> ChatMessageResponse:
    """Build a response from a persisted or in-memory message."""
    message.requires_confirmation = bool(message.requires_confirmation)
    message.created_at = message.created_at or datetime.utcnow()
    payload = ChatMessageResponse.model_validate(message).model_dump()
    payload["confirmation_token"] = confirmation_token
    return ChatMessageResponse(**payload)


async def _load_message_for_user(
    message_id: str,
    user_id: str,
    db: AsyncSession,
) -> ChatMessage | None:
    result = await db.execute(
        select(ChatMessage)
        .join(ChatSession, ChatSession.id == ChatMessage.session_id)
        .where(
            ChatMessage.id == message_id,
            ChatSession.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


@router.post("/sessions", response_model=ChatSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_chat_session(
    data: ChatSessionCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Create a new chat session."""
    result = await db.execute(
        select(DatabaseConnection).where(
            DatabaseConnection.id == data.connection_id,
            DatabaseConnection.user_id == current_user.id,
        )
    )
    connection = result.scalar_one_or_none()

    if not connection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Database connection not found",
        )

    session_title = data.title.strip() if data.title and data.title.strip() else None
    session = ChatSession(
        id=str(uuid.uuid4()),
        user_id=current_user.id,
        connection_id=data.connection_id,
        title=session_title,
    )

    db.add(session)
    await db.commit()

    result = await db.execute(
        select(ChatSession)
        .options(selectinload(ChatSession.messages))
        .where(ChatSession.id == session.id)
    )
    created_session = result.scalar_one()
    payload = ChatSessionResponse.model_validate(created_session).model_dump()
    payload["title"] = _resolved_session_title(created_session, connection.name)
    payload["connection_name"] = connection.name
    _log_chat_event(
        action="chat.session_created",
        status="success",
        user=current_user,
        resource_id=session.id,
        details={
            "connection_id": connection.id,
            "connection_name": connection.name,
            "title": payload["title"],
        },
    )
    return ChatSessionResponse(**payload)


@router.get("/sessions", response_model=List[ChatSessionSummaryResponse])
async def list_chat_sessions(
    connection_id: str | None = Query(default=None),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    """List chat sessions for the current user."""
    query = (
        select(ChatSession)
        .options(selectinload(ChatSession.messages))
        .where(ChatSession.user_id == current_user.id)
        .order_by(ChatSession.updated_at.desc())
    )

    if connection_id:
        query = query.where(ChatSession.connection_id == connection_id)

    result = await db.execute(query)
    sessions = result.scalars().all()
    connection_ids = {session.connection_id for session in sessions}
    connections = {}
    if connection_ids:
        connection_result = await db.execute(
            select(DatabaseConnection).where(DatabaseConnection.id.in_(connection_ids))
        )
        connections = {
            connection.id: connection.name for connection in connection_result.scalars().all()
        }

    summaries = []
    for session in sessions:
        sorted_messages = sorted(session.messages, key=lambda item: item.created_at)
        if len(sorted_messages) == 0:
            continue
        last_message = sorted_messages[-1] if sorted_messages else None
        summaries.append(
            ChatSessionSummaryResponse(
                id=session.id,
                user_id=session.user_id,
                connection_id=session.connection_id,
                connection_name=connections.get(session.connection_id),
                title=_resolved_session_title(
                    session,
                    connections.get(session.connection_id),
                ),
                created_at=session.created_at,
                updated_at=session.updated_at,
                message_count=len(sorted_messages),
                last_user_prompt=last_message.user_prompt if last_message else None,
                last_response_preview=last_message.ai_explanation if last_message else None,
            )
        )

    return summaries


@router.get("/sessions/{session_id}", response_model=ChatSessionResponse)
async def get_chat_session(
    session_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Get a chat session with all messages."""
    result = await db.execute(
        select(ChatSession)
        .options(selectinload(ChatSession.messages))
        .where(
            ChatSession.id == session_id,
            ChatSession.user_id == current_user.id,
        )
    )
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat session not found",
        )

    connection = await db_connection_service.get_connection(session.connection_id, db)
    payload = ChatSessionResponse.model_validate(session).model_dump()
    payload["title"] = _resolved_session_title(
        session,
        connection.name if connection else None,
    )
    payload["connection_name"] = connection.name if connection else None
    return ChatSessionResponse(**payload)


@router.post("/sessions/{session_id}/messages", response_model=ChatMessageResponse)
async def send_message(
    session_id: str,
    data: ChatMessageCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Process a user message as a conversational DB assistant response.
    """
    current_user_id = current_user.id

    result = await db.execute(
        select(ChatSession)
        .options(selectinload(ChatSession.messages))
        .where(
            ChatSession.id == session_id,
            ChatSession.user_id == current_user_id,
        )
    )
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat session not found",
        )

    connection = await db_connection_service.get_connection(session.connection_id, db)
    if not connection or connection.user_id != current_user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Database connection not found",
        )

    message = ChatMessage(
        id=str(uuid.uuid4()),
        session_id=session_id,
        user_prompt=data.user_prompt,
        execution_status="pending",
    )

    if len(session.messages) == 0 and _should_auto_title_session(session, connection.name):
        session.title = _summarize_chat_title(data.user_prompt)

    _log_chat_event(
        action="chat.message_received",
        status="success",
        user=current_user,
        resource_id=session_id,
        details={
            "connection_id": connection.id,
            "connection_name": connection.name,
            "prompt_preview": activity_service.preview_text(data.user_prompt),
            "existing_message_count": len(session.messages),
        },
    )

    confirmation_token = None
    reserved_tokens = 0
    token_usage = {"prompt_tokens": 0, "completion_tokens": 0}

    def add_usage(payload: dict | None) -> None:
        if not payload:
            return
        token_usage["prompt_tokens"] += payload.get("prompt_tokens", 0) or 0
        token_usage["completion_tokens"] += payload.get("completion_tokens", 0) or 0

    try:
        if _is_identity_query(data.user_prompt):
            message.execution_status = "answered"
            message.ai_explanation = _identity_response()
            saved_message = await _persist_message(db, session, message)
            _log_chat_event(
                action="chat.message_processed",
                status="success",
                user=current_user,
                resource_id=session_id,
                details={
                    "message_id": saved_message.id,
                    "connection_id": connection.id,
                    "connection_name": connection.name,
                    "execution_status": saved_message.execution_status,
                    "prompt_preview": activity_service.preview_text(data.user_prompt),
                    "response_preview": activity_service.preview_text(saved_message.ai_explanation),
                    "mode": "identity_response",
                },
            )
            return ChatMessageResponse.model_validate(saved_message)

        conversation_history = _build_conversation_history(session.messages)
        schema = await schema_discovery_service.get_connection_schema(connection.id, db)
        schema_context = (
            await schema_discovery_service.get_schema_for_rag(schema.id, db)
            if schema
            else "No synced schema is currently available for this connection."
        )

        estimated_tokens = token_usage_service.estimate_request_tokens(
            data.user_prompt,
            schema_context,
            conversation_history,
            completion_allowance=CHAT_PLANNING_COMPLETION_ALLOWANCE,
        )

        reservation = await token_usage_service.reserve_tokens(
            current_user_id,
            estimated_tokens,
            db,
        )
        reserved_tokens = reservation.get("reserved_tokens", 0)

        plan = await sql_generation_service.plan_response(
            user_query=data.user_prompt,
            schema_context=schema_context,
            database_type=connection.db_type.value,
            conversation_history=conversation_history,
        )
        add_usage(plan.get("usage"))

        if plan.get("error") and not plan.get("response"):
            if _looks_like_business_advice(data.user_prompt):
                plan["action"] = "respond"
                plan["response"] = _offline_business_advice_fallback(data.user_prompt)
            else:
                raise RuntimeError(error_message_service.ai_failed_message(plan["error"]))

        action = plan.get("action", "clarify")
        planned_response = (plan.get("response") or "").strip()

        if action == "refuse" and _should_salvage_analytical_follow_up(data.user_prompt):
            action = "clarify"
            planned_response = _analytical_follow_up_response()

        if action == "refuse" and _looks_like_mutation_request(data.user_prompt):
            write_plan = await sql_generation_service.plan_response(
                user_query=data.user_prompt,
                schema_context=schema_context,
                database_type=connection.db_type.value,
                conversation_history=conversation_history,
                intent_hint="write_request",
            )
            add_usage(write_plan.get("usage"))
            action = write_plan.get("action", action)
            planned_response = (write_plan.get("response") or planned_response).strip()
            if write_plan.get("sql"):
                plan["sql"] = write_plan.get("sql")

            if action == "refuse":
                action = "clarify"
                planned_response = _mutation_clarification_response()

        if (
            action in {"refuse", "clarify"}
            and _looks_like_record_lookup(data.user_prompt)
            and _schema_supports_record_lookup(schema_context)
        ):
            lookup_plan = await sql_generation_service.plan_response(
                user_query=data.user_prompt,
                schema_context=schema_context,
                database_type=connection.db_type.value,
                conversation_history=conversation_history,
                intent_hint="record_lookup",
            )
            add_usage(lookup_plan.get("usage"))

            lookup_action = lookup_plan.get("action", action)
            lookup_response = (lookup_plan.get("response") or planned_response).strip()
            if lookup_plan.get("sql"):
                plan["sql"] = lookup_plan.get("sql")

            if lookup_action != "refuse":
                action = lookup_action
                planned_response = lookup_response

        if action in {"respond", "clarify", "refuse"}:
            message.execution_status = "answered" if action == "respond" else action
            message.ai_explanation = (
                planned_response
                or "I can help with your data, the app, and business strategy, but I need a bit more context to give you a useful answer."
            )
        else:
            if not schema:
                message.execution_status = "clarify"
                message.ai_explanation = (
                    "I can help with that. To ground it in live database data, I need the latest schema "
                    "for this connection first. Please sync the schema, or ask for high-level guidance "
                    "and I can help without querying."
                )
                saved_message = await _persist_message(db, session, message)
                _log_chat_event(
                    action="chat.message_processed",
                    status="warning",
                    user=current_user,
                    resource_id=session_id,
                    details={
                        "message_id": saved_message.id,
                        "connection_id": connection.id,
                        "connection_name": connection.name,
                        "execution_status": saved_message.execution_status,
                        "prompt_preview": activity_service.preview_text(data.user_prompt),
                        "response_preview": activity_service.preview_text(saved_message.ai_explanation),
                        "reason": "schema_not_synced",
                    },
                )
                return ChatMessageResponse.model_validate(saved_message)

            generated_sql = (plan.get("sql") or "").strip()
            if not generated_sql:
                message.execution_status = "error"
                message.ai_explanation = (
                    "I couldn't form a valid query from that request using the current schema."
                )
            else:
                message.generated_sql = generated_sql
                safety_check = await query_execution_service.validate_and_prepare(
                    generated_sql
                )

                if not safety_check["is_safe"]:
                    message.execution_status = "error"
                    message.ai_explanation = (
                        "I couldn't safely run that request. "
                        f"{safety_check.get('error') or 'Please rephrase the question.'}"
                    )
                elif safety_check["is_destructive"]:
                    confirmation_token = await query_execution_service.request_confirmation(
                        user_id=current_user_id,
                        connection_id=connection.id,
                        query=generated_sql,
                        db=db,
                    )
                    message.requires_confirmation = True
                    message.execution_status = "awaiting_confirmation"
                    message.ai_explanation = (
                        planned_response
                        or "I prepared the database change for you. Review the SQL before confirming it."
                    )
                else:
                    username, password = db_connection_service.decrypt_credentials(connection)
                    execution_result = await query_execution_service.execute_query(
                        connection=connection,
                        query=generated_sql,
                        username_decrypted=username,
                        password_decrypted=password,
                        user_id=current_user_id,
                        db=db,
                        safety_check=safety_check,
                    )

                    if execution_result.get("success"):
                        rows = _sanitize_result_payload(
                            execution_result.get("result", []) or []
                        )
                        row_count = execution_result.get("row_count", 0) or 0
                        truncated = bool(execution_result.get("truncated"))
                        message.query_result = _json_safe(
                            {
                                "rows": rows,
                                "row_count": row_count,
                                "truncated": truncated,
                            }
                        )
                        message.execution_status = "executed"

                        if _should_use_ai_result_response(
                            data.user_prompt,
                            row_count,
                            truncated,
                        ):
                            assistant_response = await sql_generation_service.generate_result_response(
                                user_query=data.user_prompt,
                                query=generated_sql,
                                results=rows,
                                row_count=row_count,
                                truncated=truncated,
                            )
                            add_usage(assistant_response.get("usage"))
                            message.ai_explanation = (
                                assistant_response.get("response")
                                or _fallback_query_response(
                                    rows,
                                    row_count,
                                    truncated,
                                    user_prompt=data.user_prompt,
                                )
                            )
                        else:
                            message.ai_explanation = _fallback_query_response(
                                rows,
                                row_count,
                                truncated,
                                user_prompt=data.user_prompt,
                            )
                    else:
                        message.execution_status = "error"
                        message.ai_explanation = execution_result.get("error")

        saved_message = await _persist_message(db, session, message)

        if reserved_tokens > 0:
            total_tokens = token_usage["prompt_tokens"] + token_usage["completion_tokens"]
            if total_tokens > 0:
                await token_usage_service.finalize_tokens(
                    user_id=current_user_id,
                    reserved_tokens=reserved_tokens,
                    prompt_tokens=token_usage["prompt_tokens"],
                    completion_tokens=token_usage["completion_tokens"],
                    db=db,
                    session_id=session_id,
                    message_id=saved_message.id,
                    request_kind="chat",
                )
            else:
                await token_usage_service.release_tokens(
                    user_id=current_user_id,
                    reserved_tokens=reserved_tokens,
                    db=db,
                )
            reserved_tokens = 0

        _log_chat_event(
            action="chat.message_processed",
            status="success" if saved_message.execution_status != "error" else "error",
            user=current_user,
            resource_id=session_id,
            details={
                "message_id": saved_message.id,
                "connection_id": connection.id,
                "connection_name": connection.name,
                "execution_status": saved_message.execution_status,
                "requires_confirmation": bool(saved_message.requires_confirmation),
                "confirmation_token_issued": bool(confirmation_token),
                "prompt_preview": activity_service.preview_text(data.user_prompt),
                "response_preview": activity_service.preview_text(saved_message.ai_explanation),
                "sql_preview": activity_service.preview_sql(saved_message.generated_sql),
                "row_count": _extract_row_count_for_logs(saved_message.query_result),
                "prompt_tokens": token_usage["prompt_tokens"],
                "completion_tokens": token_usage["completion_tokens"],
            },
        )

        return _build_chat_response(
            saved_message,
            confirmation_token=confirmation_token,
        )
    except TokenLimitExceededError as exc:
        logger.info("Token limit reached for user %s: %s", current_user_id, exc)
        saved_message = await _persist_error_message(
            db=db,
            session=session,
            message=message,
            error_message=str(exc),
        )
        _log_chat_event(
            action="chat.message_processed",
            status="warning",
            resource_id=session_id,
            user_id=current_user_id,
            details={
                "message_id": getattr(saved_message, "id", None),
                "connection_id": connection.id,
                "connection_name": connection.name,
                "execution_status": "error",
                "prompt_preview": activity_service.preview_text(data.user_prompt),
                "error": str(exc),
            },
        )
        return _build_chat_response(saved_message or message)
    except Exception as exc:
        logger.error("Error processing chat message: %s", exc, exc_info=True)
        if reserved_tokens > 0:
            await _release_reserved_tokens_safely(
                user_id=current_user_id,
                reserved_tokens=reserved_tokens,
                db=db,
            )
            reserved_tokens = 0

        saved_message = await _persist_error_message(
            db=db,
            session=session,
            message=message,
            error_message=_chat_error_message(exc),
        )
        _log_chat_event(
            action="chat.message_processed",
            status="error",
            user=current_user,
            resource_id=session_id,
            details={
                "message_id": getattr(saved_message, "id", None),
                "connection_id": connection.id,
                "connection_name": connection.name,
                "prompt_preview": activity_service.preview_text(data.user_prompt),
                **activity_service.exception_details(exc),
            },
        )
        return _build_chat_response(saved_message or message)


@router.post("/workspace/message", response_model=WorkspaceChatMessageResponse)
async def send_workspace_message(
    data: WorkspaceChatMessageCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Process an ephemeral multi-source workspace message."""
    current_user_id = current_user.id
    created_at = datetime.utcnow()
    selected_source_ids = data.source_ids or data.connection_ids
    active_source_id = data.active_source_id or data.active_connection_id
    response_payload: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "user_prompt": data.user_prompt,
        "ai_explanation": None,
        "execution_status": "pending",
        "active_connection_id": data.active_connection_id or active_source_id,
        "active_source_id": active_source_id,
        "targeted_connection_ids": [],
        "targeted_source_ids": [],
        "generated_queries": [],
        "query_results": [],
        "warnings": [],
        "created_at": created_at,
    }
    reserved_tokens = 0
    token_usage = {"prompt_tokens": 0, "completion_tokens": 0}

    def add_usage(payload: dict | None) -> None:
        if not payload:
            return
        token_usage["prompt_tokens"] += payload.get("prompt_tokens", 0) or 0
        token_usage["completion_tokens"] += payload.get("completion_tokens", 0) or 0

    try:
        _log_workspace_event(
            action="workspace.message_received",
            status="success",
            user=current_user,
            details={
                "active_connection_id": data.active_connection_id,
                "active_source_id": active_source_id,
                "selected_connection_count": len(data.connection_ids or []),
                "selected_source_count": len(selected_source_ids or []),
                "prompt_preview": activity_service.preview_text(data.user_prompt),
                "history_length": len(data.history),
            },
        )
        available_connections = await db_connection_service.get_user_connections(
            current_user_id,
            db,
        )
        available_lookup = {
            connection.id: connection for connection in available_connections
        }
        spreadsheet_lookup = {}
        candidate_spreadsheet_ids = [
            source_id
            for source_id in selected_source_ids
            if source_id not in available_lookup
        ]
        if candidate_spreadsheet_ids:
            available_spreadsheets = await spreadsheet_service.get_user_sources(
                current_user_id,
                db,
            )
            spreadsheet_lookup = {
                source.id: source for source in available_spreadsheets
            }
        missing_source_ids = [
            source_id
            for source_id in selected_source_ids
            if source_id not in available_lookup and source_id not in spreadsheet_lookup
        ]
        if missing_source_ids:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="One or more selected sources were not found.",
            )

        ordered_connections = [
            available_lookup[source_id]
            for source_id in selected_source_ids
            if source_id in available_lookup
        ]
        ordered_spreadsheets = [
            spreadsheet_lookup[source_id]
            for source_id in selected_source_ids
            if source_id in spreadsheet_lookup
        ]
        selected_lookup = {
            connection.id: connection for connection in ordered_connections
        }
        active_connection = (
            available_lookup.get(active_source_id)
            or (ordered_connections[0] if ordered_connections else None)
        )
        if active_connection:
            response_payload["active_connection_id"] = active_connection.id
        conversation_history = _build_workspace_history(data.history)

        workspace_connections: list[dict[str, Any]] = []
        combined_schema_parts: list[str] = []
        for connection in ordered_connections:
            schema = await schema_discovery_service.get_connection_schema(connection.id, db)
            schema_context = (
                await schema_discovery_service.get_schema_for_rag(schema.id, db)
                if schema
                else "No synced schema is currently available for this connection."
            )
            workspace_connection = {
                "id": connection.id,
                "name": connection.name,
                "database_name": connection.database_name,
                "db_type": connection.db_type.value,
                "schema_context": schema_context,
                "has_schema": bool(schema),
            }
            workspace_connections.append(workspace_connection)
            combined_schema_parts.append(
                "\n".join(
                    [
                        f"Connection ID: {connection.id}",
                        f"Connection name: {connection.name}",
                        f"Database name: {connection.database_name}",
                        f"Database type: {connection.db_type.value}",
                        "Schema:",
                        schema_context,
                    ]
                )
            )

        spreadsheet_contexts: list[dict[str, Any]] = []
        for source in ordered_spreadsheets:
            schema_context = spreadsheet_service.format_source_for_ai(source)
            # Find a small set of matching raw rows for the user's prompt so the
            # workspace planner can use row-level evidence when deciding how to
            # route and plan queries.
            try:
                matched_rows = spreadsheet_service._matching_spreadsheet_rows(
                    source,
                    prompt=data.user_prompt,
                    limit=12,
                )
            except Exception:
                matched_rows = []

            spreadsheet_contexts.append(
                {
                    "id": source.id,
                    "name": source.name,
                    "database_name": source.original_filename or source.name,
                    "db_type": "spreadsheet",
                    "schema_context": schema_context,
                    "has_schema": True,
                    "source_rows": matched_rows,
                }
            )
            combined_schema_parts.append(
                "\n".join(
                    [
                        f"Source ID: {source.id}",
                        f"Source name: {source.name}",
                        "Source type: spreadsheet",
                        "Schema:",
                        schema_context,
                    ]
                )
            )

        if _is_identity_query(data.user_prompt):
            response_payload["execution_status"] = "answered"
            response_payload["ai_explanation"] = _identity_response()
            _log_workspace_event(
                action="workspace.message_processed",
                status="success",
                user=current_user,
                details={
                    "execution_status": response_payload["execution_status"],
                    "active_connection_id": data.active_connection_id,
                    "active_source_id": active_source_id,
                    "prompt_preview": activity_service.preview_text(data.user_prompt),
                    "response_preview": activity_service.preview_text(response_payload["ai_explanation"]),
                    "mode": "identity_response",
                },
            )
            return WorkspaceChatMessageResponse.model_validate(response_payload)

        combined_schema_context = "\n\n---\n\n".join(combined_schema_parts)
        estimated_tokens = token_usage_service.estimate_request_tokens(
            data.user_prompt,
            combined_schema_context,
            conversation_history,
            completion_allowance=WORKSPACE_PLANNING_COMPLETION_ALLOWANCE,
        )
        reservation = await token_usage_service.reserve_tokens(
            current_user_id,
            estimated_tokens,
            db,
        )
        reserved_tokens = reservation.get("reserved_tokens", 0)

        spreadsheet_runs: list[dict[str, Any]] = []
        for source in ordered_spreadsheets:
            try:
                # For link-backed spreadsheets, refresh the source so we query live data
                source_kind = (
                    source.source_kind.value
                    if hasattr(source.source_kind, "value")
                    else str(source.source_kind)
                )
                if source_kind == SpreadsheetSourceKind.LINK.value:
                    try:
                        source = await spreadsheet_service.sync_source(
                            source_id=source.id,
                            user_id=current_user_id,
                            db=db,
                        )
                    except Exception as exc:
                        logger.warning("Failed to sync spreadsheet link %s: %s", getattr(source, "id", "?"), exc)

                # Build the workspace summary from the (possibly refreshed) source
                try:
                    spreadsheet_runs.append(
                        spreadsheet_service.build_workspace_summary(
                            source=source,
                            user_prompt=data.user_prompt,
                        )
                    )
                except Exception as exc:
                    logger.warning("Failed to build spreadsheet summary for %s: %s", getattr(source, "id", "?"), exc)
            except Exception:
                # Defensive: skip problematic sources without failing the whole request
                logger.exception("Unexpected error while preparing spreadsheet source")

        query_runs: list[dict[str, Any]] = []
        any_success = False
        warnings: list[str] = []
        terminal_response_ready = False

        if workspace_connections:
            plan = await sql_generation_service.plan_workspace_response(
                user_query=data.user_prompt,
                active_connection_id=(active_connection or ordered_connections[0]).id,
                workspace_connections=workspace_connections,
                conversation_history=conversation_history,
            )
            add_usage(plan.get("usage"))
            warnings = list(plan.get("warnings") or [])
            response_payload["warnings"] = warnings
            action = plan.get("action", "clarify")
            planned_response = (plan.get("response") or "").strip()
            planner_parse_failed = any(
                "invalid response" in str(warning).lower()
                for warning in warnings
            )

            if plan.get("error") and not plan.get("response") and not planner_parse_failed:
                raise RuntimeError(error_message_service.ai_failed_message(plan["error"]))

            if action in {"respond", "clarify", "refuse"} and not (
                planner_parse_failed or spreadsheet_runs
            ):
                response_payload["execution_status"] = "answered" if action == "respond" else action
                response_payload["ai_explanation"] = (
                    planned_response
                    or "I can compare the selected sources, but I need a bit more context to choose the right metric, table, or time window."
                )
            else:
                raw_query_plans = list(plan.get("queries") or [])
                validated_query_plans: list[dict[str, str]] = []
                seen_connections: set[str] = set()

                for raw_plan in raw_query_plans:
                    connection_id = str(raw_plan.get("connection_id") or "").strip()
                    sql = str(raw_plan.get("sql") or "").strip()
                    if not connection_id or not sql:
                        continue
                    if connection_id not in selected_lookup:
                        continue
                    if connection_id in seen_connections:
                        warnings.append(
                            "The workspace planner returned multiple queries for one database, so only the first query was used."
                        )
                        continue
                    seen_connections.add(connection_id)
                    validated_query_plans.append(
                        {
                            "connection_id": connection_id,
                            "sql": sql,
                        }
                    )

                if not validated_query_plans and not spreadsheet_runs:
                    response_payload["execution_status"] = "answered"
                    response_payload["ai_explanation"] = (
                        planned_response
                        or "I could not form a precise query, so I checked the selected source metadata instead. Try naming the metric, segment, or time window for a deeper answer."
                    )
                    terminal_response_ready = True
                else:
                    response_payload["targeted_connection_ids"] = [
                        item["connection_id"] for item in validated_query_plans
                    ]

                    workspace_context_lookup = {
                        connection_context["id"]: connection_context
                        for connection_context in workspace_connections
                    }

                    for query_plan in validated_query_plans:
                        target_context = workspace_context_lookup[query_plan["connection_id"]]
                        if not target_context["has_schema"]:
                            response_payload["execution_status"] = "clarify"
                            response_payload["ai_explanation"] = (
                                f"I need the latest schema for {target_context['name']} before I can query it in the workspace. "
                                "Please sync that database first."
                            )
                            return WorkspaceChatMessageResponse.model_validate(response_payload)

                        safety_check = await query_execution_service.validate_and_prepare(
                            query_plan["sql"]
                        )
                        query_plan["safety_check"] = safety_check
                        if not safety_check["is_safe"]:
                            response_payload["execution_status"] = "error"
                            response_payload["ai_explanation"] = (
                                f"I couldn't safely run the workspace query for {target_context['name']}. "
                                f"{safety_check.get('error') or 'Please rephrase the question.'}"
                            )
                            return WorkspaceChatMessageResponse.model_validate(response_payload)

                        if safety_check["is_destructive"]:
                            response_payload["execution_status"] = "clarify"
                            response_payload["ai_explanation"] = (
                                "Workspace chat is designed for read queries across sources. "
                                "For write operations, switch to the single-database chat for the specific source."
                            )
                            return WorkspaceChatMessageResponse.model_validate(response_payload)

                    for query_plan in validated_query_plans:
                        connection = selected_lookup[query_plan["connection_id"]]
                        username, password = db_connection_service.decrypt_credentials(connection)
                        execution_result = await query_execution_service.execute_query(
                            connection=connection,
                            query=query_plan["sql"],
                            username_decrypted=username,
                            password_decrypted=password,
                            user_id=current_user_id,
                            db=db,
                            safety_check=query_plan.get("safety_check"),
                        )

                        generated_query = {
                            "source_id": connection.id,
                            "source_type": "database",
                            "connection_id": connection.id,
                            "connection_name": connection.name,
                            "database_name": connection.database_name,
                            "sql": query_plan["sql"],
                            "status": "executed" if execution_result.get("success") else "error",
                            "row_count": execution_result.get("row_count"),
                            "error": execution_result.get("error"),
                        }
                        response_payload["generated_queries"].append(generated_query)

                        if execution_result.get("success"):
                            any_success = True
                            rows = _sanitize_result_payload(execution_result.get("result", []) or [])
                            row_count = execution_result.get("row_count", 0) or 0
                            truncated = bool(execution_result.get("truncated"))
                            query_run = {
                                "source_id": connection.id,
                                "source_type": "database",
                                "connection_id": connection.id,
                                "connection_name": connection.name,
                                "database_name": connection.database_name,
                                "sql": query_plan["sql"],
                                "row_count": row_count,
                                "truncated": truncated,
                                "rows": rows,
                                "error": None,
                            }
                        else:
                            query_run = {
                                "source_id": connection.id,
                                "source_type": "database",
                                "connection_id": connection.id,
                                "connection_name": connection.name,
                                "database_name": connection.database_name,
                                "sql": query_plan["sql"],
                                "row_count": 0,
                                "truncated": False,
                                "rows": [],
                                "error": execution_result.get("error"),
                            }

                        response_payload["query_results"].append(query_run)
                        query_runs.append(query_run)

        for spreadsheet_run in spreadsheet_runs:
            any_success = True
            generated_summary = {
                "source_id": spreadsheet_run["source_id"],
                "source_type": "spreadsheet",
                "connection_id": spreadsheet_run["connection_id"],
                "connection_name": spreadsheet_run["connection_name"],
                "database_name": spreadsheet_run["database_name"],
                "sql": spreadsheet_run.get("sql") or "SPREADSHEET_ANALYSIS",
                "status": "executed",
                "row_count": spreadsheet_run["row_count"],
                "error": None,
            }
            response_payload["generated_queries"].append(generated_summary)
            response_payload["query_results"].append(spreadsheet_run)
            query_runs.append(spreadsheet_run)

        response_payload["targeted_source_ids"] = [
            run.get("source_id") or run.get("connection_id")
            for run in query_runs
            if run.get("source_id") or run.get("connection_id")
        ]
        if not response_payload["targeted_connection_ids"]:
            response_payload["targeted_connection_ids"] = [
                run["connection_id"]
                for run in query_runs
                if run.get("source_type", "database") == "database"
            ]

        if terminal_response_ready:
            pass
        elif any_success:
            response_payload["execution_status"] = "executed"
            if query_runs:
                assistant_response = await sql_generation_service.generate_workspace_result_response(
                    user_query=data.user_prompt,
                    query_runs=query_runs,
                )
                add_usage(assistant_response.get("usage"))
                response_payload["ai_explanation"] = (
                    assistant_response.get("response")
                    or (
                        " ".join(run.get("response", "") for run in spreadsheet_runs).strip()
                        if all(run.get("source_type") == "spreadsheet" for run in query_runs)
                        else ""
                    )
                    or _fallback_workspace_query_response(query_runs)
                )
            else:
                response_payload["ai_explanation"] = _fallback_workspace_query_response(query_runs)
        else:
            response_payload["execution_status"] = "error"
            response_payload["ai_explanation"] = _fallback_workspace_query_response(
                query_runs
            )

        if reserved_tokens > 0:
            total_tokens = token_usage["prompt_tokens"] + token_usage["completion_tokens"]
            if total_tokens > 0:
                await token_usage_service.finalize_tokens(
                    user_id=current_user_id,
                    reserved_tokens=reserved_tokens,
                    prompt_tokens=token_usage["prompt_tokens"],
                    completion_tokens=token_usage["completion_tokens"],
                    db=db,
                    request_kind="workspace_chat",
                )
            else:
                await token_usage_service.release_tokens(
                    user_id=current_user_id,
                    reserved_tokens=reserved_tokens,
                    db=db,
                )
            reserved_tokens = 0

        _log_workspace_event(
            action="workspace.message_processed",
            status="success" if response_payload["execution_status"] != "error" else "error",
            user=current_user,
            details={
                "execution_status": response_payload["execution_status"],
                "active_connection_id": data.active_connection_id,
                "active_source_id": active_source_id,
                "targeted_connection_ids": response_payload["targeted_connection_ids"],
                "targeted_source_ids": response_payload["targeted_source_ids"],
                "query_count": len(response_payload["generated_queries"]),
                "warning_count": len(response_payload["warnings"]),
                "prompt_preview": activity_service.preview_text(data.user_prompt),
                "response_preview": activity_service.preview_text(response_payload["ai_explanation"]),
                "prompt_tokens": token_usage["prompt_tokens"],
                "completion_tokens": token_usage["completion_tokens"],
            },
        )

        return WorkspaceChatMessageResponse.model_validate(response_payload)
    except HTTPException:
        if reserved_tokens > 0:
            await _release_reserved_tokens_safely(
                user_id=current_user_id,
                reserved_tokens=reserved_tokens,
                db=db,
            )
        raise
    except TokenLimitExceededError as exc:
        logger.info("Workspace token limit reached for user %s: %s", current_user_id, exc)
        response_payload["execution_status"] = "error"
        response_payload["ai_explanation"] = str(exc)
        _log_workspace_event(
            action="workspace.message_processed",
            status="warning",
            user=current_user,
            details={
                "execution_status": response_payload["execution_status"],
                "active_connection_id": data.active_connection_id,
                "active_source_id": active_source_id,
                "prompt_preview": activity_service.preview_text(data.user_prompt),
                "error": str(exc),
            },
        )
        return WorkspaceChatMessageResponse.model_validate(response_payload)
    except Exception as exc:
        logger.error("Error processing workspace message: %s", exc, exc_info=True)
        if reserved_tokens > 0:
            await _release_reserved_tokens_safely(
                user_id=current_user_id,
                reserved_tokens=reserved_tokens,
                db=db,
            )
        response_payload["execution_status"] = "error"
        response_payload["ai_explanation"] = _chat_error_message(exc)
        _log_workspace_event(
            action="workspace.message_processed",
            status="error",
            user=current_user,
            details={
                "execution_status": response_payload["execution_status"],
                "active_connection_id": data.active_connection_id,
                "active_source_id": active_source_id,
                "prompt_preview": activity_service.preview_text(data.user_prompt),
                **activity_service.exception_details(exc),
            },
        )
        return WorkspaceChatMessageResponse.model_validate(response_payload)


@router.post("/validate-query", response_model=QueryValidationResponse)
async def validate_query(
    request: ExecuteQueryRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Validate a SQL query for safety."""
    safety_check = await query_execution_service.validate_and_prepare(request.query_text)
    activity_service.log_event(
        action="query.validated",
        status="success" if safety_check["is_safe"] else "warning",
        user=current_user,
        resource_type="database_connection",
        resource_id=request.connection_id,
        details={
            "query_type": safety_check["query_type"],
            "is_safe": safety_check["is_safe"],
            "is_destructive": safety_check["is_destructive"],
            "warnings": safety_check["warnings"],
            "error": safety_check["error"],
            "query_preview": activity_service.preview_sql(request.query_text),
        },
    )

    return QueryValidationResponse(
        is_safe=safety_check["is_safe"],
        is_destructive=safety_check["is_destructive"],
        query_type=safety_check["query_type"],
        warnings=safety_check["warnings"],
        error=safety_check["error"],
    )


@router.post("/confirm-query", response_model=ExecuteQueryResponse)
async def confirm_and_execute_query(
    request: QueryConfirmationRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Confirm and execute a destructive query."""
    message = None
    if request.message_id:
        message = await _load_message_for_user(request.message_id, current_user.id, db)

    try:
        confirmed_query = await query_execution_service.confirm_query(
            confirmation_token=request.confirmation_token,
            user_id=current_user.id,
            connection_id=request.connection_id,
            db=db,
        )

        if not confirmed_query:
            if message:
                message.requires_confirmation = False
                message.execution_status = "error"
                message.ai_explanation = "This confirmation token is invalid or has expired. Please generate a new request."
                await db.commit()
            activity_service.log_event(
                action="query.confirmation_execute",
                status="warning",
                user=current_user,
                resource_type="database_connection",
                resource_id=request.connection_id,
                details={
                    "message_id": request.message_id,
                    "reason": "invalid_or_expired_token",
                },
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired confirmation token",
            )

        connection = await db_connection_service.get_connection(
            request.connection_id,
            db,
        )
        if not connection or connection.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Database connection not found",
            )

        username, password = db_connection_service.decrypt_credentials(connection)
        execution_result = await query_execution_service.execute_query(
            connection=connection,
            query=confirmed_query,
            username_decrypted=username,
            password_decrypted=password,
            user_id=current_user.id,
            db=db,
        )

        if message:
            message.requires_confirmation = False
            if execution_result.get("success"):
                result_payload = execution_result.get("result")
                message.query_result = _json_safe(
                    {
                        "result": result_payload,
                        "row_count": execution_result.get("row_count", 0),
                    }
                )
                message.execution_status = "executed"
                message.ai_explanation = _mutation_success_message(
                    execution_result.get("row_count")
                )
            else:
                message.execution_status = "error"
                message.ai_explanation = execution_result.get("error")

            await db.commit()

        activity_service.log_event(
            action="query.confirmation_execute",
            status="success" if execution_result.get("success") else "error",
            user=current_user,
            resource_type="database_connection",
            resource_id=request.connection_id,
            details={
                "message_id": request.message_id,
                "rows_affected": execution_result.get("row_count"),
                "execution_time_ms": execution_result.get("execution_time_ms"),
                "error": execution_result.get("error"),
                "query_preview": activity_service.preview_sql(confirmed_query),
            },
        )

        return ExecuteQueryResponse(
            success=execution_result.get("success", False),
            rows_affected=execution_result.get("row_count"),
            result=_json_safe(execution_result.get("result")),
            error=execution_result.get("error"),
            execution_time_ms=execution_result.get("execution_time_ms"),
            requires_confirmation=False,
            confirmation_token=None,
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error confirming query: %s", exc, exc_info=True)
        if message:
            await _safe_rollback(db)
            message.query_result = None
            message.requires_confirmation = False
            message.execution_status = "error"
            message.ai_explanation = _chat_error_message(exc)
            try:
                db.add(message)
                await db.commit()
            except Exception:
                logger.error("Failed to persist confirm-query error message", exc_info=True)
        activity_service.log_event(
            action="query.confirmation_execute",
            status="error",
            user=current_user,
            resource_type="database_connection",
            resource_id=request.connection_id,
            details={
                "message_id": request.message_id,
                **activity_service.exception_details(exc),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_chat_error_message(exc),
        )


@router.get("/query-logs", response_model=List[QueryLogResponse])
async def get_query_logs(
    connection_id: str = None,
    limit: int = 50,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Get query logs for current user."""
    from app.models import QueryLog

    query = select(QueryLog).where(
        QueryLog.user_id == current_user.id
    ).order_by(QueryLog.executed_at.desc()).limit(limit)

    if connection_id:
        query = query.where(QueryLog.connection_id == connection_id)

    result = await db.execute(query)
    logs = result.scalars().all()

    return [QueryLogResponse.model_validate(log) for log in logs]


@router.get("/query-logs/paginated", response_model=QueryLogPageResponse)
async def get_paginated_query_logs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=10),
    connection_id: str | None = None,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Get paginated query logs for the current user."""
    from app.models import QueryLog

    filters = [QueryLog.user_id == current_user.id]
    if connection_id:
        filters.append(QueryLog.connection_id == connection_id)

    total_result = await db.execute(
        select(func.count()).select_from(QueryLog).where(*filters)
    )
    total_items = total_result.scalar_one() or 0

    offset = (page - 1) * page_size
    total_pages = (total_items + page_size - 1) // page_size if total_items else 1

    query = (
        select(QueryLog)
        .where(*filters)
        .order_by(QueryLog.executed_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    result = await db.execute(query)
    logs = result.scalars().all()

    return QueryLogPageResponse(
        items=[QueryLogResponse.model_validate(log) for log in logs],
        page=page,
        page_size=page_size,
        total_items=total_items,
        total_pages=total_pages,
    )


@router.get("/query-stats", response_model=QueryStatsResponse)
async def get_query_stats(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Get aggregated query success stats for the current user."""
    from app.models import QueryLog

    total_result = await db.execute(
        select(func.count()).select_from(QueryLog).where(QueryLog.user_id == current_user.id)
    )
    success_result = await db.execute(
        select(func.count()).select_from(QueryLog).where(
            QueryLog.user_id == current_user.id,
            QueryLog.status == "success",
        )
    )

    total_queries = total_result.scalar_one() or 0
    successful_queries = success_result.scalar_one() or 0
    failed_queries = max(total_queries - successful_queries, 0)
    success_rate = round((successful_queries / total_queries) * 100) if total_queries else 0

    return QueryStatsResponse(
        total_queries=total_queries,
        successful_queries=successful_queries,
        failed_queries=failed_queries,
        success_rate=success_rate,
    )
