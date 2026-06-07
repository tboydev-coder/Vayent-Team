"""Schema discovery and metadata management service."""
import logging
import time
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.db_connectors.connector import get_connector
from app.models import (
    ColumnMetadata,
    DatabaseConnection,
    DatabaseSchema,
    SchemaAnnotation,
    TableMetadata,
)
from app.schemas import SchemaAnnotationUpsertRequest
from app.services.error_message_service import error_message_service

logger = logging.getLogger(__name__)


class SchemaDiscoveryService:
    """Service for discovering and managing database schemas."""

    _SCHEMA_TEXT_CACHE_TTL_SECONDS = 300
    _SCHEMA_TEXT_CACHE_MAX_ITEMS = 128

    def __init__(self) -> None:
        self._schema_text_cache: dict[str, tuple[float, str]] = {}

    def _schema_select(self):
        return select(DatabaseSchema).options(
            selectinload(DatabaseSchema.tables).selectinload(TableMetadata.columns)
        )

    @staticmethod
    def _annotation_key(
        target_type: str,
        table_name: str = "",
        column_name: str = "",
    ) -> tuple[str, str, str]:
        return (
            target_type,
            table_name or "",
            column_name or "",
        )

    @staticmethod
    def _first_present(*values: str | None) -> str | None:
        for value in values:
            if value is not None and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _split_foreign_key_reference(reference: str | None) -> tuple[str | None, str | None]:
        if not reference:
            return None, None

        table_name, separator, column_name = reference.partition(".")
        if not separator:
            return reference, None
        return table_name, column_name

    def _build_annotation_map(
        self,
        annotations: list[SchemaAnnotation],
    ) -> dict[tuple[str, str, str], SchemaAnnotation]:
        return {
            self._annotation_key(
                annotation.target_type,
                annotation.table_name,
                annotation.column_name,
            ): annotation
            for annotation in annotations
        }

    def _build_relationships(
        self,
        tables: list[TableMetadata],
    ) -> list[dict[str, str]]:
        relationships: list[dict[str, str]] = []

        for table in tables:
            for column in table.columns:
                if not column.is_foreign_key or not column.foreign_key_reference:
                    continue

                target_table_name, target_column_name = self._split_foreign_key_reference(
                    column.foreign_key_reference
                )
                if not target_table_name or not target_column_name:
                    continue

                relationships.append(
                    {
                        "id": (
                            f"{table.table_name}.{column.column_name}"
                            f"->{target_table_name}.{target_column_name}"
                        ),
                        "source_table_name": table.table_name,
                        "source_column_name": column.column_name,
                        "target_table_name": target_table_name,
                        "target_column_name": target_column_name,
                    }
                )

        relationships.sort(
            key=lambda item: (
                item["source_table_name"].lower(),
                item["source_column_name"].lower(),
                item["target_table_name"].lower(),
                item["target_column_name"].lower(),
            )
        )
        return relationships

    def _serialize_schema(
        self,
        schema: DatabaseSchema,
        annotation_map: dict[tuple[str, str, str], SchemaAnnotation],
    ) -> dict[str, Any]:
        schema_annotation = annotation_map.get(self._annotation_key("schema"))
        relationships = self._build_relationships(schema.tables)

        tables_payload: list[dict[str, Any]] = []
        for table in schema.tables:
            table_annotation = annotation_map.get(
                self._annotation_key("table", table.table_name)
            )
            columns_payload: list[dict[str, Any]] = []

            for column in table.columns:
                column_annotation = annotation_map.get(
                    self._annotation_key("column", table.table_name, column.column_name)
                )
                columns_payload.append(
                    {
                        "id": column.id,
                        "column_name": column.column_name,
                        "nickname": column_annotation.nickname if column_annotation else None,
                        "data_type": column.data_type,
                        "is_nullable": column.is_nullable,
                        "is_primary_key": column.is_primary_key,
                        "is_foreign_key": column.is_foreign_key,
                        "foreign_key_reference": column.foreign_key_reference,
                        "column_description": self._first_present(
                            column_annotation.description if column_annotation else None,
                            column.column_description,
                        ),
                        "created_at": column.created_at,
                    }
                )

            tables_payload.append(
                {
                    "id": table.id,
                    "table_name": table.table_name,
                    "table_description": self._first_present(
                        table_annotation.description if table_annotation else None,
                        table.table_description,
                    ),
                    "row_count": table.row_count,
                    "columns": columns_payload,
                    "created_at": table.created_at,
                    "updated_at": table.updated_at,
                }
            )

        return {
            "id": schema.id,
            "schema_name": schema.schema_name,
            "schema_description": self._first_present(
                schema_annotation.description if schema_annotation else None,
                schema.schema_description,
            ),
            "tables": tables_payload,
            "relationships": relationships,
            "created_at": schema.created_at,
            "updated_at": schema.updated_at,
        }

    def _format_schema_for_ai(
        self,
        schema: DatabaseSchema,
        annotation_map: dict[tuple[str, str, str], SchemaAnnotation],
    ) -> str:
        payload = self._serialize_schema(schema, annotation_map)
        text_parts = [
            f"Database Schema: {payload['schema_name']}",
            "Naming Guidance: Raw table and column names are SQL identifiers only. "
            "For user-facing answers, dashboard titles, chart labels, and summaries, "
            "prefer nicknames, descriptions, or human-readable labels. If no nickname "
            "exists, translate snake_case/camelCase identifiers into plain language.",
        ]

        if payload["schema_description"]:
            text_parts.append(f"Schema Description: {payload['schema_description']}")

        text_parts.append("")

        for table in payload["tables"]:
            table_display_label = str(table["table_name"]).replace("_", " ").strip().title()
            text_parts.append(f"Table: {table['table_name']}")
            text_parts.append(f"  User-facing label: {table_display_label}")
            text_parts.append(
                f"  Description: {table['table_description'] or 'No description'}"
            )
            text_parts.append(f"  Rows: {table['row_count'] or 'Unknown'}")
            text_parts.append("  Columns:")

            for column in table["columns"]:
                nullable = "nullable" if column["is_nullable"] else "not null"
                pk = " PRIMARY KEY" if column["is_primary_key"] else ""
                fk = (
                    f" FK: {column['foreign_key_reference']}"
                    if column["is_foreign_key"]
                    else ""
                )
                nickname = (
                    f" [nickname: {column['nickname']}]"
                    if column["nickname"]
                    else ""
                )
                display_label = (
                    column["nickname"]
                    or str(column["column_name"]).replace("_", " ").strip().title()
                )

                text_parts.append(
                    "    - "
                    f"{column['column_name']}{nickname} ({column['data_type']}) "
                    f"{nullable}{pk}{fk}"
                )
                text_parts.append(f"      User-facing label: {display_label}")
                if column["column_description"]:
                    text_parts.append(
                        f"      Description: {column['column_description']}"
                    )

            related_relationships = [
                relationship
                for relationship in payload["relationships"]
                if relationship["source_table_name"] == table["table_name"]
            ]
            if related_relationships:
                text_parts.append("  Relationships:")
                for relationship in related_relationships:
                    text_parts.append(
                        "    - "
                        f"{relationship['source_column_name']} -> "
                        f"{relationship['target_table_name']}."
                        f"{relationship['target_column_name']}"
                    )

            text_parts.append("")

        if payload["relationships"]:
            text_parts.append("Relationship Summary:")
            for relationship in payload["relationships"]:
                text_parts.append(
                    "  - "
                    f"{relationship['source_table_name']}."
                    f"{relationship['source_column_name']} -> "
                    f"{relationship['target_table_name']}."
                    f"{relationship['target_column_name']}"
                )

        return "\n".join(text_parts)

    def _get_cached_schema_text(self, schema_id: str) -> str | None:
        cached = self._schema_text_cache.get(schema_id)
        if cached is None:
            return None

        cached_at, schema_text = cached
        if time.monotonic() - cached_at > self._SCHEMA_TEXT_CACHE_TTL_SECONDS:
            self._schema_text_cache.pop(schema_id, None)
            return None

        return schema_text

    def _cache_schema_text(self, schema_id: str, schema_text: str) -> None:
        self._schema_text_cache[schema_id] = (time.monotonic(), schema_text)
        if len(self._schema_text_cache) <= self._SCHEMA_TEXT_CACHE_MAX_ITEMS:
            return

        oldest_schema_id = min(
            self._schema_text_cache,
            key=lambda item: self._schema_text_cache[item][0],
        )
        self._schema_text_cache.pop(oldest_schema_id, None)

    def _invalidate_schema_cache(self, schema_id: str | None) -> None:
        if schema_id:
            self._schema_text_cache.pop(schema_id, None)

    def _ensure_annotation_target_exists(
        self,
        schema: DatabaseSchema,
        annotation_data: SchemaAnnotationUpsertRequest,
    ) -> None:
        if annotation_data.target_type == "schema":
            return

        table = next(
            (
                table_item
                for table_item in schema.tables
                if table_item.table_name == annotation_data.table_name
            ),
            None,
        )
        if table is None:
            raise ValueError(
                f"Table '{annotation_data.table_name}' does not exist in the synced schema."
            )

        if annotation_data.target_type == "table":
            return

        column = next(
            (
                column_item
                for column_item in table.columns
                if column_item.column_name == annotation_data.column_name
            ),
            None,
        )
        if column is None:
            raise ValueError(
                "Column "
                f"'{annotation_data.table_name}.{annotation_data.column_name}' "
                "does not exist in the synced schema."
            )

    async def discover_and_sync_schema(
        self,
        connection: DatabaseConnection,
        username_decrypted: str,
        password_decrypted: str,
        db: AsyncSession,
    ) -> DatabaseSchema:
        """
        Discover schema from connected database and store metadata.

        Returns: DatabaseSchema object with all metadata
        """
        connector = get_connector(
            db_type=connection.db_type.value,
            host=connection.host,
            port=connection.port,
            database=connection.database_name,
            username=username_decrypted,
            password=password_decrypted,
            ssl_mode=getattr(connection, "ssl_mode", None),
        )

        try:
            connection_check = await connector.test_connection_with_details()
            if not connection_check.get("success"):
                raise Exception(
                    error_message_service.connection_failed_message(
                        raw_error=connection_check.get("error", "Unknown connection error"),
                        host=connection.host,
                        port=connection.port,
                        database_name=connection.database_name,
                    )
                )

            raw_schema = await connector.get_schema()

            result = await db.execute(
                select(DatabaseSchema).where(DatabaseSchema.connection_id == connection.id)
            )
            old_schema = result.scalar_one_or_none()
            if old_schema:
                self._invalidate_schema_cache(old_schema.id)
                await db.delete(old_schema)

            schema = DatabaseSchema(
                id=str(uuid.uuid4()),
                connection_id=connection.id,
                schema_name=connection.database_name,
                raw_schema_metadata=raw_schema,
            )
            db.add(schema)
            await db.flush()

            for table_info in raw_schema.get("tables", []):
                table_meta = TableMetadata(
                    id=str(uuid.uuid4()),
                    schema_id=schema.id,
                    table_name=table_info.get("name"),
                    row_count=table_info.get("row_count"),
                )
                db.add(table_meta)
                await db.flush()

                for col_info in table_info.get("columns", []):
                    column_meta = ColumnMetadata(
                        id=str(uuid.uuid4()),
                        table_id=table_meta.id,
                        column_name=col_info.get("name"),
                        data_type=col_info.get("type"),
                        is_nullable=col_info.get("nullable", True),
                        is_primary_key=col_info.get("primary_key", False),
                        is_foreign_key=col_info.get("foreign_key", False),
                        foreign_key_reference=col_info.get("foreign_key_reference"),
                    )
                    db.add(column_meta)

            connection.last_synced_at = datetime.utcnow()

            await db.commit()
            logger.info("Schema synced for connection %s", connection.id)

            return schema

        except Exception as exc:
            logger.error("Schema discovery error for connection %s: %s", connection.id, exc)
            await db.rollback()
            raise

    async def get_schema_by_id(
        self,
        schema_id: str,
        db: AsyncSession,
    ) -> DatabaseSchema | None:
        result = await db.execute(
            self._schema_select().where(DatabaseSchema.id == schema_id)
        )
        return result.scalar_one_or_none()

    async def get_connection_schema(
        self,
        connection_id: str,
        db: AsyncSession,
    ) -> DatabaseSchema | None:
        """Get latest schema for a connection."""
        result = await db.execute(
            self._schema_select()
            .where(DatabaseSchema.connection_id == connection_id)
            .order_by(DatabaseSchema.updated_at.desc())
        )
        return result.scalar_one_or_none()

    async def get_connection_annotations(
        self,
        connection_id: str,
        db: AsyncSession,
    ) -> list[SchemaAnnotation]:
        result = await db.execute(
            select(SchemaAnnotation)
            .where(SchemaAnnotation.connection_id == connection_id)
            .order_by(
                SchemaAnnotation.target_type.asc(),
                SchemaAnnotation.table_name.asc(),
                SchemaAnnotation.column_name.asc(),
            )
        )
        return list(result.scalars().all())

    async def get_connection_schema_response(
        self,
        connection_id: str,
        db: AsyncSession,
    ) -> dict[str, Any] | None:
        schema = await self.get_connection_schema(connection_id, db)
        if not schema:
            return None

        annotations = await self.get_connection_annotations(connection_id, db)
        return self._serialize_schema(schema, self._build_annotation_map(annotations))

    async def get_schema_for_rag(self, schema_id: str, db: AsyncSession) -> str:
        """
        Get schema in RAG-friendly text format.
        Returns formatted schema as string for embedding.
        """
        cached_schema_text = self._get_cached_schema_text(schema_id)
        if cached_schema_text is not None:
            return cached_schema_text

        schema = await self.get_schema_by_id(schema_id, db)

        if not schema:
            raise Exception(f"Schema not found: {schema_id}")

        annotations = await self.get_connection_annotations(schema.connection_id, db)
        schema_text = self._format_schema_for_ai(
            schema,
            self._build_annotation_map(annotations),
        )
        self._cache_schema_text(schema_id, schema_text)
        return schema_text

    async def upsert_annotation(
        self,
        connection_id: str,
        schema: DatabaseSchema,
        annotation_data: SchemaAnnotationUpsertRequest,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Create, update, or clear a durable schema annotation."""
        self._ensure_annotation_target_exists(schema, annotation_data)

        table_name = annotation_data.table_name or ""
        column_name = annotation_data.column_name or ""

        try:
            result = await db.execute(
                select(SchemaAnnotation).where(
                    SchemaAnnotation.connection_id == connection_id,
                    SchemaAnnotation.target_type == annotation_data.target_type,
                    SchemaAnnotation.table_name == table_name,
                    SchemaAnnotation.column_name == column_name,
                )
            )
            annotation = result.scalar_one_or_none()

            if annotation_data.nickname is None and annotation_data.description is None:
                if annotation:
                    await db.delete(annotation)
            else:
                if annotation is None:
                    annotation = SchemaAnnotation(
                        id=str(uuid.uuid4()),
                        connection_id=connection_id,
                        target_type=annotation_data.target_type,
                        table_name=table_name,
                        column_name=column_name,
                    )
                    db.add(annotation)

                annotation.nickname = annotation_data.nickname
                annotation.description = annotation_data.description
                annotation.updated_at = datetime.utcnow()

            await db.commit()
            self._invalidate_schema_cache(schema.id)
            response = await self.get_connection_schema_response(connection_id, db)
            if response is None:
                raise ValueError("Schema not found for connection.")
            return response
        except Exception:
            await db.rollback()
            raise


# Singleton instance
schema_discovery_service = SchemaDiscoveryService()
