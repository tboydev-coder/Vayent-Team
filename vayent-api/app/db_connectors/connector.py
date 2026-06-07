"""Database connector for user-connected databases."""
import asyncio
import ipaddress
import logging
import ssl
from typing import Dict, Any

from app.config import (
    CONNECTED_DATABASE_TLS_MODES,
    get_settings,
    normalize_connected_database_ssl_mode,
)

logger = logging.getLogger(__name__)


class UnsafeDatabaseTargetError(ValueError):
    """Raised when a configured source database host is not allowed."""


def _normalize_host(host: str) -> str:
    return host.strip().strip("[]").rstrip(".").lower()


def _matches_host_suffix(host: str, suffixes: list[str]) -> bool:
    for suffix in suffixes:
        normalized_suffix = suffix.strip().lower().lstrip(".")
        if not normalized_suffix:
            continue
        if host == normalized_suffix or host.endswith(f".{normalized_suffix}"):
            return True
    return False


def validate_database_target(host: str) -> None:
    """Validate a user-supplied database host before opening a socket."""
    settings = get_settings()
    normalized_host = _normalize_host(host)

    if not normalized_host:
        raise UnsafeDatabaseTargetError("Database host is required")

    if normalized_host in {item.lower() for item in settings.blocked_database_hosts}:
        raise UnsafeDatabaseTargetError(
            "This database host is blocked by server policy")

    try:
        address = ipaddress.ip_address(normalized_host)
    except ValueError:
        address = None

    if address is not None:
        is_private_target = (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        )
        if is_private_target and not settings.allow_private_database_hosts:
            raise UnsafeDatabaseTargetError(
                "Private, local, link-local, reserved, and unspecified database hosts are disabled by server policy"
            )
    elif normalized_host in {"localhost", "localhost.localdomain"}:
        if not settings.allow_private_database_hosts:
            raise UnsafeDatabaseTargetError(
                "Local database hosts are disabled by server policy")

    allowed_suffixes = list(settings.allowed_database_host_suffixes)
    if allowed_suffixes and not _matches_host_suffix(normalized_host, allowed_suffixes):
        raise UnsafeDatabaseTargetError(
            "Database host is outside the configured allowlist")


def _quote_postgres_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_mysql_identifier(identifier: str) -> str:
    return "`" + identifier.replace("`", "``") + "`"


class DatabaseConnector:
    """Base class for database connections."""

    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        username: str,
        password: str,
        ssl_mode: str | None = None,
    ):
        validate_database_target(host)
        self.host = host
        self.port = port
        self.database = database
        self.username = username
        self.password = password
        self.settings = get_settings()
        self.ssl_mode = (
            normalize_connected_database_ssl_mode(ssl_mode)
            or self.settings.default_connected_database_ssl_mode
        )

    def _ssl_context(self) -> ssl.SSLContext | None:
        if self.ssl_mode not in CONNECTED_DATABASE_TLS_MODES:
            return None
        return ssl.create_default_context()

    async def test_connection_with_details(self) -> Dict[str, Any]:
        """Test database connection and return structured result."""
        raise NotImplementedError

    async def test_connection(self) -> bool:
        """Compatibility wrapper that returns only success."""
        result = await self.test_connection_with_details()
        return bool(result.get("success"))

    async def execute_query(self, query: str) -> Dict[str, Any]:
        """Execute a query and return results."""
        raise NotImplementedError

    async def execute_write_query(self, query: str) -> Dict[str, Any]:
        """Execute a write query and return results."""
        raise NotImplementedError

    async def get_schema(self) -> Dict[str, Any]:
        """Get full database schema."""
        raise NotImplementedError


class PostgreSQLConnector(DatabaseConnector):
    """Connector for PostgreSQL databases."""

    def _connection_kwargs(self, *, ssl_mode: str) -> dict[str, Any]:
        """Build asyncpg connection args with an explicit SSL posture."""
        return {
            "user": self.username,
            "password": self.password,
            "database": self.database,
            "host": self.host,
            "port": self.port,
            "timeout": 10,
            "command_timeout": self.settings.query_timeout_seconds,
            "ssl": ssl_mode,
        }

    @staticmethod
    def _should_retry_with_required_ssl(exc: Exception) -> bool:
        message = str(exc).lower()
        return (
            "no encryption" in message
            or "ssl off" in message
            or ("pg_hba.conf" in message and "encrypt" in message)
        )

    async def _connect(self):
        """Create a PostgreSQL connection with the configured safety posture."""
        import asyncpg

        ssl_mode = self.ssl_mode
        try:
            conn = await asyncpg.connect(**self._connection_kwargs(ssl_mode=ssl_mode))
        except Exception as exc:
            if (
                ssl_mode != "prefer"
                or not self._should_retry_with_required_ssl(exc)
            ):
                raise

            logger.info(
                "PostgreSQL server rejected an unencrypted connection; retrying with required TLS"
            )
            conn = await asyncpg.connect(**self._connection_kwargs(ssl_mode="require"))

        if self.settings.connected_database_read_only:
            await conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        return conn

    async def test_connection_with_details(self) -> Dict[str, Any]:
        """Test PostgreSQL connection."""
        try:
            conn = await self._connect()
            await conn.close()
            logger.info(
                f"PostgreSQL connection test successful to {self.host}")
            return {
                "success": True,
            }
        except Exception as e:
            logger.error(f"PostgreSQL connection test failed: {e}")
            return {
                "success": False,
                "error": str(e),
            }

    async def execute_query(self, query: str) -> Dict[str, Any]:
        """Execute query on PostgreSQL."""
        try:
            conn = await self._connect()

            try:
                result_rows = []
                truncated = False
                max_rows = self.settings.max_result_rows

                async with conn.transaction(readonly=True):
                    async for row in conn.cursor(query, prefetch=min(max_rows + 1, 1000)):
                        if len(result_rows) >= max_rows:
                            truncated = True
                            break
                        result_rows.append(dict(row))

                return {
                    "success": True,
                    "rows": result_rows,
                    "row_count": len(result_rows),
                    "truncated": truncated,
                }
            finally:
                await conn.close()

        except Exception as e:
            logger.error(f"PostgreSQL query execution error: {e}")
            return {
                "success": False,
                "error": str(e),
                "rows": [],
            }

    async def execute_write_query(self, query: str) -> Dict[str, Any]:
        """Execute write query (INSERT, UPDATE, DELETE) on PostgreSQL."""
        try:
            if self.settings.connected_database_read_only:
                return {
                    "success": False,
                    "error": "Write queries are disabled by server read-only policy",
                }

            conn = await self._connect()

            try:
                result = await conn.execute(query)
                # Result is like "UPDATE 5" or "INSERT 0 3"
                return {
                    "success": True,
                    "result": result,
                }
            finally:
                await conn.close()

        except Exception as e:
            logger.error(f"PostgreSQL write query error: {e}")
            return {
                "success": False,
                "error": str(e),
            }

    async def get_schema(self) -> Dict[str, Any]:
        """Get PostgreSQL database schema."""
        try:
            conn = await self._connect()

            try:
                tables_query = """
                    SELECT
                        c.relname AS table_name,
                        GREATEST(c.reltuples::bigint, 0) AS row_count
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public'
                      AND c.relkind = 'r'
                    ORDER BY c.relname
                """
                tables = await conn.fetch(tables_query)

                columns_query = """
                    SELECT
                        table_name,
                        column_name,
                        data_type,
                        is_nullable
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                    ORDER BY table_name, ordinal_position
                """
                columns = await conn.fetch(columns_query)

                pk_query = """
                    SELECT
                        c.relname AS table_name,
                        a.attname AS column_name
                    FROM pg_index i
                    JOIN pg_class c ON c.oid = i.indrelid
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    JOIN pg_attribute a
                      ON a.attrelid = i.indrelid
                     AND a.attnum = ANY(i.indkey)
                    WHERE n.nspname = 'public'
                      AND i.indisprimary
                """
                pk_rows = await conn.fetch(pk_query)

                fk_query = """
                    SELECT
                        tc.table_name,
                        kcu.column_name,
                        ccu.table_name AS foreign_table_name,
                        ccu.column_name AS foreign_column_name
                    FROM information_schema.table_constraints AS tc
                    JOIN information_schema.key_column_usage AS kcu
                      ON tc.constraint_name = kcu.constraint_name
                     AND tc.table_schema = kcu.table_schema
                    JOIN information_schema.constraint_column_usage AS ccu
                      ON ccu.constraint_name = tc.constraint_name
                     AND ccu.table_schema = tc.table_schema
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                      AND tc.table_schema = 'public'
                """
                fk_rows = await conn.fetch(fk_query)

                columns_by_table: dict[str, list[dict[str, Any]]] = {}
                for column in columns:
                    columns_by_table.setdefault(column["table_name"], []).append(dict(column))

                pk_columns = {
                    (row["table_name"], row["column_name"])
                    for row in pk_rows
                }
                foreign_keys = {
                    (row["table_name"], row["column_name"]): (
                        f'{row["foreign_table_name"]}.{row["foreign_column_name"]}'
                    )
                    for row in fk_rows
                }

                schema_info = {"tables": []}
                for table_row in tables:
                    table_name = table_row["table_name"]
                    table_columns = columns_by_table.get(table_name, [])

                    table_info = {
                        "name": table_name,
                        "row_count": int(table_row["row_count"] or 0),
                        "columns": [
                            {
                                "name": col["column_name"],
                                "type": col["data_type"],
                                "nullable": col["is_nullable"] == "YES",
                                "primary_key": (
                                    table_name,
                                    col["column_name"],
                                ) in pk_columns,
                                "foreign_key": (
                                    table_name,
                                    col["column_name"],
                                ) in foreign_keys,
                                "foreign_key_reference": foreign_keys.get(
                                    (table_name, col["column_name"])
                                ),
                            }
                            for col in table_columns
                        ],
                    }

                    schema_info["tables"].append(table_info)

                return schema_info

            finally:
                await conn.close()

        except Exception as e:
            logger.error(f"PostgreSQL schema retrieval error: {e}")
            raise


class MySQLConnector(DatabaseConnector):
    """Connector for MySQL databases."""

    async def _connect(self, *, autocommit: bool | None = None):
        """Create a MySQL connection using args supported by aiomysql."""
        import aiomysql

        connection_kwargs = {
            "host": self.host,
            "port": self.port,
            "user": self.username,
            "password": self.password,
            "db": self.database,
            "connect_timeout": 10,
        }
        if autocommit is not None:
            connection_kwargs["autocommit"] = autocommit
        ssl_context = self._ssl_context()
        if ssl_context is not None:
            connection_kwargs["ssl"] = ssl_context

        conn = await aiomysql.connect(**connection_kwargs)
        if self.settings.connected_database_read_only:
            async with conn.cursor() as cursor:
                await cursor.execute("SET SESSION TRANSACTION READ ONLY")
        return conn

    async def _run_with_timeout(self, awaitable):
        """Apply the configured query timeout to long-running MySQL calls."""
        return await asyncio.wait_for(
            awaitable,
            timeout=self.settings.query_timeout_seconds,
        )

    async def test_connection_with_details(self) -> Dict[str, Any]:
        """Test MySQL connection."""
        try:
            conn = await self._connect()
            conn.close()
            logger.info(f"MySQL connection test successful to {self.host}")
            return {
                "success": True,
            }
        except Exception as e:
            logger.error(f"MySQL connection test failed: {e}")
            return {
                "success": False,
                "error": str(e),
            }

    async def execute_query(self, query: str) -> Dict[str, Any]:
        """Execute query on MySQL."""
        try:
            import aiomysql

            conn = await self._connect()

            try:
                async with conn.cursor(aiomysql.DictCursor) as cursor:
                    await self._run_with_timeout(
                        cursor.execute(query),
                    )
                    result_rows = await self._run_with_timeout(
                        cursor.fetchmany(self.settings.max_result_rows + 1),
                    )
                    truncated = len(result_rows) > self.settings.max_result_rows
                    if truncated:
                        result_rows = result_rows[: self.settings.max_result_rows]

                    return {
                        "success": True,
                        "rows": result_rows,
                        "row_count": len(result_rows),
                        "truncated": truncated,
                    }
            finally:
                conn.close()

        except Exception as e:
            logger.error(f"MySQL query execution error: {e}")
            return {
                "success": False,
                "error": str(e),
                "rows": [],
            }

    async def execute_write_query(self, query: str) -> Dict[str, Any]:
        """Execute write query (INSERT, UPDATE, DELETE) on MySQL."""
        try:
            if self.settings.connected_database_read_only:
                return {
                    "success": False,
                    "error": "Write queries are disabled by server read-only policy",
                }

            import aiomysql

            conn = await self._connect(autocommit=False)

            try:
                async with conn.cursor() as cursor:
                    await self._run_with_timeout(
                        cursor.execute(query),
                    )
                    await self._run_with_timeout(conn.commit())
                    return {
                        "success": True,
                        "result": f"{cursor.rowcount} rows affected",
                    }
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"MySQL write query error: {e}")
            return {
                "success": False,
                "error": str(e),
            }

    async def get_schema(self) -> Dict[str, Any]:
        """Get MySQL database schema."""
        try:
            import aiomysql

            conn = await self._connect()

            try:
                async with conn.cursor(aiomysql.DictCursor) as cursor:
                    await self._run_with_timeout(
                        cursor.execute(
                            "SELECT TABLE_NAME, COALESCE(TABLE_ROWS, 0) AS TABLE_ROWS "
                            "FROM information_schema.TABLES "
                            "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE' "
                            "ORDER BY TABLE_NAME",
                            (self.database,),
                        )
                    )
                    tables = await self._run_with_timeout(cursor.fetchall())

                    await self._run_with_timeout(
                        cursor.execute(
                            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, "
                            "COLUMN_KEY FROM information_schema.COLUMNS "
                            "WHERE TABLE_SCHEMA = %s "
                            "ORDER BY TABLE_NAME, ORDINAL_POSITION",
                            (self.database,),
                        )
                    )
                    columns = await self._run_with_timeout(cursor.fetchall())

                    await self._run_with_timeout(
                        cursor.execute(
                            "SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, "
                            "REFERENCED_COLUMN_NAME "
                            "FROM information_schema.KEY_COLUMN_USAGE "
                            "WHERE TABLE_SCHEMA = %s "
                            "AND REFERENCED_TABLE_NAME IS NOT NULL",
                            (self.database,),
                        )
                    )
                    fk_rows = await self._run_with_timeout(cursor.fetchall())

                    columns_by_table: dict[str, list[dict[str, Any]]] = {}
                    for column in columns:
                        columns_by_table.setdefault(column["TABLE_NAME"], []).append(column)

                    foreign_keys = {
                        (row["TABLE_NAME"], row["COLUMN_NAME"]): (
                            f'{row["REFERENCED_TABLE_NAME"]}.{row["REFERENCED_COLUMN_NAME"]}'
                        )
                        for row in fk_rows
                    }

                    schema_info = {"tables": []}
                    for table_row in tables:
                        table_name = table_row["TABLE_NAME"]
                        table_columns = columns_by_table.get(table_name, [])

                        table_info = {
                            "name": table_name,
                            "row_count": int(table_row.get("TABLE_ROWS") or 0),
                            "columns": [
                                {
                                    "name": col["COLUMN_NAME"],
                                    "type": col["COLUMN_TYPE"],
                                    "nullable": col["IS_NULLABLE"] == "YES",
                                    "primary_key": col["COLUMN_KEY"] == "PRI",
                                    "foreign_key": (
                                        table_name,
                                        col["COLUMN_NAME"],
                                    ) in foreign_keys,
                                    "foreign_key_reference": foreign_keys.get(
                                        (table_name, col["COLUMN_NAME"])
                                    ),
                                }
                                for col in table_columns
                            ],
                        }
                        schema_info["tables"].append(table_info)

                return schema_info

            finally:
                conn.close()

        except Exception as e:
            logger.error(f"MySQL schema retrieval error: {e}")
            raise


def get_connector(
    db_type: str,
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
    ssl_mode: str | None = None,
) -> DatabaseConnector:
    """Factory function to get appropriate database connector."""
    if db_type.lower() == "postgresql":
        return PostgreSQLConnector(host, port, database, username, password, ssl_mode)
    elif db_type.lower() == "mysql":
        return MySQLConnector(host, port, database, username, password, ssl_mode)
    else:
        raise ValueError(f"Unsupported database type: {db_type}")
