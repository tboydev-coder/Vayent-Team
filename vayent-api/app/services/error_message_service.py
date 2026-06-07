"""Translate low-level errors into user-facing messages."""
from __future__ import annotations

import re


AI_SERVICE_UNREACHABLE_MESSAGE = (
    "I couldn't process that request because the AI service is temporarily unreachable. "
    "Please try again in a moment. If this keeps happening, contact your workspace admin."
)


class ErrorMessageService:
    """Create concise, readable messages for users."""

    @staticmethod
    def _extract_identifier(pattern: str, raw_error: str) -> str | None:
        match = re.search(pattern, raw_error, re.IGNORECASE)
        if not match:
            return None
        return match.group(1)

    def connection_failed_message(
        self,
        raw_error: str,
        host: str,
        port: int,
        database_name: str,
    ) -> str:
        """Convert connection errors into clear frontend-safe copy."""
        message = raw_error.strip() or "Unknown connection error."
        lowered = message.lower()

        if "getaddrinfo" in lowered or "name or service not known" in lowered:
            return f"Connection failed: The host '{host}' could not be resolved. Please verify the server address."
        if "timeout" in lowered or "timed out" in lowered:
            return f"Connection failed: Unable to reach the database server at {host}:{port}. Please check the host, port, and network access."
        if "connection refused" in lowered or "can't connect" in lowered:
            return f"Connection failed: The database server at {host}:{port} refused the connection. Please confirm that the server is running and accepts remote connections."
        if "rejected ssl upgrade" in lowered:
            return (
                "Connection failed: The database server rejected SSL/TLS negotiation. "
                "Use TLS if your provider supports it, or choose 'SSL disabled' only "
                "for trusted test/private databases."
            )
        if "password authentication failed" in lowered or "access denied" in lowered:
            return "Connection failed: The username or password was rejected by the database server."
        if "does not exist" in lowered and database_name.lower() in lowered:
            return f"Connection failed: The database '{database_name}' was not found on the server."
        if "unknown database" in lowered:
            return f"Connection failed: The database '{database_name}' was not found on the server."

        return f"Connection failed: {message}"

    def query_failed_message(self, raw_error: str) -> str:
        """Convert query/database execution errors into clear chat messages."""
        message = raw_error.strip() or "Unknown query error."
        lowered = message.lower()

        column_name = self._extract_identifier(
            r"(?:column|unknown column)\s+[\"'`]?([a-zA-Z0-9_\.]+)[\"'`]?",
            message,
        )
        if column_name and ("does not exist" in lowered or "unknown column" in lowered):
            hint = self._extract_identifier(r"did you mean [\"'`]?([a-zA-Z0-9_\.]+)[\"'`]?", message)
            suffix = f" Did you mean '{hint}'?" if hint else " Sync the database schema and try again."
            return f"Query failed: The column '{column_name}' does not exist.{suffix}"

        table_name = self._extract_identifier(
            r"(?:relation|table)\s+[\"'`]?([a-zA-Z0-9_\.]+)[\"'`]?",
            message,
        )
        if table_name and ("does not exist" in lowered or "unknown table" in lowered):
            return f"Query failed: The table '{table_name}' does not exist in the connected database. Sync the schema and try again."

        if "syntax error" in lowered:
            return "Query failed: The generated SQL was not valid for this database."
        if "permission denied" in lowered or "not authorized" in lowered:
            return "Query failed: This database user does not have permission to run that operation."
        if "timeout" in lowered or "timed out" in lowered:
            return "Query failed: The database took too long to respond. Please narrow the request and try again."
        if "connection" in lowered and ("closed" in lowered or "lost" in lowered):
            return "Query failed: The database connection was interrupted while the request was running."

        return f"Query failed: {message}"

    def ai_failed_message(self, raw_error: str) -> str:
        """Return a safe message when AI generation fails."""
        message = raw_error.strip() if raw_error else ""
        lowered = message.lower()

        if not message:
            return "I couldn't process that request because the AI service is currently unavailable."

        if "insufficient_quota" in lowered or ("quota" in lowered and "token" not in lowered):
            return (
                "I couldn't process that request because the AI service quota is exhausted right now. "
                "Please try again later."
            )

        if (
            "rate limit" in lowered
            or "rate_limit" in lowered
            or "too many requests" in lowered
            or "status code: 429" in lowered
            or "error code: 429" in lowered
        ):
            return (
                "I couldn't process that request because the AI service is rate-limiting requests right now. "
                "Please wait a moment and try again."
            )

        if (
            "invalid_api_key" in lowered
            or ("api key" in lowered and "invalid" in lowered)
            or "didn't provide an api key" in lowered
            or "did not provide an api key" in lowered
            or "no api key" in lowered
            or "openai_api_key is not configured" in lowered
            or ("api key" in lowered and "must be set" in lowered)
            or ("api key" in lowered and "must be configured" in lowered)
        ):
            return (
                "I couldn't process that request because the AI service is misconfigured. "
                "Please contact support or try again later."
            )

        if "authentication" in lowered and "ai service" in lowered:
            return (
                "I couldn't process that request because the AI service authentication failed. "
                "Please try again later."
            )

        if (
            "couldn't be reached" in lowered
            or "could not be reached" in lowered
            or "cannot connect to" in lowered
            or "unreachable" in lowered
            or "connection error" in lowered
            or ("connection" in lowered and "failed" in lowered)
        ):
            return AI_SERVICE_UNREACHABLE_MESSAGE

        if "timeout" in lowered or "timed out" in lowered:
            return (
                "I couldn't process that request because the AI service took too long to respond. "
                "Please try again."
            )

        if "length limit" in lowered or "finish_reason" in lowered and "length" in lowered:
            return (
                "I couldn't process that request because the AI response was truncated before it finished. "
                "Please try again, or shorten the request/schema context."
            )

        if "server error" in lowered or "status code: 5" in lowered:
            return "I couldn't process that request because the AI service is currently unavailable."

        if message.startswith("I couldn't process that request because the AI service"):
            return message

        return f"I couldn't process that request because the AI service returned an error: {message}"


error_message_service = ErrorMessageService()
