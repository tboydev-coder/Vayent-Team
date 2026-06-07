"""AI-powered chat planning and result narration using OpenAI."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from openai import AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError

from app.config import get_settings
from app.services.openai_config_service import (
    build_async_openai_client,
    build_chat_completion_controls,
    require_openai_api_key,
    require_openai_reachable,
)

logger = logging.getLogger(__name__)

WORKSPACE_QUERY_STOPWORDS = {
    "about",
    "after",
    "again",
    "against",
    "along",
    "also",
    "around",
    "because",
    "before",
    "being",
    "between",
    "both",
    "current",
    "database",
    "databases",
    "for",
    "from",
    "have",
    "into",
    "just",
    "last",
    "make",
    "month",
    "show",
    "that",
    "their",
    "them",
    "then",
    "this",
    "those",
    "through",
    "today",
    "using",
    "what",
    "when",
    "where",
    "which",
    "with",
}

WORKSPACE_MULTI_DATABASE_CUES = (
    "across",
    "benchmark",
    "benchmarks",
    "compare",
    "comparison",
    "differences",
    "distributed",
    "ranking",
    "rankings",
    "side by side",
    "versus",
    "vs",
)


class AssistantPlanResult(BaseModel):
    """Structured LLM output for deciding how to answer a user."""

    action: str = Field(pattern="^(query|respond|clarify|refuse)$")
    response: str = ""
    sql: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)


class WorkspaceQueryPlan(BaseModel):
    """Connection-scoped SQL plan for workspace chat."""

    connection_id: str
    sql: str = Field(min_length=1)
    reason: str = ""


class WorkspaceAssistantPlanResult(BaseModel):
    """Structured LLM output for workspace chat across databases."""

    action: str = Field(pattern="^(query|respond|clarify|refuse)$")
    response: str = ""
    queries: list[WorkspaceQueryPlan] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)


class AssistantResponseResult(BaseModel):
    """Structured LLM output for user-facing replies."""

    response: str = ""


class SQLGenerationService:
    """Service for planning DB-aware chat behaviour with an LLM."""

    def __init__(self):
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
    def _normalize_workspace_text(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()

    @classmethod
    def _tokenize_workspace_text(cls, value: str) -> list[str]:
        tokens: list[str] = []
        seen: set[str] = set()

        for token in cls._normalize_workspace_text(value).split():
            if len(token) < 3 or token in WORKSPACE_QUERY_STOPWORDS:
                continue
            if token in seen:
                continue
            seen.add(token)
            tokens.append(token)

        return tokens

    @staticmethod
    def _workspace_token_matches(token: str, normalized_text: str) -> bool:
        if not token or not normalized_text:
            return False
        return bool(re.search(rf"\b{re.escape(token)}[a-z0-9]*\b", normalized_text))

    @classmethod
    def _build_workspace_selection_guidance(
        cls,
        *,
        user_query: str,
        active_connection_id: str,
        workspace_connections: list[dict[str, Any]],
    ) -> str:
        normalized_query = cls._normalize_workspace_text(user_query)
        padded_query = f" {normalized_query} "
        query_tokens = cls._tokenize_workspace_text(user_query)[:10]
        comparison_cues = [
            cue for cue in WORKSPACE_MULTI_DATABASE_CUES if f" {cue} " in padded_query
        ]

        scorecards = []
        for connection in workspace_connections:
            connection_name = str(connection.get("name", ""))
            database_name = str(connection.get("database_name", ""))
            metadata_blob = cls._normalize_workspace_text(
                " ".join(
                    filter(
                        None,
                        [
                            connection_name,
                            database_name,
                            str(connection.get("db_type", "")),
                        ],
                    )
                )
            )
            schema_blob = cls._normalize_workspace_text(
                str(connection.get("schema_context", "")))
            padded_metadata_blob = f" {metadata_blob} "
            padded_schema_blob = f" {schema_blob} "

            exact_name_matches = []
            for label in filter(None, [connection_name, database_name]):
                normalized_label = cls._normalize_workspace_text(str(label))
                if normalized_label and f" {normalized_label} " in padded_query:
                    exact_name_matches.append(str(label))

            metadata_matches = [
                token
                for token in query_tokens
                if cls._workspace_token_matches(token, padded_metadata_blob)
            ]
            schema_matches = [
                token
                for token in query_tokens
                if token not in metadata_matches
                and cls._workspace_token_matches(token, padded_schema_blob)
            ]

            scorecards.append(
                {
                    "connection_id": str(connection.get("id", "")),
                    "name": connection_name or str(connection.get("id", "")),
                    "score": (len(exact_name_matches) * 8)
                    + (len(metadata_matches) * 3)
                    + len(schema_matches),
                    "exact_name_matches": exact_name_matches[:2],
                    "metadata_matches": metadata_matches[:4],
                    "schema_matches": schema_matches[:4],
                }
            )

        ranked_scorecards = sorted(
            scorecards,
            key=lambda item: (
                item["score"], item["connection_id"] == active_connection_id),
            reverse=True,
        )

        if len(workspace_connections) == 1 and ranked_scorecards:
            recommendation = (
                f"Recommended routing: use connection `{ranked_scorecards[0]['connection_id']}` "
                "because it is the only selected database."
            )
        elif comparison_cues:
            recommendation = (
                "Recommended routing: treat this as a multi-database question and use "
                "multiple queries for the relevant selected databases, because the "
                f"question includes comparison cues: {', '.join(comparison_cues)}."
            )
        elif ranked_scorecards and ranked_scorecards[0]["score"] > 0:
            second_score = ranked_scorecards[1]["score"] if len(
                ranked_scorecards) > 1 else -1
            if (
                ranked_scorecards[0]["exact_name_matches"]
                or ranked_scorecards[0]["score"] >= second_score + 2
            ):
                recommendation = (
                    f"Recommended routing: use connection `{ranked_scorecards[0]['connection_id']}` "
                    "because it has the strongest lexical and schema match for the "
                    "question."
                )
            else:
                recommendation = (
                    f"Recommended routing: use active connection `{active_connection_id}` as "
                    "the fallback because the top matches are too close to separate "
                    "confidently."
                )
        else:
            recommendation = (
                f"Recommended routing: use active connection `{active_connection_id}` as "
                "the fallback because the question does not clearly isolate another "
                "selected source."
            )

        score_lines = []
        for scorecard in ranked_scorecards:
            match_details = []
            if scorecard["exact_name_matches"]:
                match_details.append(
                    "direct name matches: " +
                    ", ".join(scorecard["exact_name_matches"])
                )
            if scorecard["metadata_matches"]:
                match_details.append(
                    "metadata matches: " +
                    ", ".join(scorecard["metadata_matches"])
                )
            if scorecard["schema_matches"]:
                match_details.append(
                    "schema matches: " + ", ".join(scorecard["schema_matches"])
                )
            if not match_details:
                match_details.append("no strong lexical match")

            active_marker = " (active)" if scorecard["connection_id"] == active_connection_id else ""
            score_lines.append(
                f"- {scorecard['connection_id']} / {scorecard['name']}{active_marker}: "
                f"score {scorecard['score']}; " + "; ".join(match_details)
            )

        return "\n".join(
            [
                "Workspace routing guidance:",
                f"- Active fallback connection: {active_connection_id}",
                f"- Query tokens considered: {', '.join(query_tokens) if query_tokens else 'none'}",
                f"- Comparison cues detected: {', '.join(comparison_cues) if comparison_cues else 'none'}",
                *score_lines,
                recommendation,
            ]
        )

    async def plan_response(
        self,
        user_query: str,
        schema_context: str,
        database_type: str,
        conversation_history: Optional[list[dict[str, str]]] = None,
        intent_hint: str | None = None,
    ) -> dict[str, Any]:
        """
        Decide whether to answer directly, clarify, refuse, or generate SQL.
        """
        try:
            intent_prompt = ""
            if intent_hint == "write_request":
                intent_prompt = """

Write-request guidance:
- Treat requests to add, create, insert, update, edit, rename, delete, or remove database records as in-scope.
- Do not refuse solely because the user wants to modify data.
- If the target table/entity is clear from the schema or conversation, set action to "query" and generate the SQL.
- If required columns or values are missing, set action to "clarify" and ask only for the missing database fields.
"""
            elif intent_hint == "record_lookup":
                intent_prompt = """

Record-lookup guidance:
- Treat requests like "give me details on <name>", "tell me about <person>", "who is <name>", and "look up <student>" as in-scope when the schema suggests person-style tables such as students, users, customers, members, employees, contacts, or similar.
- Do not refuse solely because the user asks about an individual. If that individual could be a row in the connected database, set action to "query".
- Generate one read-only SELECT statement that searches the most plausible identifying text columns from the schema, such as name, full_name, first_name, last_name, email, username, or similar.
- Use case-insensitive matching and return the most relevant matching rows with a sensible LIMIT.
- If the schema truly lacks any plausible table or identifying column, set action to "clarify" and ask which table or identifier should be used.
"""

            system_prompt = f"""You are Vayent, a CEO copilot and database-aware advisor for one connected database.

You should stay grounded in:
1. The connected database schema provided below
2. The previous conversation
3. Known Vayent product context
4. The user's business, product, operations, or database goals

Database type: {database_type}

Available schema:
{schema_context}

Product context:
- Vayent bridges technical ways of getting insight from data with practical
  business planning, visualization, and schema understanding for non-technical
  users.

Rules:
- Never invent tables, columns, relationships, or data.
- If the user asks who you are or who created you, answer: "I am Vayent, a database analyst assistant AI created by Oluwatumininu Owolabi. I help bridge technical data work with business insight, planning, visualization, and schema understanding."
- The user may ask about:
  - the Vayent app and how it can help
  - their business, operations, growth, retention, pricing, competitors, and strategy
  - analytics, statistical improvement, KPI design, segmentation, and data quality
  - the connected database schema, prior results, or live data
- User-facing replies must use plain business language. Prefer schema
  nicknames, user-facing labels, descriptions, and SQL result aliases over raw
  table names, column names, or database names. If no alias exists, humanize the
  identifier before showing it.
- Keep raw technical identifiers inside SQL or technical details only.
- Treat app questions, CEO-style advisory questions, and strategic business questions as in-scope even when they are not direct data retrieval requests.
- If the user asks about the app, product usage, workflow ideas, or a follow-up that can be answered from the conversation without a fresh query, set action to "respond".
- If the user asks for a recommendation, forecast, growth analysis, competitor positioning, or performance insight, either answer from the existing conversation or set action to "clarify" and explain what metrics, time windows, segments, or data fields would make the advice stronger.
- Treat follow-up write requests that refer to earlier database context as in-scope. For example, if the user was discussing students and then says "add one more student", use that context instead of refusing.
- Requests for details about a named student, customer, user, member, employee, or other likely record in the connected database are in-scope. If the schema supports that lookup, query the database instead of refusing.
- If the schema is missing or incomplete, you may still answer app/product questions and give general business frameworks, but do not generate SQL unless the available schema supports it. Ask the user to sync the schema when live data grounding is needed.
- If the request is unrelated to the app, the business, the conversation, or the connected data, set action to "refuse".
- If you need live database data to answer correctly, set action to "query" and provide one SQL statement only.
- If the user asks for the "most active user", return the user identifier plus the supporting activity count, not just the identifier. Prefer columns/aliases like email, username, activity_count, api_request_count, ai_request_count, chat_count, or query_count when the schema supports them.
- If the user asks why someone is most active, generate a breakdown query grouped by meaningful activity dimensions such as action, resource_type, endpoint, method, or request_kind, with a count column.
- If the request is too ambiguous, set action to "clarify".
- When filtering user-provided text values such as names, use case-insensitive matching so David, david, and DAVID are treated as the same person when appropriate.
- Use only columns and tables that appear in the schema.
- When selecting values for a user-facing result, add clear SQL aliases to
  selected expressions and columns where possible. Prefer readable snake_case
  aliases that can be humanized by the app, such as customer_name, total_orders,
  retention_rate, or latest_activity.
- You may provide strategic suggestions and recommendations, but make sure they are clearly framed as guidance grounded in the schema, conversation, or returned data rather than as confirmed facts.
- Do not explain SQL unless the user asked for technical details.
- For "respond", "clarify", and "refuse", leave sql as null.
- Keep the response concise and natural, usually one or two short sentences.
  Vary sentence structure and avoid sounding templated or rigid. Do not prefix
  it with labels such as "Response:".
{intent_prompt}

Return JSON with this exact shape:
{{
  "action": "query|respond|clarify|refuse",
  "response": "short user-facing reply",
  "sql": "single SQL statement or null",
  "confidence": 0.0,
  "warnings": ["optional warning"]
}}"""

            client = self._get_client()
            usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            last_parse_error: Exception | None = None

            for attempt, active_system_prompt in enumerate(
                (
                    system_prompt,
                    system_prompt
                    + "\n\nRepair instruction: return only a valid JSON object with action, response, sql, confidence, and warnings. "
                    "Do not include markdown, comments, or extra keys. If a query is not safe or not necessary, set action to respond or clarify and set sql to null.",
                ),
                start=1,
            ):
                messages = [{"role": "system", "content": active_system_prompt}]
                if conversation_history:
                    messages.extend(conversation_history)
                messages.append({"role": "user", "content": user_query})

                response = await client.chat.completions.create(
                    model=self.settings.openai_model,
                    messages=messages,
                    # temperature=0.05,
                    response_format={"type": "json_object"},
                    **build_chat_completion_controls(
                        self.settings.openai_model,
                        280 if attempt == 1 else 700,
                    ),
                )

                response_usage = self._usage_payload(response)
                usage = {
                    key: usage.get(key, 0) + response_usage.get(key, 0)
                    for key in usage
                }

                response_text = response.choices[0].message.content or "{}"
                try:
                    result = AssistantPlanResult.model_validate(
                        json.loads(response_text)
                    )
                    payload = result.model_dump()
                    payload["usage"] = usage
                    if attempt > 1:
                        payload.setdefault("warnings", []).append(
                            "The assistant planner repaired an invalid first response."
                        )
                    return payload
                except (json.JSONDecodeError, ValidationError) as exc:
                    last_parse_error = exc
                    logger.warning(
                        "Assistant plan parse failed on attempt %s: %s",
                        attempt,
                        exc,
                    )
                    continue

            raise last_parse_error or ValueError("Assistant planner returned invalid JSON.")
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.error("Failed to parse assistant plan: %s", exc)
            return {
                "action": "clarify",
                "response": "I can help with your data, the app, and business strategy, but I need a bit more context to give you a useful answer.",
                "sql": None,
                "confidence": 0,
                "warnings": ["The AI planner returned an invalid response."],
                "error": "Response parsing failed",
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
        except Exception as exc:
            logger.error("Assistant planning error: %s", exc, exc_info=True)
            return {
                "action": "clarify",
                "response": "",
                "sql": None,
                "confidence": 0,
                "warnings": [],
                "error": str(exc),
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }

    async def plan_workspace_response(
        self,
        *,
        user_query: str,
        active_connection_id: str,
        workspace_connections: list[dict[str, Any]],
        conversation_history: Optional[list[dict[str, str]]] = None,
    ) -> dict[str, Any]:
        """Plan a response for a multi-database workspace conversation."""
        try:
            selection_guidance = self._build_workspace_selection_guidance(
                user_query=user_query,
                active_connection_id=active_connection_id,
                workspace_connections=workspace_connections,
            )
            connection_blocks = []
            for connection in workspace_connections:
                parts = [
                    f"Connection ID: {connection['id']}",
                    f"Connection name: {connection['name']}",
                    f"Database name: {connection['database_name']}",
                    f"Database type: {connection['db_type']}",
                    f"Is active: {'yes' if connection['id'] == active_connection_id else 'no'}",
                    "Schema:",
                    connection["schema_context"],
                ]

                # If row-level evidence is attached (for spreadsheets), include
                # a small sample so the planner can ground decisions in real
                # records without requiring a separate analysis step.
                try:
                    rows = connection.get("source_rows") or []
                    if rows:
                        import json as _json

                        sample = _json.dumps(rows[:8], default=str)
                        parts.append("Source rows (sample):")
                        parts.append(sample)
                except Exception:
                    # Best-effort only; do not fail planning on serialization issues
                    pass

                connection_blocks.append("\n".join(parts))

            system_prompt = f"""You are Vayent, a CEO copilot and database-aware advisor for a workspace with multiple connected databases.

The active database is connection `{active_connection_id}`.

Connected databases:

{'\n\n---\n\n'.join(connection_blocks)}

Routing hints for this question:

{selection_guidance}

Rules:
- Never invent tables, columns, relationships, databases, or results.
- If the user asks who you are or who created you, answer: "I am Vayent, a database analyst assistant AI created by Oluwatumininu Owolabi. I help bridge technical data work with business insight, planning, visualization, and schema understanding."
- Vayent bridges technical data access with business planning, visualization,
  and schema understanding for non-technical users.
- First infer the most relevant selected database from the wording, the routing hints above, connection metadata, and schema terms.
- Do not wait for the user to explicitly say "use the active database" or "selected databases". Choose the best-fit source automatically whenever one database is a clearly better match.
- If the question clearly points to one database by business domain, table names, metrics, or connection naming, return one query for that database.
- If the wording implies comparison, benchmarking, ranking, spread, or cross-system analysis, you may return multiple queries even when the user does not explicitly mention the selected databases.
- Use the active database as a tiebreaker or fallback only when the best source is still ambiguous after checking the schema and metadata signals.
- Each query must target exactly one connection from the provided list and must use only that connection's schema.
- User-facing replies must prefer aliases, nicknames, descriptions, and
  humanized labels over raw table names, column names, or database names.
- Keep raw identifiers inside SQL or technical details only. Use readable
  snake_case aliases for selected result columns when possible.
- Prefer the smallest number of queries needed to answer the user well.
- Workspace mode is read-focused. Never generate INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, or other destructive SQL here. If the user asks for a write operation, set action to "clarify" and tell them to use the single-database chat for write operations.
- If the request can be answered from prior conversation or product knowledge without new SQL, set action to "respond".
- If the request is ambiguous, set action to "clarify".
- If the request is unrelated to the app, the workspace, or the connected data, set action to "refuse".
- Keep the response concise and natural, usually one or two short sentences.
  Vary the wording so answers do not feel templated.
- For "respond", "clarify", and "refuse", return an empty queries array.

Return JSON with this exact shape:
{{
  "action": "query|respond|clarify|refuse",
  "response": "short user-facing reply",
  "queries": [
    {{
      "connection_id": "one of the provided connection ids",
      "sql": "single SQL statement for that connection",
      "reason": "short note about why this connection is included"
    }}
  ],
  "confidence": 0.0,
  "warnings": ["optional warning"]
}}"""

            client = self._get_client()
            usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            last_parse_error: Exception | None = None
            for attempt, active_system_prompt in enumerate(
                (
                    system_prompt,
                    system_prompt
                    + "\n\nRepair instruction: return only a valid JSON object with action, response, queries, confidence, and warnings. "
                    "Do not include markdown, comments, or extra keys. If no query is safe, use action respond or clarify with an empty queries array.",
                ),
                start=1,
            ):
                messages = [{"role": "system", "content": active_system_prompt}]
                if conversation_history:
                    messages.extend(conversation_history)
                messages.append({"role": "user", "content": user_query})

                response = await client.chat.completions.create(
                    model=self.settings.openai_model,
                    messages=messages,
                    # temperature=0.05,
                    response_format={"type": "json_object"},
                    **build_chat_completion_controls(
                        self.settings.openai_model,
                        420 if attempt == 1 else 900,
                    ),
                )
                response_usage = self._usage_payload(response)
                usage = {
                    key: usage.get(key, 0) + response_usage.get(key, 0)
                    for key in usage
                }

                response_text = response.choices[0].message.content or "{}"
                try:
                    result = WorkspaceAssistantPlanResult.model_validate(
                        json.loads(response_text)
                    )
                    payload = result.model_dump()
                    payload["usage"] = usage
                    if attempt > 1:
                        payload.setdefault("warnings", []).append(
                            "The workspace planner repaired an invalid first response."
                        )
                    return payload
                except (json.JSONDecodeError, ValidationError) as exc:
                    last_parse_error = exc
                    logger.warning(
                        "Workspace assistant plan parse failed on attempt %s: %s",
                        attempt,
                        exc,
                    )
                    continue

            raise last_parse_error or ValueError("Workspace planner returned invalid JSON.")
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.error("Failed to parse workspace assistant plan: %s", exc)
            return {
                "action": "clarify",
                "response": "I checked the selected source metadata, but could not build a reliable live query from that request. Try naming the metric, segment, or time window.",
                "queries": [],
                "confidence": 0,
                "warnings": ["The workspace AI planner returned an invalid response."],
                "error": "Response parsing failed",
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
        except Exception as exc:
            logger.error("Workspace assistant planning error: %s", exc)
            return {
                "action": "clarify",
                "response": "",
                "queries": [],
                "confidence": 0,
                "warnings": [],
                "error": str(exc),
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }

    async def generate_result_response(
        self,
        user_query: str,
        query: str,
        results: list[dict[str, Any]],
        row_count: int,
        truncated: bool = False,
    ) -> dict[str, Any]:
        """
        Turn executed SQL results into a concise user-facing answer.
        """
        try:
            sample_results = results[:3]
            prompt = f"""You are Vayent, a CEO copilot and database-aware advisor. Convert query results into a direct answer for the user.

User question:
{user_query}

Executed SQL:
{query}

Returned rows count:
{row_count}

Rows sample:
{json.dumps(sample_results, default=str)}

Was the result truncated?
{truncated}

Rules:
- Answer only from the provided results.
- Keep the response concise and conversational.
- Do not mention SQL unless the user asked for it.
- Use plain business language for non-technical users. Prefer aliases and
  human-readable result labels over raw table, column, or database names.
- If a result key is a technical identifier, translate it into a readable label
  before mentioning it.
- If nothing was found, say so clearly.
- If the result was truncated, mention that the response is based on a preview.
- If the user asked for strategy, recommendations, or business advice, explain the implications of the results and suggest next steps grounded in the returned data.
- If the user asks for the most active user, answer directly: "Your most active user is ..." and include the returned count when available.
- If the user asks why a user is most active, summarize the returned counts by category and highlight the biggest drivers. Do not paste JSON or raw rows into the answer.
- When a recommendation is an inference rather than a confirmed fact, phrase it as guidance or a hypothesis.
- Vary the wording so answers feel responsive to the question instead of
  formulaic. Do not prefix the answer with labels such as "Response:".

Return JSON:
{{
  "response": "short user-facing answer"
}}"""

            client = self._get_client()
            response = await client.chat.completions.create(
                model=self.settings.openai_model,
                messages=[{"role": "user", "content": prompt}],
                # temperature=0.1,
                response_format={"type": "json_object"},
                **build_chat_completion_controls(self.settings.openai_model, 140),
            )

            response_text = response.choices[0].message.content or "{}"
            result = AssistantResponseResult.model_validate(
                json.loads(response_text))
            payload = result.model_dump()
            payload["usage"] = self._usage_payload(response)
            return payload
        except Exception as exc:
            logger.error("Result response generation error: %s", exc)
            return {
                "response": "",
                "error": str(exc),
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }

    async def generate_workspace_result_response(
        self,
        *,
        user_query: str,
        query_runs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Summarize results that came from multiple workspace data sources."""
        try:
            prompt = f"""You are Vayent, a CEO copilot and data-aware advisor. Convert multi-source query and spreadsheet analysis results into a concise answer for the user.

User question:
{user_query}

Executed data-source runs:
{json.dumps(query_runs, default=str)}

Rules:
- Answer only from the provided results.
- Use spreadsheet insights, recommendations, risks, opportunities, and rows when the source_type is spreadsheet.
- Mention source, database, or connection names when comparing sources.
- If one source returned nothing or failed, say so clearly.
- Keep the response concise and conversational.
- Do not mention SQL unless the user asked for it.
- Prefer aliases, nicknames, and humanized labels over raw technical identifiers.
- If the results suggest a recommendation, frame it as guidance grounded in the returned data.
- For spreadsheet insight requests, include the most important finding and a concrete next best action when present.

Return JSON:
{{
  "response": "short user-facing answer"
}}"""

            client = self._get_client()
            response = await client.chat.completions.create(
                model=self.settings.openai_model,
                messages=[{"role": "user", "content": prompt}],
                # temperature=0.1,
                response_format={"type": "json_object"},
                **build_chat_completion_controls(self.settings.openai_model, 180),
            )

            response_text = response.choices[0].message.content or "{}"
            result = AssistantResponseResult.model_validate(
                json.loads(response_text))
            payload = result.model_dump()
            payload["usage"] = self._usage_payload(response)
            return payload
        except Exception as exc:
            logger.error("Workspace result response generation error: %s", exc)
            return {
                "response": "",
                "error": str(exc),
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }

    async def generate_explanation(
        self,
        query: str,
        results: list[dict[str, Any]],
        limit: int = 3,
    ) -> str:
        """
        Compatibility wrapper for older callers.
        """
        payload = await self.generate_result_response(
            user_query="Summarize the result of this database operation.",
            query=query,
            results=results[:limit],
            row_count=len(results),
            truncated=len(results) > limit,
        )
        return payload.get("response") or "The query completed successfully."


sql_generation_service = SQLGenerationService()
