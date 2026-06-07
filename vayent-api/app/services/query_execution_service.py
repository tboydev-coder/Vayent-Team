"""Query execution and confirmation service."""
import logging
import secrets
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models import QueryLog, QueryConfirmation, ChatMessage
from app.safety.query_validator import query_safety
from app.db_connectors.connector import get_connector
from app.config import get_settings
from app.services.activity_service import activity_service
from app.services.error_message_service import error_message_service

logger = logging.getLogger(__name__)


class QueryExecutionService:
    """Service for executing queries with safety checks and confirmation."""

    def __init__(self):
        self.settings = get_settings()

    async def validate_and_prepare(self, query: str, *, force_read_only: bool = False) -> Dict[str, Any]:
        """
        Validate query and check safety.

        Returns: Safety check result
        """
        safety_result = query_safety.check_query_safety(query)
        # Enforce read-only policy when requested (voice sessions) or globally configured
        if safety_result["is_destructive"] and (force_read_only or self.settings.connected_database_read_only):
            safety_result["is_safe"] = False
            safety_result["error"] = "Write queries are disabled by server read-only policy"
        # Prevent destructive queries when server policy forbids them explicitly
        if safety_result["is_destructive"] and not self.settings.allow_destructive_queries:
            safety_result["is_safe"] = False
            safety_result["error"] = "Destructive queries are disabled by server policy"
        return safety_result

    async def request_confirmation(
        self,
        user_id: str,
        connection_id: str,
        query: str,
        db: AsyncSession,
    ) -> str:
        """
        Create a query confirmation record for destructive queries.

        Returns: confirmation_token
        """
        confirmation_token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(hours=1)

        confirmation = QueryConfirmation(
            id=str(uuid.uuid4()),
            user_id=user_id,
            connection_id=connection_id,
            query_text=query,
            confirmation_token=confirmation_token,
            expires_at=expires_at,
        )

        db.add(confirmation)
        await db.commit()

        logger.info(f"Created confirmation request for user {user_id}")
        activity_service.log_event(
            action="query.confirmation_requested",
            status="success",
            user_id=user_id,
            resource_type="query_confirmation",
            resource_id=confirmation.id,
            details={
                "connection_id": connection_id,
                "expires_at": expires_at.isoformat(),
                "query_preview": activity_service.preview_sql(query),
            },
        )
        return confirmation_token

    async def confirm_query(
        self,
        confirmation_token: str,
        user_id: str,
        connection_id: str,
        db: AsyncSession,
    ) -> Optional[str]:
        """
        Confirm a destructive query.

        Returns: The confirmed query text or None if token invalid/expired
        """
        result = await db.execute(
            select(QueryConfirmation).where(
                QueryConfirmation.confirmation_token == confirmation_token,
                QueryConfirmation.user_id == user_id,
                QueryConfirmation.connection_id == connection_id,
            )
        )
        confirmation = result.scalar_one_or_none()

        if not confirmation:
            logger.warning(f"Invalid confirmation token")
            activity_service.log_event(
                action="query.confirmation_failed",
                status="warning",
                user_id=user_id,
                resource_id=connection_id,
                resource_type="query_confirmation",
                details={"reason": "invalid_token_or_scope"},
            )
            return None

        if datetime.utcnow() > confirmation.expires_at:
            logger.warning(f"Confirmation token expired")
            activity_service.log_event(
                action="query.confirmation_failed",
                status="warning",
                user_id=confirmation.user_id,
                resource_type="query_confirmation",
                resource_id=confirmation.id,
                details={"reason": "expired"},
            )
            return None

        if confirmation.is_confirmed or confirmation.is_rejected:
            logger.warning(f"Confirmation already processed")
            activity_service.log_event(
                action="query.confirmation_failed",
                status="warning",
                user_id=confirmation.user_id,
                resource_type="query_confirmation",
                resource_id=confirmation.id,
                details={
                    "reason": "already_processed",
                    "is_confirmed": bool(confirmation.is_confirmed),
                    "is_rejected": bool(confirmation.is_rejected),
                },
            )
            return None

        # Mark as confirmed
        confirmation.is_confirmed = True
        confirmation.confirmed_at = datetime.utcnow()
        await db.commit()

        logger.info(f"Query confirmed by user {confirmation.user_id}")
        activity_service.log_event(
            action="query.confirmation_confirmed",
            status="success",
            user_id=confirmation.user_id,
            resource_type="query_confirmation",
            resource_id=confirmation.id,
            details={
                "query_preview": activity_service.preview_sql(confirmation.query_text),
            },
        )
        return confirmation.query_text

    async def execute_query(
        self,
        connection,
        query: str,
        username_decrypted: str,
        password_decrypted: str,
        user_id: str,
        db: AsyncSession,
        safety_check: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """
        Execute a query on user's database.

        Args:
            connection: DatabaseConnection model
            query: SQL query to execute
            username_decrypted: Decrypted database username
            password_decrypted: Decrypted database password
            user_id: User ID executing the query
            db: Database session

        Returns: Execution result with status and data
        """
        start_time = time.time()

        try:
            # Get connector
            connector = get_connector(
                db_type=connection.db_type.value,
                host=connection.host,
                port=connection.port,
                database=connection.database_name,
                username=username_decrypted,
                password=password_decrypted,
                ssl_mode=getattr(connection, "ssl_mode", None),
            )

            # Check if destructive
            safety_check = safety_check or await self.validate_and_prepare(query)
            is_destructive = safety_check["is_destructive"]
            query_type = safety_check["query_type"]

            if not safety_check["is_safe"]:
                raise Exception(safety_check["error"] or "Query failed safety validation")

            result = None
            row_count = 0
            truncated = False

            if is_destructive:
                # Execute write query
                execution_result = await connector.execute_write_query(query)
                if execution_result.get("success"):
                    result = execution_result.get("result")
                    # Parse row count from result (e.g., "UPDATE 5" -> 5)
                    result_str = str(result)
                    import re
                    match = re.search(r'\d+', result_str)
                    if match:
                        row_count = int(match.group())
                else:
                    raise Exception(execution_result.get("error"))
            else:
                # Execute read query
                execution_result = await connector.execute_query(query)
                if execution_result.get("success"):
                    result = execution_result.get("rows", [])
                    row_count = execution_result.get("row_count", 0)
                    truncated = execution_result.get("truncated", False)
                else:
                    raise Exception(execution_result.get("error"))

            # Log query execution
            execution_time_ms = int((time.time() - start_time) * 1000)

            query_log = QueryLog(
                id=str(uuid.uuid4()),
                user_id=user_id,
                connection_id=connection.id,
                query_text=query,
                query_type=query_type,
                is_destructive=is_destructive,
                execution_time_ms=execution_time_ms,
                row_count=row_count,
                status="success",
            )
            db.add(query_log)
            await db.commit()

            activity_service.log_event(
                action="query.executed",
                status="success",
                user_id=user_id,
                resource_type="database_connection",
                resource_id=connection.id,
                details={
                    "connection_name": connection.name,
                    "database_name": connection.database_name,
                    "db_type": connection.db_type.value,
                    "query_type": query_type,
                    "is_destructive": is_destructive,
                    "row_count": row_count,
                    "execution_time_ms": execution_time_ms,
                    "truncated": truncated,
                    "query_preview": activity_service.preview_sql(query),
                },
            )

            return {
                "success": True,
                "result": result,
                "row_count": row_count,
                "execution_time_ms": execution_time_ms,
                "is_destructive": is_destructive,
                "truncated": truncated,
            }

        except Exception as e:
            execution_time_ms = int((time.time() - start_time) * 1000)
            raw_error = str(e)
            user_error = error_message_service.query_failed_message(raw_error)

            # Log failed query
            query_log = QueryLog(
                id=str(uuid.uuid4()),
                user_id=user_id,
                connection_id=connection.id,
                query_text=query,
                query_type=query_safety.check_query_safety(query)[
                    "query_type"],
                is_destructive=query_safety.check_query_safety(query)[
                    "is_destructive"],
                execution_time_ms=execution_time_ms,
                error_message=raw_error,
                status="error",
            )
            db.add(query_log)
            await db.commit()

            logger.error(f"Query execution error: {raw_error}")
            activity_service.log_event(
                action="query.executed",
                status="error",
                user_id=user_id,
                resource_type="database_connection",
                resource_id=connection.id,
                details={
                    "connection_name": connection.name,
                    "database_name": connection.database_name,
                    "db_type": connection.db_type.value,
                    "query_type": query_log.query_type,
                    "is_destructive": query_log.is_destructive,
                    "execution_time_ms": execution_time_ms,
                    "query_preview": activity_service.preview_sql(query),
                    "user_error": user_error,
                    "raw_error": raw_error,
                },
            )
            return {
                "success": False,
                "error": user_error,
                "raw_error": raw_error,
                "execution_time_ms": execution_time_ms,
            }


# Singleton instance
query_execution_service = QueryExecutionService()
