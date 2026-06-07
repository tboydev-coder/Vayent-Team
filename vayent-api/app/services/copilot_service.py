"""Advanced copilot workflows for investigations, briefings, and watchlists."""
from __future__ import annotations

import json
import logging
import numbers
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import CopilotArtifact, CopilotMemory, CopilotWatchlist, DatabaseConnection
from app.services.db_connection_service import db_connection_service
from app.services.error_message_service import error_message_service
from app.services.openai_config_service import (
    build_async_openai_client,
    build_chat_completion_controls,
    require_openai_api_key,
    require_openai_reachable,
)
from app.services.query_execution_service import query_execution_service
from app.services.schema_discovery_service import schema_discovery_service
from app.services.spreadsheet_service import spreadsheet_service

logger = logging.getLogger(__name__)


class InvestigationStepPlan(BaseModel):
    """A single diagnostic step in an investigation."""

    question: str
    rationale: str
    sql: str


class InvestigationPlanResult(BaseModel):
    """Structured multi-step investigation plan."""

    title: str
    steps: list[InvestigationStepPlan] = Field(
        default_factory=list, max_length=4)


class InsightSynthesisResult(BaseModel):
    """Structured business insight output."""

    title: str
    summary: str
    findings: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ScenarioAnalysisResult(BaseModel):
    """Structured scenario-mode output."""

    title: str
    summary: str
    assumptions: list[str] = Field(default_factory=list)
    upside: list[str] = Field(default_factory=list)
    downside: list[str] = Field(default_factory=list)
    watch_items: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class DashboardCardPlan(BaseModel):
    """A saved dashboard card definition."""

    title: str
    description: str
    sql: str
    visualization: str = "auto"
    explanation: str = ""
    interpretation: str = ""
    recommended_action: str = ""


class DashboardPlanResult(BaseModel):
    """Structured dashboard output."""

    title: str
    description: str
    cards: list[DashboardCardPlan] = Field(default_factory=list, max_length=6)


class WatchlistPlanResult(BaseModel):
    """Structured watchlist definition."""

    title: str
    description: str
    sql: str
    note: str = ""


@dataclass
class ConnectionContext:
    """Loaded database context for copilot operations."""

    connection: DatabaseConnection
    schema_context: str
    has_schema: bool


class CopilotService:
    """Orchestrate advanced AI workflows grounded in app context and database data."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.client = build_async_openai_client(self.settings)

    def _get_client(self) -> AsyncOpenAI:
        require_openai_api_key(self.settings, logger)
        require_openai_reachable(self.settings, logger)
        if self.client is None:
            # Recreate the client in case settings changed and to avoid ambient env var overrides.
            self.client = build_async_openai_client(self.settings)
            if self.client is None:
                raise RuntimeError("OPENAI_API_KEY is not configured.")
        return self.client

    @staticmethod
    def _usage_payload(response) -> dict[str, int]:
        usage = getattr(response, "usage", None)
        return {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
            "total_tokens": getattr(usage, "total_tokens", 0) or 0,
        }

    @staticmethod
    def _merge_usage(*payloads: dict[str, int] | None) -> dict[str, int]:
        merged = {"prompt_tokens": 0,
                  "completion_tokens": 0, "total_tokens": 0}
        for payload in payloads:
            if not payload:
                continue
            merged["prompt_tokens"] += payload.get("prompt_tokens", 0) or 0
            merged["completion_tokens"] += payload.get(
                "completion_tokens", 0) or 0
            merged["total_tokens"] += payload.get("total_tokens", 0) or 0
        return merged

    @staticmethod
    def _json_safe(value: Any) -> Any:
        """Convert nested values into JSON-safe structures."""
        try:
            return json.loads(json.dumps(value, default=str))
        except Exception:
            return str(value)

    @staticmethod
    def _preview_rows(rows: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
        return [CopilotService._json_safe(row) for row in (rows or [])[:limit]]

    async def _complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model,
        max_tokens: int = 520,
        # temperature: float = 0.1,
    ) -> tuple[Any, dict[str, int]]:
        """Run an OpenAI JSON completion and validate the payload."""
        try:
            client = self._get_client()
            retry_system_prompt = (
                f"{system_prompt}\n\n"
                "Return a complete JSON object that satisfies the response schema. "
                "Do not return an empty object and do not omit required fields. "
                "Be concise: keep string fields short and avoid filler."
            )
            usage_payload: dict[str, int] | None = None

            for attempt, active_system_prompt in enumerate(
                (system_prompt, retry_system_prompt),
                start=1,
            ):
                try:
                    # The SDK parse() call will fail if the model hits the length cap.
                    # On retry, give it more room and reduce reasoning so it reliably emits JSON.
                    max_completion_tokens = (
                        max_tokens if attempt == 1 else min(max_tokens * 3, 2400)
                    )
                    # For strict structured output, prioritize emitting the JSON over reasoning traces.
                    reasoning_effort = "none"
                    response = await client.beta.chat.completions.parse(
                        model=self.settings.openai_model,
                        messages=[
                            {"role": "system", "content": active_system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        # temperature=temperature,
                        response_format=response_model,
                        **build_chat_completion_controls(
                            self.settings.openai_model,
                            max_completion_tokens,
                            reasoning_effort=reasoning_effort,
                        ),
                    )
                    usage_payload = self._merge_usage(
                        usage_payload,
                        self._usage_payload(response),
                    )
                    message = response.choices[0].message
                    if getattr(message, "refusal", None):
                        raise RuntimeError(message.refusal)

                    parsed = getattr(message, "parsed", None)
                    if parsed is None:
                        raise ValueError(
                            f"{response_model.__name__} response was empty or incomplete."
                        )
                    return parsed, usage_payload
                except Exception as exc:
                    if attempt == 1:
                        logger.warning(
                            "Copilot structured output retry for %s after: %s",
                            response_model.__name__,
                            exc,
                        )
                        continue
                    raise
        except Exception as exc:
            logger.error("Copilot completion error: %s", exc)
            raise RuntimeError(
                error_message_service.ai_failed_message(str(exc))) from exc

    async def _load_connection_context(
        self,
        connection_id: str,
        user_id: str,
        db: AsyncSession,
    ) -> ConnectionContext:
        result = await db.execute(
            select(DatabaseConnection).where(
                DatabaseConnection.id == connection_id,
                DatabaseConnection.user_id == user_id,
            )
        )
        connection = result.scalar_one_or_none()
        if not connection:
            raise ValueError("Database connection not found")

        schema = await schema_discovery_service.get_connection_schema(connection_id, db)
        schema_context = (
            await schema_discovery_service.get_schema_for_rag(schema.id, db)
            if schema
            else "No synced schema is currently available for this connection."
        )
        return ConnectionContext(
            connection=connection,
            schema_context=schema_context,
            has_schema=bool(schema),
        )

    async def _load_business_context(
        self,
        user_id: str,
        connection_id: str | None,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Collect memories, watchlists, and recent artifacts for prompts."""
        artifacts_stmt = (
            select(CopilotArtifact)
            .where(CopilotArtifact.user_id == user_id)
            .order_by(CopilotArtifact.created_at.desc())
            .limit(8)
        )
        memories_stmt = (
            select(CopilotMemory)
            .where(CopilotMemory.user_id == user_id)
            .order_by(CopilotMemory.updated_at.desc())
            .limit(8)
        )
        watchlists_stmt = (
            select(CopilotWatchlist)
            .where(CopilotWatchlist.user_id == user_id)
            .order_by(CopilotWatchlist.updated_at.desc())
            .limit(8)
        )

        if connection_id:
            artifacts_stmt = artifacts_stmt.where(
                (CopilotArtifact.connection_id == connection_id)
                | (CopilotArtifact.connection_id.is_(None))
            )
            memories_stmt = memories_stmt.where(
                (CopilotMemory.connection_id == connection_id)
                | (CopilotMemory.connection_id.is_(None))
            )
            watchlists_stmt = watchlists_stmt.where(
                (CopilotWatchlist.connection_id == connection_id)
                | (CopilotWatchlist.connection_id.is_(None))
            )

        artifacts = (await db.execute(artifacts_stmt)).scalars().all()
        memories = (await db.execute(memories_stmt)).scalars().all()
        watchlists = (await db.execute(watchlists_stmt)).scalars().all()

        return {
            "artifacts": [
                {
                    "type": item.artifact_type,
                    "title": item.title,
                    "summary": item.summary,
                    "created_at": item.created_at.isoformat(),
                    "payload": self._json_safe(item.payload),
                }
                for item in artifacts
            ],
            "memories": [
                {
                    "title": item.title,
                    "category": item.category,
                    "content": item.content,
                    "updated_at": item.updated_at.isoformat() if item.updated_at else None,
                }
                for item in memories
            ],
            "watchlists": [
                {
                    "title": item.title,
                    "description": item.description,
                    "status": item.last_status,
                    "value": item.last_value,
                    "threshold": item.threshold_value,
                    "comparator": item.comparator,
                    "summary": item.last_summary,
                }
                for item in watchlists
            ],
        }

    async def _execute_readonly_query(
        self,
        *,
        context: ConnectionContext,
        sql: str,
        user_id: str,
        db: AsyncSession,
        label: str,
    ) -> dict[str, Any]:
        """Execute a safe read-only query and return summarized evidence."""
        safety = await query_execution_service.validate_and_prepare(sql)
        if not safety["is_safe"]:
            raise ValueError(safety.get("error")
                             or "Query failed safety validation")
        if safety["is_destructive"] or safety["query_type"].upper() != "SELECT":
            raise ValueError(
                "Copilot evidence queries must be read-only SELECT statements")

        username, password = db_connection_service.decrypt_credentials(
            context.connection)
        execution = await query_execution_service.execute_query(
            connection=context.connection,
            query=sql,
            username_decrypted=username,
            password_decrypted=password,
            user_id=user_id,
            db=db,
            safety_check=safety,
        )
        if not execution.get("success"):
            raise RuntimeError(execution.get("error")
                               or "Evidence query failed")

        rows = execution.get("result", []) or []
        row_count = execution.get("row_count", 0) or 0
        truncated = bool(execution.get("truncated"))
        return {
            "label": label,
            "sql": sql,
            "row_count": row_count,
            "truncated": truncated,
            "rows": self._preview_rows(rows),
        }

    @staticmethod
    def _extract_numeric_value(rows: list[dict[str, Any]]) -> float | None:
        """Get a numeric value from the first row for watchlist evaluation."""
        if not rows:
            return None

        first_row = rows[0]
        if not isinstance(first_row, dict):
            return None

        for value in first_row.values():
            if isinstance(value, bool):
                continue
            if isinstance(value, numbers.Number):
                return float(value)
            if isinstance(value, str):
                normalized = value.replace(",", "").strip()
                try:
                    return float(normalized)
                except ValueError:
                    continue
        return None

    @staticmethod
    def _compare(value: float, comparator: str, threshold: float) -> bool:
        if comparator == "gt":
            return value > threshold
        if comparator == "gte":
            return value >= threshold
        if comparator == "lt":
            return value < threshold
        if comparator == "lte":
            return value <= threshold
        return False

    async def _store_artifact(
        self,
        *,
        user_id: str,
        connection_id: str | None,
        session_id: str | None,
        artifact_type: str,
        title: str,
        prompt: str | None,
        summary: str | None,
        payload: dict[str, Any],
        db: AsyncSession,
        source_id: str | None = None,
        source_type: str | None = None,
    ) -> CopilotArtifact:
        artifact = CopilotArtifact(
            id=str(uuid.uuid4()),
            user_id=user_id,
            connection_id=connection_id,
            source_id=source_id,
            source_type=source_type,
            session_id=session_id,
            artifact_type=artifact_type,
            title=title,
            prompt=prompt,
            summary=summary,
            payload=self._json_safe(payload) or {},
        )
        db.add(artifact)
        await db.commit()
        await db.refresh(artifact)
        return artifact

    async def create_memory(
        self,
        *,
        user_id: str,
        connection_id: str | None,
        title: str,
        category: str,
        content: str,
        db: AsyncSession,
    ) -> CopilotMemory:
        memory = CopilotMemory(
            id=str(uuid.uuid4()),
            user_id=user_id,
            connection_id=connection_id,
            title=title.strip(),
            category=category.strip().lower(),
            content=content.strip(),
        )
        db.add(memory)
        await db.commit()
        await db.refresh(memory)
        return memory

    async def list_memories(
        self,
        *,
        user_id: str,
        connection_id: str | None,
        db: AsyncSession,
    ) -> list[CopilotMemory]:
        stmt = (
            select(CopilotMemory)
            .where(CopilotMemory.user_id == user_id)
            .order_by(CopilotMemory.updated_at.desc(), CopilotMemory.created_at.desc())
        )
        if connection_id:
            stmt = stmt.where(
                (CopilotMemory.connection_id == connection_id)
                | (CopilotMemory.connection_id.is_(None))
            )
        return list((await db.execute(stmt)).scalars().all())

    async def delete_memory(
        self,
        *,
        memory_id: str,
        user_id: str,
        db: AsyncSession,
    ) -> bool:
        stmt = select(CopilotMemory).where(
            CopilotMemory.id == memory_id,
            CopilotMemory.user_id == user_id,
        )
        memory = (await db.execute(stmt)).scalar_one_or_none()
        if not memory:
            return False
        await db.delete(memory)
        await db.commit()
        return True

    async def list_artifacts(
        self,
        *,
        user_id: str,
        connection_id: str | None,
        artifact_type: str | None,
        db: AsyncSession,
        limit: int = 24,
    ) -> list[CopilotArtifact]:
        stmt = (
            select(CopilotArtifact)
            .where(CopilotArtifact.user_id == user_id)
            .order_by(CopilotArtifact.created_at.desc())
            .limit(limit)
        )
        if connection_id:
            stmt = stmt.where(
                or_(
                    CopilotArtifact.connection_id == connection_id,
                    CopilotArtifact.source_id == connection_id,
                    (
                        CopilotArtifact.connection_id.is_(None)
                        & CopilotArtifact.source_id.is_(None)
                    ),
                )
            )
        if artifact_type:
            stmt = stmt.where(CopilotArtifact.artifact_type == artifact_type)
        return list((await db.execute(stmt)).scalars().all())

    async def get_artifact(
        self,
        *,
        artifact_id: str,
        user_id: str,
        db: AsyncSession,
    ) -> CopilotArtifact | None:
        stmt = select(CopilotArtifact).where(
            CopilotArtifact.id == artifact_id,
            CopilotArtifact.user_id == user_id,
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    async def update_artifact(
        self,
        *,
        artifact_id: str,
        user_id: str,
        title: str | None,
        summary: str | None,
        workspace: str | None,
        project: str | None,
        db: AsyncSession,
    ) -> CopilotArtifact | None:
        artifact = await self.get_artifact(
            artifact_id=artifact_id,
            user_id=user_id,
            db=db,
        )
        if not artifact:
            return None

        if title is not None:
            artifact.title = title.strip()
        if summary is not None:
            artifact.summary = summary.strip() or None

        payload = dict(artifact.payload or {})
        if workspace is not None:
            payload["workspace"] = workspace.strip() or "Default"
        if project is not None:
            payload["project"] = project.strip() or "General"

        artifact.payload = self._json_safe(payload) or {}
        artifact.updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(artifact)
        return artifact

    async def duplicate_artifact(
        self,
        *,
        artifact_id: str,
        user_id: str,
        db: AsyncSession,
    ) -> CopilotArtifact | None:
        artifact = await self.get_artifact(
            artifact_id=artifact_id,
            user_id=user_id,
            db=db,
        )
        if not artifact:
            return None

        payload = self._json_safe(artifact.payload or {}) or {}
        payload["duplicated_from"] = artifact.id
        copy = CopilotArtifact(
            id=str(uuid.uuid4()),
            user_id=user_id,
            connection_id=artifact.connection_id,
            source_id=artifact.source_id,
            source_type=artifact.source_type,
            session_id=artifact.session_id,
            artifact_type=artifact.artifact_type,
            title=f"{artifact.title} copy",
            prompt=artifact.prompt,
            summary=artifact.summary,
            payload=payload,
        )
        db.add(copy)
        await db.commit()
        await db.refresh(copy)
        return copy

    async def delete_artifact(
        self,
        *,
        artifact_id: str,
        user_id: str,
        db: AsyncSession,
    ) -> bool:
        artifact = await self.get_artifact(
            artifact_id=artifact_id,
            user_id=user_id,
            db=db,
        )
        if not artifact:
            return False

        await db.delete(artifact)
        await db.commit()
        return True

    async def list_watchlists(
        self,
        *,
        user_id: str,
        connection_id: str | None,
        db: AsyncSession,
    ) -> list[CopilotWatchlist]:
        stmt = (
            select(CopilotWatchlist)
            .where(CopilotWatchlist.user_id == user_id)
            .order_by(CopilotWatchlist.updated_at.desc(), CopilotWatchlist.created_at.desc())
        )
        if connection_id:
            stmt = stmt.where(
                (CopilotWatchlist.connection_id == connection_id)
                | (CopilotWatchlist.connection_id.is_(None))
            )
        return list((await db.execute(stmt)).scalars().all())

    async def get_overview(
        self,
        *,
        user_id: str,
        connection_id: str | None,
        db: AsyncSession,
    ) -> dict[str, Any]:
        recent_artifacts = await self.list_artifacts(
            user_id=user_id,
            connection_id=connection_id,
            artifact_type=None,
            db=db,
            limit=8,
        )
        memories = await self.list_memories(user_id=user_id, connection_id=connection_id, db=db)
        watchlists = await self.list_watchlists(
            user_id=user_id,
            connection_id=connection_id,
            db=db,
        )
        alerts = [item for item in watchlists if item.last_status == "alert"]
        return {
            "recent_artifacts": recent_artifacts,
            "memories": memories,
            "watchlists": watchlists,
            "alerts": alerts,
        }

    async def generate_investigation(
        self,
        *,
        user_id: str,
        connection_id: str,
        prompt: str,
        session_id: str | None,
        db: AsyncSession,
    ) -> tuple[CopilotArtifact, dict[str, int]]:
        context = await self._load_connection_context(connection_id, user_id, db)
        if not context.has_schema:
            raise ValueError(
                "Sync the schema before running an investigation.")

        planner_system = f"""You are Vayent, an investigative business analyst.

Convert the business question into a compact multi-step investigation plan.

Rules:
- Use only the provided schema.
- Return 2 to 4 read-only SELECT queries.
- Each query must answer a distinct diagnostic question.
- Prefer aggregates, trends, segments, comparisons, and rankings.
- Never use INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, CREATE, or comments.
- Keep SQL compatible with {context.connection.db_type.value}.

Available schema:
{context.schema_context}

Return JSON:
{{
  "title": "short investigation title",
  "steps": [
    {{
      "question": "what this step checks",
      "rationale": "why this matters",
      "sql": "SELECT ..."
    }}
  ]
}}"""
        plan, plan_usage = await self._complete_json(
            system_prompt=planner_system,
            user_prompt=prompt,
            response_model=InvestigationPlanResult,
            max_tokens=420,
        )

        evidence_queries = []
        for step in plan.steps[:4]:
            try:
                evidence_queries.append(
                    {
                        "question": step.question,
                        "rationale": step.rationale,
                        "status": "success",
                        **await self._execute_readonly_query(
                            context=context,
                            sql=step.sql,
                            user_id=user_id,
                            db=db,
                            label=step.question,
                        ),
                    }
                )
            except Exception as exc:
                evidence_queries.append(
                    {
                        "question": step.question,
                        "rationale": step.rationale,
                        "sql": step.sql,
                        "status": "error",
                        "error": str(exc),
                    }
                )

        business_context = await self._load_business_context(user_id, connection_id, db)
        synthesis_system = """You are Vayent, a CEO copilot.

Turn the investigation evidence into a sharp executive answer.

Rules:
- Use the evidence provided. Do not invent facts.
- You may make limited business inferences, but keep them clearly framed as guidance.
- Mention uncertainty when evidence is weak or partial.
- Prioritize concrete next steps.

Return JSON:
{
  "title": "short title",
  "summary": "concise executive answer",
  "findings": ["fact or supported observation"],
  "recommendations": ["actionable next step"],
  "risks": ["key risk"],
  "opportunities": ["key opportunity"],
  "confidence": 0.0
}"""
        synthesis_prompt = json.dumps(
            {
                "user_prompt": prompt,
                "investigation_title": plan.title,
                "business_context": business_context,
                "evidence_queries": evidence_queries,
            },
            default=str,
        )
        synthesis, synthesis_usage = await self._complete_json(
            system_prompt=synthesis_system,
            user_prompt=synthesis_prompt,
            response_model=InsightSynthesisResult,
            max_tokens=520,
        )

        artifact = await self._store_artifact(
            user_id=user_id,
            connection_id=connection_id,
            session_id=session_id,
            artifact_type="investigation",
            title=synthesis.title or plan.title,
            prompt=prompt,
            summary=synthesis.summary,
            payload={
                "plan": self._json_safe(plan.model_dump()),
                "evidence_queries": evidence_queries,
                "findings": synthesis.findings,
                "recommendations": synthesis.recommendations,
                "risks": synthesis.risks,
                "opportunities": synthesis.opportunities,
                "confidence": synthesis.confidence,
            },
            db=db,
        )
        return artifact, self._merge_usage(plan_usage, synthesis_usage)

    async def generate_briefing(
        self,
        *,
        user_id: str,
        connection_id: str,
        prompt: str,
        db: AsyncSession,
    ) -> tuple[CopilotArtifact, dict[str, int]]:
        context = await self._load_connection_context(connection_id, user_id, db)
        business_context = await self._load_business_context(user_id, connection_id, db)
        system_prompt = """You are Vayent, an executive briefing assistant.

Create a concise business briefing using the provided memories, watchlists, recent artifacts, and schema context.

Rules:
- Be useful even if the evidence is incomplete.
- Separate observed signals from recommendations.
- Focus on what changed, what matters, and what needs action.

Return JSON:
{
  "title": "briefing title",
  "summary": "executive summary",
  "findings": ["what changed"],
  "recommendations": ["what to do next"],
  "risks": ["risk to watch"],
  "opportunities": ["opportunity"],
  "confidence": 0.0
}"""
        briefing_prompt = json.dumps(
            {
                "user_prompt": prompt,
                "schema_context": context.schema_context,
                "business_context": business_context,
            },
            default=str,
        )
        briefing, usage = await self._complete_json(
            system_prompt=system_prompt,
            user_prompt=briefing_prompt,
            response_model=InsightSynthesisResult,
            max_tokens=420,
        )

        artifact = await self._store_artifact(
            user_id=user_id,
            connection_id=connection_id,
            session_id=None,
            artifact_type="briefing",
            title=briefing.title,
            prompt=prompt,
            summary=briefing.summary,
            payload={
                "findings": briefing.findings,
                "recommendations": briefing.recommendations,
                "risks": briefing.risks,
                "opportunities": briefing.opportunities,
                "confidence": briefing.confidence,
                "business_context_snapshot": business_context,
            },
            db=db,
        )
        return artifact, usage

    async def generate_recommendations(
        self,
        *,
        user_id: str,
        connection_id: str,
        prompt: str,
        session_id: str | None,
        db: AsyncSession,
    ) -> tuple[CopilotArtifact, dict[str, int]]:
        context = await self._load_connection_context(connection_id, user_id, db)
        business_context = await self._load_business_context(user_id, connection_id, db)
        system_prompt = """You are Vayent, a recommendation engine for founders and operators.

Provide prioritized, evidence-aware recommendations.

Rules:
- Recommendations must be practical and specific.
- Explain the operating logic in the findings or risks.
- Use the available business context and schema context. Do not invent live numbers.

Return JSON:
{
  "title": "recommendation title",
  "summary": "short headline summary",
  "findings": ["why this matters"],
  "recommendations": ["priority action"],
  "risks": ["risk if ignored"],
  "opportunities": ["benefit if done"],
  "confidence": 0.0
}"""
        recommendation_prompt = json.dumps(
            {
                "user_prompt": prompt,
                "schema_context": context.schema_context,
                "business_context": business_context,
            },
            default=str,
        )
        recommendation, usage = await self._complete_json(
            system_prompt=system_prompt,
            user_prompt=recommendation_prompt,
            response_model=InsightSynthesisResult,
            max_tokens=420,
        )

        artifact = await self._store_artifact(
            user_id=user_id,
            connection_id=connection_id,
            session_id=session_id,
            artifact_type="recommendation",
            title=recommendation.title,
            prompt=prompt,
            summary=recommendation.summary,
            payload={
                "findings": recommendation.findings,
                "recommendations": recommendation.recommendations,
                "risks": recommendation.risks,
                "opportunities": recommendation.opportunities,
                "confidence": recommendation.confidence,
                "business_context_snapshot": business_context,
            },
            db=db,
        )
        return artifact, usage

    async def generate_scenario(
        self,
        *,
        user_id: str,
        connection_id: str,
        prompt: str,
        session_id: str | None,
        db: AsyncSession,
    ) -> tuple[CopilotArtifact, dict[str, int]]:
        context = await self._load_connection_context(connection_id, user_id, db)
        business_context = await self._load_business_context(user_id, connection_id, db)

        system_prompt = """You are Vayent, a scenario-planning assistant.

Analyze the proposed scenario and return a grounded what-if view.

Rules:
- Be explicit about assumptions.
- Focus on upside, downside, what to monitor, and what to do next.
- If the evidence is limited, keep the wording careful and lower confidence.

Return JSON:
{
  "title": "scenario title",
  "summary": "scenario summary",
  "assumptions": ["assumption"],
  "upside": ["potential upside"],
  "downside": ["potential downside"],
  "watch_items": ["signal to monitor"],
  "recommendations": ["next move"],
  "confidence": 0.0
}"""
        scenario_prompt = json.dumps(
            {
                "user_prompt": prompt,
                "schema_context": context.schema_context,
                "business_context": business_context,
            },
            default=str,
        )
        scenario, usage = await self._complete_json(
            system_prompt=system_prompt,
            user_prompt=scenario_prompt,
            response_model=ScenarioAnalysisResult,
            max_tokens=420,
        )

        artifact = await self._store_artifact(
            user_id=user_id,
            connection_id=connection_id,
            session_id=session_id,
            artifact_type="scenario",
            title=scenario.title,
            prompt=prompt,
            summary=scenario.summary,
            payload={
                "assumptions": scenario.assumptions,
                "upside": scenario.upside,
                "downside": scenario.downside,
                "watch_items": scenario.watch_items,
                "recommendations": scenario.recommendations,
                "confidence": scenario.confidence,
                "business_context_snapshot": business_context,
            },
            db=db,
        )
        return artifact, usage

    async def build_dashboard(
        self,
        *,
        user_id: str,
        connection_id: str | None,
        source_ids: list[str] | None = None,
        prompt: str,
        db: AsyncSession,
    ) -> tuple[CopilotArtifact, dict[str, int]]:
        target_source_ids = list(
            dict.fromkeys(
                source_id.strip()
                for source_id in (source_ids or [])
                if source_id and source_id.strip()
            )
        )
        if not target_source_ids and connection_id:
            target_source_ids = [connection_id]
        if not target_source_ids:
            raise ValueError("Select a source before building a dashboard.")

        primary_source_id = target_source_ids[0]
        spreadsheet_sources = []
        for source_id in target_source_ids:
            spreadsheet = await spreadsheet_service.get_source(source_id, db)
            if spreadsheet and spreadsheet.user_id == user_id and spreadsheet.is_active:
                spreadsheet_sources.append(spreadsheet)

        if spreadsheet_sources and spreadsheet_sources[0].id == primary_source_id:
            if len(spreadsheet_sources) == 1:
                source = spreadsheet_sources[0]
                dashboard = spreadsheet_service.build_dashboard_payload(
                    source=source,
                    prompt=prompt,
                )
                artifact = await self._store_artifact(
                    user_id=user_id,
                    connection_id=None,
                    source_id=source.id,
                    source_type="spreadsheet",
                    session_id=None,
                    artifact_type="dashboard",
                    title=dashboard["title"],
                    prompt=prompt,
                    summary=dashboard["summary"],
                    payload=dashboard["payload"],
                    db=db,
                )
                return artifact, {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                }

            cards: list[dict[str, Any]] = []
            insights: list[dict[str, Any]] = []
            recommendations: list[dict[str, Any]] = []
            source_names: list[str] = []
            for source in spreadsheet_sources:
                source_names.append(source.name)
                dashboard = spreadsheet_service.build_dashboard_payload(
                    source=source,
                    prompt=prompt,
                )
                payload = dashboard.get("payload", {})
                for card in payload.get("cards", []) or []:
                    enriched_card = dict(card)
                    enriched_card["source_id"] = source.id
                    enriched_card["source_name"] = source.name
                    cards.append(enriched_card)
                for insight in payload.get("insights", []) or []:
                    enriched_insight = dict(insight)
                    enriched_insight.setdefault("source_name", source.name)
                    insights.append(enriched_insight)
                for recommendation in payload.get("recommendations", []) or []:
                    enriched_recommendation = dict(recommendation)
                    enriched_recommendation.setdefault("source_name", source.name)
                    recommendations.append(enriched_recommendation)

            title = "Spreadsheet business dashboard"
            summary = "Business-focused dashboard generated from selected spreadsheets."
            artifact = await self._store_artifact(
                user_id=user_id,
                connection_id=None,
                source_id=primary_source_id,
                source_type="spreadsheet",
                session_id=None,
                artifact_type="dashboard",
                title=title,
                prompt=prompt,
                summary=summary,
                payload={
                    "description": summary,
                    "source_ids": [source.id for source in spreadsheet_sources],
                    "source_type": "spreadsheet",
                    "source_names": source_names,
                    "cards": cards[:20],
                    "insights": insights[:12],
                    "recommendations": recommendations[:12],
                    "prompt": prompt,
                },
                db=db,
            )
            return artifact, {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            }

        database_connection_id = primary_source_id
        context = await self._load_connection_context(database_connection_id, user_id, db)
        if not context.has_schema:
            raise ValueError("Sync the schema before building a dashboard.")

        system_prompt = f"""You are Vayent, an analytics dashboard builder.

Create a compact dashboard plan for the business prompt using the schema below.

Rules:
- Return 3 to 5 cards.
- Each card must use a read-only SELECT query.
- Prefer metrics, trends, rankings, and health indicators.
- Use only tables and columns from the schema.
- Keep each SQL focused on one card.
- Card titles, descriptions, and recommendations are for non-technical users.
  Use nicknames, schema descriptions, and human-readable labels instead of raw
  table names, column names, or database names.
- Keep raw identifiers only inside SQL. When returning result columns, alias
  them to clear business labels in snake_case when possible, such as
  total_revenue, active_users, or retention_rate.
- Vayent bridges technical data methods and business planning, so each card
  should make the decision value clear without requiring SQL knowledge.
- For every card, explain what happened, why it likely matters, and what the
  business owner should do next.
- Choose a visualization hint from kpi, trend, line, area, bar, pie, donut,
  funnel, cohort, forecast, heatmap, or table.

Schema:
{context.schema_context}

Return JSON:
{{
  "title": "dashboard title",
  "description": "what this dashboard covers",
  "cards": [
    {{
      "title": "card title",
      "description": "why it matters",
      "sql": "SELECT ...",
      "visualization": "best visualization hint",
      "explanation": "plain-language explanation",
      "interpretation": "business interpretation",
      "recommended_action": "specific action to take"
    }}
  ]
}}"""
        plan, usage = await self._complete_json(
            system_prompt=system_prompt,
            user_prompt=prompt,
            response_model=DashboardPlanResult,
            max_tokens=520,
        )

        cards = []
        insights = []
        recommendations = []
        for card in plan.cards[:5]:
            try:
                evidence = await self._execute_readonly_query(
                    context=context,
                    sql=card.sql,
                    user_id=user_id,
                    db=db,
                    label=card.title,
                )
                value = self._extract_numeric_value(evidence.get("rows", []))
                explanation = (
                    card.explanation.strip()
                    or f"{card.title} summarizes a current business signal from the selected data."
                )
                interpretation = (
                    card.interpretation.strip()
                    or card.description.strip()
                    or "Use this to understand what changed and where attention is needed."
                )
                recommended_action = (
                    card.recommended_action.strip()
                    or f"Review {card.title.lower()} and focus on the strongest or weakest segment first."
                )
                cards.append(
                    {
                        "title": card.title,
                        "description": card.description,
                        "sql": card.sql,
                        "visualization": card.visualization or "auto",
                        "explanation": explanation,
                        "interpretation": interpretation,
                        "recommended_action": recommended_action,
                        "status": "success",
                        "value": value,
                        "row_count": evidence.get("row_count"),
                        "rows": evidence.get("rows"),
                        "truncated": evidence.get("truncated"),
                    }
                )
                insight_body = interpretation
                if isinstance(value, numbers.Number):
                    insight_body = (
                        f"{card.title} is currently {value:,.0f}. {interpretation}"
                    )
                insights.append(
                    {
                        "title": card.title,
                        "body": insight_body,
                        "tone": "neutral",
                    }
                )
                recommendations.append(
                    {
                        "title": f"Act on {card.title}",
                        "body": recommended_action,
                        "priority": "Medium",
                    }
                )
            except Exception as exc:
                cards.append(
                    {
                        "title": card.title,
                        "description": card.description,
                        "sql": card.sql,
                        "visualization": card.visualization or "auto",
                        "explanation": card.explanation,
                        "interpretation": card.interpretation,
                        "recommended_action": card.recommended_action,
                        "status": "error",
                        "error": str(exc),
                    }
                )

        artifact = await self._store_artifact(
            user_id=user_id,
            connection_id=database_connection_id,
            source_id=database_connection_id,
            source_type="database",
            session_id=None,
            artifact_type="dashboard",
            title=plan.title,
            prompt=prompt,
            summary=plan.description,
            payload={
                "description": plan.description,
                "source_ids": [database_connection_id],
                "source_type": "database",
                "source_name": context.connection.name,
                "cards": cards,
                "insights": insights[:8],
                "recommendations": recommendations[:8],
            },
            db=db,
        )
        return artifact, usage

    async def create_watchlist(
        self,
        *,
        user_id: str,
        connection_id: str,
        prompt: str,
        comparator: str,
        threshold_value: float,
        db: AsyncSession,
    ) -> tuple[CopilotWatchlist, dict[str, int]]:
        context = await self._load_connection_context(connection_id, user_id, db)
        if not context.has_schema:
            raise ValueError("Sync the schema before creating a watchlist.")

        system_prompt = f"""You are Vayent, a metric watchlist builder.

Convert the user's rule into a single SQL metric query.

Rules:
- Return one read-only SELECT statement only.
- The query should ideally return one row and one numeric metric.
- Use only the provided schema.
- Keep the title concise and operational.

Schema:
{context.schema_context}

Return JSON:
{{
  "title": "watchlist title",
  "description": "what this alert monitors",
  "sql": "SELECT ...",
  "note": "what the metric means"
}}"""
        plan, usage = await self._complete_json(
            system_prompt=system_prompt,
            user_prompt=prompt,
            response_model=WatchlistPlanResult,
            max_tokens=220,
        )

        watchlist = CopilotWatchlist(
            id=str(uuid.uuid4()),
            user_id=user_id,
            connection_id=connection_id,
            title=plan.title,
            description=plan.description,
            prompt=prompt,
            sql_text=plan.sql,
            comparator=comparator,
            threshold_value=threshold_value,
            payload={"note": plan.note},
        )
        db.add(watchlist)
        await db.commit()
        await db.refresh(watchlist)
        evaluated_watchlist = await self.evaluate_watchlist(
            watchlist_id=watchlist.id,
            user_id=user_id,
            db=db,
        )
        return evaluated_watchlist, usage

    async def evaluate_watchlist(
        self,
        *,
        watchlist_id: str,
        user_id: str,
        db: AsyncSession,
    ) -> CopilotWatchlist:
        stmt = select(CopilotWatchlist).where(
            CopilotWatchlist.id == watchlist_id,
            CopilotWatchlist.user_id == user_id,
        )
        watchlist = (await db.execute(stmt)).scalar_one_or_none()
        if not watchlist:
            raise ValueError("Watchlist not found")

        if not watchlist.connection_id:
            raise ValueError(
                "Watchlist is not linked to a database connection")

        context = await self._load_connection_context(watchlist.connection_id, user_id, db)
        evidence = await self._execute_readonly_query(
            context=context,
            sql=watchlist.sql_text,
            user_id=user_id,
            db=db,
            label=watchlist.title,
        )
        numeric_value = self._extract_numeric_value(evidence.get("rows", []))

        if numeric_value is None:
            watchlist.last_status = "no_data"
            watchlist.last_value = None
            watchlist.last_summary = "The watchlist query ran, but Vayent could not extract a numeric metric."
        else:
            is_alert = self._compare(
                numeric_value, watchlist.comparator, watchlist.threshold_value)
            watchlist.last_value = numeric_value
            watchlist.last_status = "alert" if is_alert else "ok"
            comparator_text = {
                "gt": ">",
                "gte": ">=",
                "lt": "<",
                "lte": "<=",
            }.get(watchlist.comparator, watchlist.comparator)
            watchlist.last_summary = (
                f"Latest value {numeric_value:.2f} {comparator_text} threshold {watchlist.threshold_value:.2f}."
            )

        watchlist.last_evaluated_at = datetime.utcnow()
        payload = self._json_safe(watchlist.payload) or {}
        payload["latest_evidence"] = evidence
        watchlist.payload = payload
        await db.commit()
        await db.refresh(watchlist)
        return watchlist


copilot_service = CopilotService()
