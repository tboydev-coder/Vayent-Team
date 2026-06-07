"""Database connections API routes."""
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.database import get_db_session
from app.models import User
from app.auth.dependencies import get_current_active_user
from app.db_connectors.connector import UnsafeDatabaseTargetError, get_connector
from app.schemas import (
    ConnectedSourceListResponse,
    ConnectedSourceResponse,
    DatabaseConnectionCreate,
    DatabaseConnectionResponse,
    DatabaseSchemaResponse,
    SchemaAnnotationUpsertRequest,
    SourceRenameRequest,
    SpreadsheetLinkCreate,
    SpreadsheetSourceResponse,
    SyncSchemaResponse,
    SyncSourceResponse,
)
from app.services.db_connection_service import db_connection_service
from app.services.error_message_service import error_message_service
from app.services.schema_discovery_service import schema_discovery_service
from app.services.spreadsheet_service import (
    SpreadsheetValidationError,
    spreadsheet_service,
)
from app.rag.rag_service import rag_service
from app.services.activity_service import activity_service

router = APIRouter(prefix="/connections", tags=["Database Connections"])
logger = logging.getLogger(__name__)


async def _add_schema_to_rag_best_effort(
    *,
    schema_id: str,
    schema_name: str,
    schema_text: str,
    current_user: User,
    connection_id: str,
) -> None:
    """Index schema context without making DB connection setup depend on RAG."""
    try:
        await rag_service.add_schema_to_rag(
            schema_id=schema_id,
            schema_name=schema_name,
            schema_text=schema_text,
        )
    except Exception as exc:
        logger.warning(
            "Schema RAG indexing failed for connection %s: %s",
            connection_id,
            exc,
        )
        activity_service.log_event(
            action="connection.schema_index_failed",
            status="warning",
            user=current_user,
            resource_type="database_connection",
            resource_id=connection_id,
            details=activity_service.exception_details(exc),
        )


async def _update_schema_in_rag_best_effort(
    *,
    schema_id: str,
    schema_name: str,
    schema_text: str,
    current_user: User,
    connection_id: str,
) -> bool:
    """Return false when indexing fails but schema sync itself succeeded."""
    try:
        await rag_service.update_schema_in_rag(
            schema_id=schema_id,
            schema_name=schema_name,
            schema_text=schema_text,
        )
        return True
    except Exception as exc:
        logger.warning(
            "Schema RAG refresh failed for connection %s: %s",
            connection_id,
            exc,
        )
        activity_service.log_event(
            action="connection.schema_index_failed",
            status="warning",
            user=current_user,
            resource_type="database_connection",
            resource_id=connection_id,
            details=activity_service.exception_details(exc),
        )
        return False


async def _delete_schema_from_rag_best_effort(schema_id: str, connection_id: str) -> None:
    """Do not block connection deletion on vector-store cleanup."""
    try:
        await rag_service.delete_schema_from_rag(schema_id)
    except Exception as exc:
        logger.warning(
            "Failed to delete RAG schema %s for connection %s: %s",
            schema_id,
            connection_id,
            exc,
        )


async def _cleanup_failed_connection_setup(connection_id: str, db: AsyncSession) -> None:
    """Remove a just-created connection when setup fails after persistence."""
    try:
        await db_connection_service.delete_connection(connection_id, db)
    except Exception as exc:
        logger.warning(
            "Failed to clean up connection %s after setup failure: %s",
            connection_id,
            exc,
        )


def _database_source_response(connection) -> ConnectedSourceResponse:
    return ConnectedSourceResponse(
        id=connection.id,
        user_id=connection.user_id,
        name=connection.name,
        source_type="database",
        source_kind=connection.db_type.value,
        status="connected" if connection.is_active else "disconnected",
        status_message=None,
        display_name=connection.name,
        detail=f"{connection.database_name} on {connection.host}:{connection.port}",
        last_synced_at=connection.last_synced_at,
        created_at=connection.created_at,
        updated_at=connection.updated_at,
        metadata={
            "db_type": connection.db_type.value,
            "database_name": connection.database_name,
            "host": connection.host,
            "port": connection.port,
            "ssl_mode": connection.ssl_mode,
        },
    )


def _spreadsheet_source_response(source) -> ConnectedSourceResponse:
    method = source.source_kind.value if hasattr(source.source_kind, "value") else str(source.source_kind)
    detail = source.original_filename or source.source_url or "Spreadsheet"
    return ConnectedSourceResponse(
        id=source.id,
        user_id=source.user_id,
        name=source.name,
        source_type="spreadsheet",
        source_kind=method,
        status=source.status,
        status_message=source.status_message,
        display_name=source.name,
        detail=detail,
        last_synced_at=source.last_synced_at,
        created_at=source.created_at,
        updated_at=source.updated_at,
        metadata={
            "file_type": source.file_type,
            "original_filename": source.original_filename,
            "source_url": source.source_url,
            "source_provider": source.source_provider,
            "tables": (source.raw_schema_metadata or {}).get("tables", []),
            "insight_count": len((source.analysis_metadata or {}).get("insights", [])),
            "recommendation_count": len(
                (source.analysis_metadata or {}).get("recommendations", [])
            ),
        },
    )


async def _index_spreadsheet_source_best_effort(
    *,
    source,
    current_user: User,
) -> None:
    try:
        await rag_service.update_schema_in_rag(
            schema_id=source.id,
            schema_name=source.name,
            schema_text=spreadsheet_service.format_source_for_ai(source),
        )
    except Exception as exc:
        logger.warning(
            "Spreadsheet RAG indexing failed for source %s: %s",
            source.id,
            exc,
        )
        activity_service.log_event(
            action="spreadsheet.index_failed",
            status="warning",
            user=current_user,
            resource_type="spreadsheet_source",
            resource_id=source.id,
            details=activity_service.exception_details(exc),
        )


@router.post("", response_model=DatabaseConnectionResponse, status_code=status.HTTP_201_CREATED)
async def create_connection(
    connection_data: DatabaseConnectionCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Create a new database connection."""
    connection = None
    try:
        test_connector = get_connector(
            db_type=connection_data.db_type,
            host=connection_data.host,
            port=connection_data.port,
            database=connection_data.database_name,
            username=connection_data.username,
            password=connection_data.password,
            ssl_mode=connection_data.ssl_mode,
        )

        connection_check = await test_connector.test_connection_with_details()
        if not connection_check.get("success"):
            detail = error_message_service.connection_failed_message(
                raw_error=connection_check.get("error", "Unknown connection error"),
                host=connection_data.host,
                port=connection_data.port,
                database_name=connection_data.database_name,
            )
            activity_service.log_event(
                action="connection.create_failed",
                status="warning",
                user=current_user,
                resource_type="database_connection",
                details={
                    "connection_name": connection_data.name,
                    "db_type": str(connection_data.db_type),
                    "host": connection_data.host,
                    "port": connection_data.port,
                    "database_name": connection_data.database_name,
                    "error": detail,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=detail,
            )

        # Create connection
        connection = await db_connection_service.create_connection(
            user_id=current_user.id,
            name=connection_data.name,
            db_type=connection_data.db_type,
            host=connection_data.host,
            port=connection_data.port,
            database_name=connection_data.database_name,
            username=connection_data.username,
            password=connection_data.password,
            ssl_mode=connection_data.ssl_mode,
            db=db,
        )

        # Discover and sync schema
        schema = await schema_discovery_service.discover_and_sync_schema(
            connection,
            connection_data.username,
            connection_data.password,
            db,
        )

        schema_text = await schema_discovery_service.get_schema_for_rag(schema.id, db)
        await _add_schema_to_rag_best_effort(
            schema_id=schema.id,
            schema_name=connection.database_name,
            schema_text=schema_text,
            current_user=current_user,
            connection_id=connection.id,
        )

        activity_service.log_event(
            action="connection.created",
            status="success",
            user=current_user,
            resource_type="database_connection",
            resource_id=connection.id,
            details={
                "connection_name": connection.name,
                "db_type": connection.db_type.value,
                "host": connection.host,
                "port": connection.port,
                "database_name": connection.database_name,
                "ssl_mode": connection.ssl_mode,
                "schema_id": schema.id,
            },
        )

        return DatabaseConnectionResponse.model_validate(connection)

    except HTTPException:
        raise
    except UnsafeDatabaseTargetError as e:
        if connection is not None:
            await _cleanup_failed_connection_setup(connection.id, db)

        detail = f"Connection failed: {str(e)}"
        activity_service.log_event(
            action="connection.create_failed",
            status="warning",
            user=current_user,
            resource_type="database_connection",
            details={
                "connection_name": connection_data.name,
                "db_type": str(connection_data.db_type),
                "host": connection_data.host,
                "port": connection_data.port,
                "database_name": connection_data.database_name,
                "error": detail,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
        ) from e
    except Exception as e:
        if connection is not None:
            await _cleanup_failed_connection_setup(connection.id, db)

        detail = str(e)
        if not detail.lower().startswith("connection failed:"):
            detail = (
                "Connection failed: Vayent reached the database, but could not "
                f"sync its schema metadata. {detail}"
            )

        activity_service.log_event(
            action="connection.create_failed",
            status="warning" if connection is not None else "error",
            user=current_user,
            resource_type="database_connection",
            details={
                "connection_name": connection_data.name,
                "db_type": str(connection_data.db_type),
                "host": connection_data.host,
                "port": connection_data.port,
                "database_name": connection_data.database_name,
                "error": detail,
            },
        )
        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
                if connection is not None
                else status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=detail,
        )


@router.get("", response_model=List[DatabaseConnectionResponse])
async def list_connections(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    """List all database connections for current user."""
    connections = await db_connection_service.get_user_connections(
        user_id=current_user.id,
        db=db,
    )

    return [DatabaseConnectionResponse.model_validate(c) for c in connections]


@router.get("/sources", response_model=ConnectedSourceListResponse)
async def list_connected_sources(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    """List all connected database and spreadsheet sources for current user."""
    database_connections = await db_connection_service.get_user_connections(
        user_id=current_user.id,
        db=db,
    )
    spreadsheet_sources = await spreadsheet_service.get_user_sources(
        user_id=current_user.id,
        db=db,
    )
    items = [
        *[_database_source_response(connection) for connection in database_connections],
        *[_spreadsheet_source_response(source) for source in spreadsheet_sources],
    ]
    items.sort(key=lambda item: item.created_at, reverse=True)
    return ConnectedSourceListResponse(items=items)


@router.post(
    "/spreadsheets/upload",
    response_model=SpreadsheetSourceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_spreadsheet_source(
    file: UploadFile = File(...),
    name: str | None = Form(default=None),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Create a spreadsheet source from an uploaded .xlsx, .xls, or .csv file."""
    try:
        content = await file.read()
        source = await spreadsheet_service.create_upload_source(
            user_id=current_user.id,
            name=name or file.filename or "Spreadsheet",
            filename=file.filename,
            content_type=file.content_type,
            content=content,
            db=db,
        )
        await _index_spreadsheet_source_best_effort(
            source=source,
            current_user=current_user,
        )
        activity_service.log_event(
            action="spreadsheet.uploaded",
            status="success",
            user=current_user,
            resource_type="spreadsheet_source",
            resource_id=source.id,
            details={
                "name": source.name,
                "file_type": source.file_type,
                "original_filename": source.original_filename,
            },
        )
        return SpreadsheetSourceResponse.model_validate(source)
    except SpreadsheetValidationError as exc:
        activity_service.log_event(
            action="spreadsheet.upload_failed",
            status="warning",
            user=current_user,
            resource_type="spreadsheet_source",
            details={"filename": file.filename, "error": str(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        activity_service.log_event(
            action="spreadsheet.upload_failed",
            status="error",
            user=current_user,
            resource_type="spreadsheet_source",
            details=activity_service.exception_details(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


@router.post(
    "/spreadsheets/link",
    response_model=SpreadsheetSourceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_spreadsheet_link_source(
    request: SpreadsheetLinkCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Create a spreadsheet source from a public or export URL."""
    try:
        source = await spreadsheet_service.create_link_source(
            user_id=current_user.id,
            name=request.name,
            url=request.url,
            db=db,
        )
        await _index_spreadsheet_source_best_effort(
            source=source,
            current_user=current_user,
        )
        activity_service.log_event(
            action="spreadsheet.link_connected",
            status="success",
            user=current_user,
            resource_type="spreadsheet_source",
            resource_id=source.id,
            details={
                "name": source.name,
                "provider": source.source_provider,
                "file_type": source.file_type,
            },
        )
        return SpreadsheetSourceResponse.model_validate(source)
    except SpreadsheetValidationError as exc:
        activity_service.log_event(
            action="spreadsheet.link_failed",
            status="warning",
            user=current_user,
            resource_type="spreadsheet_source",
            details={"url": request.url, "error": str(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        activity_service.log_event(
            action="spreadsheet.link_failed",
            status="error",
            user=current_user,
            resource_type="spreadsheet_source",
            details=activity_service.exception_details(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


@router.get("/spreadsheets/{source_id}", response_model=SpreadsheetSourceResponse)
async def get_spreadsheet_source(
    source_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Get a spreadsheet source."""
    source = await spreadsheet_service.get_source(source_id, db)
    if not source or source.user_id != current_user.id or not source.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Spreadsheet source not found",
        )
    return SpreadsheetSourceResponse.model_validate(source)


@router.patch("/sources/{source_id}/rename", response_model=ConnectedSourceResponse)
async def rename_connected_source(
    source_id: str,
    request: SourceRenameRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Rename a database or spreadsheet source."""
    connection = await db_connection_service.get_connection(source_id, db)
    if connection and connection.user_id == current_user.id and connection.is_active:
        updated = await db_connection_service.update_connection(
            connection_id=source_id,
            db=db,
            name=request.name,
        )
        activity_service.log_event(
            action="source.renamed",
            status="success",
            user=current_user,
            resource_type="database_connection",
            resource_id=source_id,
            details={"name": request.name},
        )
        return _database_source_response(updated)

    source = await spreadsheet_service.rename_source(
        source_id=source_id,
        user_id=current_user.id,
        name=request.name,
        db=db,
    )
    if not source:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Source not found",
        )
    activity_service.log_event(
        action="source.renamed",
        status="success",
        user=current_user,
        resource_type="spreadsheet_source",
        resource_id=source_id,
        details={"name": request.name},
    )
    return _spreadsheet_source_response(source)


@router.post("/sources/{source_id}/sync", response_model=SyncSourceResponse)
async def sync_connected_source(
    source_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Refresh a database schema or spreadsheet dataset."""
    connection = await db_connection_service.get_connection(source_id, db)
    if connection and connection.user_id == current_user.id and connection.is_active:
        username, password = db_connection_service.decrypt_credentials(connection)
        schema = await schema_discovery_service.discover_and_sync_schema(
            connection,
            username,
            password,
            db,
        )
        schema_text = await schema_discovery_service.get_schema_for_rag(schema.id, db)
        await _update_schema_in_rag_best_effort(
            schema_id=schema.id,
            schema_name=connection.database_name,
            schema_text=schema_text,
            current_user=current_user,
            connection_id=connection.id,
        )
        activity_service.log_event(
            action="source.synced",
            status="success",
            user=current_user,
            resource_type="database_connection",
            resource_id=connection.id,
            details={"source_type": "database", "schema_id": schema.id},
        )
        return SyncSourceResponse(
            message="Database schema synced successfully.",
            source_id=connection.id,
            source_type="database",
            schema_id=schema.id,
        )

    try:
        source = await spreadsheet_service.sync_source(
            source_id=source_id,
            user_id=current_user.id,
            db=db,
        )
        await _index_spreadsheet_source_best_effort(
            source=source,
            current_user=current_user,
        )
        activity_service.log_event(
            action="source.synced",
            status="success",
            user=current_user,
            resource_type="spreadsheet_source",
            resource_id=source.id,
            details={"source_type": "spreadsheet", "source_kind": str(source.source_kind)},
        )
        return SyncSourceResponse(
            message="Spreadsheet synced, reprocessed, and refreshed for AI.",
            source_id=source.id,
            source_type="spreadsheet",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except SpreadsheetValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_connected_source(
    source_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Disconnect a database or spreadsheet source."""
    connection = await db_connection_service.get_connection(source_id, db)
    if connection and connection.user_id == current_user.id:
        schema = await schema_discovery_service.get_connection_schema(source_id, db)
        if schema:
            await _delete_schema_from_rag_best_effort(schema.id, source_id)
        await db_connection_service.delete_connection(source_id, db)
        activity_service.log_event(
            action="source.disconnected",
            status="success",
            user=current_user,
            resource_type="database_connection",
            resource_id=source_id,
            details={"source_type": "database"},
        )
        return None

    disconnected = await spreadsheet_service.disconnect_source(
        source_id=source_id,
        user_id=current_user.id,
        db=db,
    )
    if not disconnected:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Source not found",
        )
    await _delete_schema_from_rag_best_effort(source_id, source_id)
    activity_service.log_event(
        action="source.disconnected",
        status="success",
        user=current_user,
        resource_type="spreadsheet_source",
        resource_id=source_id,
        details={"source_type": "spreadsheet"},
    )
    return None


@router.get("/{connection_id}", response_model=DatabaseConnectionResponse)
async def get_connection(
    connection_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Get a specific database connection."""
    connection = await db_connection_service.get_connection(connection_id, db)

    if not connection or connection.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Connection not found",
        )

    return DatabaseConnectionResponse.model_validate(connection)


@router.get("/{connection_id}/schema", response_model=DatabaseSchemaResponse)
async def get_connection_schema(
    connection_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Get schema for a connection."""
    connection = await db_connection_service.get_connection(connection_id, db)

    if not connection or connection.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Connection not found",
        )

    schema = await schema_discovery_service.get_connection_schema_response(connection_id, db)

    if not schema:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Schema not found for connection",
        )

    return DatabaseSchemaResponse.model_validate(schema)


@router.put("/{connection_id}/schema/annotations", response_model=DatabaseSchemaResponse)
async def upsert_schema_annotation(
    connection_id: str,
    annotation_data: SchemaAnnotationUpsertRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Save or clear a user-authored schema annotation."""
    connection = await db_connection_service.get_connection(connection_id, db)

    if not connection or connection.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Connection not found",
        )

    schema = await schema_discovery_service.get_connection_schema(connection_id, db)
    if not schema:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Schema not found for connection",
        )

    try:
        payload = await schema_discovery_service.upsert_annotation(
            connection_id=connection_id,
            schema=schema,
            annotation_data=annotation_data,
            db=db,
        )
    except ValueError as exc:
        activity_service.log_event(
            action="connection.schema_annotation_failed",
            status="warning",
            user=current_user,
            resource_type="database_connection",
            resource_id=connection_id,
            details={
                "target_type": annotation_data.target_type,
                "table_name": annotation_data.table_name,
                "column_name": annotation_data.column_name,
                "error": str(exc),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    try:
        schema_text = await schema_discovery_service.get_schema_for_rag(schema.id, db)
        index_updated = await _update_schema_in_rag_best_effort(
            schema_id=schema.id,
            schema_name=connection.database_name,
            schema_text=schema_text,
            current_user=current_user,
            connection_id=connection.id,
        )
    except Exception as exc:
        logger.warning(
            "Failed to refresh RAG after schema annotation update for %s: %s",
            connection_id,
            exc,
        )

    activity_service.log_event(
        action="connection.schema_annotation_updated",
        status="success",
        user=current_user,
        resource_type="database_connection",
        resource_id=connection_id,
        details={
            "connection_name": connection.name,
            "target_type": annotation_data.target_type,
            "table_name": annotation_data.table_name,
            "column_name": annotation_data.column_name,
            "has_nickname": bool(annotation_data.nickname),
            "has_description": bool(annotation_data.description),
        },
    )

    return DatabaseSchemaResponse.model_validate(payload)


@router.post("/{connection_id}/sync-schema", response_model=SyncSchemaResponse)
async def sync_connection_schema(
    connection_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Resync schema for a connection."""
    try:
        connection = await db_connection_service.get_connection(connection_id, db)

        if not connection or connection.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Connection not found",
            )

        # Decrypt credentials
        username, password = db_connection_service.decrypt_credentials(
            connection)

        # Discover and sync schema
        schema = await schema_discovery_service.discover_and_sync_schema(
            connection,
            username,
            password,
            db,
        )

        # Update RAG
        schema_text = await schema_discovery_service.get_schema_for_rag(schema.id, db)
        index_updated = await _update_schema_in_rag_best_effort(
            schema_id=schema.id,
            schema_name=connection.database_name,
            schema_text=schema_text,
            current_user=current_user,
            connection_id=connection.id,
        )

        activity_service.log_event(
            action="connection.schema_synced",
            status="success",
            user=current_user,
            resource_type="database_connection",
            resource_id=connection.id,
            details={
                "connection_name": connection.name,
                "db_type": connection.db_type.value,
                "database_name": connection.database_name,
                "schema_id": schema.id,
            },
        )

        return SyncSchemaResponse(
            message=(
                "Schema synced successfully"
                if index_updated
                else "Schema synced successfully. AI search indexing will catch up later."
            ),
            schema_id=schema.id,
        )

    except HTTPException:
        raise
    except Exception as e:
        detail = str(e)
        is_connection_failure = detail.lower().startswith("connection failed:")
        activity_service.log_event(
            action="connection.schema_sync_failed",
            status="warning" if is_connection_failure else "error",
            user=current_user,
            resource_type="database_connection",
            resource_id=connection_id,
            details={"error": detail},
        )
        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
                if is_connection_failure
                else status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=detail,
        )


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(
    connection_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Delete a database connection."""
    try:
        connection = await db_connection_service.get_connection(connection_id, db)

        if not connection or connection.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Connection not found",
            )

        # Delete from RAG
        schema = await schema_discovery_service.get_connection_schema(connection_id, db)
        if schema:
            await _delete_schema_from_rag_best_effort(schema.id, connection_id)

        # Delete connection
        await db_connection_service.delete_connection(connection_id, db)

        activity_service.log_event(
            action="connection.deleted",
            status="success",
            user=current_user,
            resource_type="database_connection",
            resource_id=connection_id,
            details={
                "connection_name": connection.name,
                "db_type": connection.db_type.value,
                "database_name": connection.database_name,
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        activity_service.log_event(
            action="connection.delete_failed",
            status="error",
            user=current_user,
            resource_type="database_connection",
            resource_id=connection_id,
            details=activity_service.exception_details(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )
