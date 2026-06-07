"""Advanced copilot API routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_active_user
from app.config import get_settings
from app.database import get_db_session
from app.models import User
from app.schemas import (
    CopilotArtifactListResponse,
    CopilotArtifactResponse,
    CopilotArtifactUpdate,
    CopilotMemoryCreate,
    CopilotMemoryResponse,
    CopilotOverviewResponse,
    CopilotWatchlistCreate,
    CopilotWatchlistResponse,
    DashboardBuildRequest,
    ExecutiveBriefingRequest,
    InvestigationRequest,
    RecommendationRequest,
    ScenarioRequest,
)
from app.services.copilot_service import copilot_service
from app.services.activity_service import activity_service
from app.services.token_usage_service import TokenLimitExceededError, token_usage_service

router = APIRouter(prefix="/copilot", tags=["Copilot"])
settings = get_settings()

COPILOT_COMPLETION_ALLOWANCES = {
    "copilot_investigation": 900,
    "copilot_briefing": 460,
    "copilot_recommendation": 460,
    "copilot_scenario": 460,
    "copilot_dashboard": 580,
    "copilot_watchlist": 220,
}


def _log_copilot_event(
    *,
    action: str,
    status: str = "success",
    user: User,
    resource_id: str | None = None,
    details: dict | None = None,
) -> None:
    activity_service.log_event(
        action=action,
        status=status,
        user=user,
        resource_type="copilot",
        resource_id=resource_id,
        details=details,
    )


def _to_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc

    message = str(exc)
    lowered = message.lower()

    if "not found" in lowered:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)
    if "quota" in lowered or "rate-limiting" in lowered:
        return HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=message)
    if "ai service" in lowered and (
        "unavailable" in lowered
        or "misconfigured" in lowered
        or "authentication failed" in lowered
        or "couldn't be reached" in lowered
        or "timed out" in lowered
    ):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=message)
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)


def _metric_monitoring_coming_soon() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Metric monitoring is coming soon.",
    )


async def _safe_release_reserved_tokens(
    *,
    user_id: str,
    reserved_tokens: int,
    db: AsyncSession,
) -> None:
    if reserved_tokens <= 0:
        return

    in_transaction = getattr(db, "in_transaction", None)
    if callable(in_transaction):
        try:
            if in_transaction():
                await db.rollback()
        except Exception:
            pass

    await token_usage_service.release_tokens(
        user_id=user_id,
        reserved_tokens=reserved_tokens,
        db=db,
    )


async def _run_metered_copilot_request(
    *,
    user_id: str,
    prompt: str,
    request_kind: str,
    db: AsyncSession,
    action,
):
    reserved_tokens = 0

    try:
        reservation = await token_usage_service.reserve_tokens(
            user_id,
            token_usage_service.estimate_request_tokens(
                prompt,
                completion_allowance=COPILOT_COMPLETION_ALLOWANCES[request_kind],
            ),
            db,
        )
        reserved_tokens = reservation.get("reserved_tokens", 0)
        result, usage = await action()
        total_tokens = (usage or {}).get("total_tokens", 0) or 0

        if reserved_tokens > 0:
            if total_tokens > 0:
                await token_usage_service.finalize_tokens(
                    user_id=user_id,
                    reserved_tokens=reserved_tokens,
                    prompt_tokens=(usage or {}).get("prompt_tokens", 0) or 0,
                    completion_tokens=(usage or {}).get("completion_tokens", 0) or 0,
                    db=db,
                    request_kind=request_kind,
                )
            else:
                await token_usage_service.release_tokens(
                    user_id=user_id,
                    reserved_tokens=reserved_tokens,
                    db=db,
                )

        return result
    except TokenLimitExceededError as exc:
        if reserved_tokens > 0:
            await _safe_release_reserved_tokens(
                user_id=user_id,
                reserved_tokens=reserved_tokens,
                db=db,
            )
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    except Exception:
        if reserved_tokens > 0:
            await _safe_release_reserved_tokens(
                user_id=user_id,
                reserved_tokens=reserved_tokens,
                db=db,
            )
        raise


@router.get("/overview", response_model=CopilotOverviewResponse)
async def get_copilot_overview(
    connection_id: str | None = Query(default=None),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    overview = await copilot_service.get_overview(
        user_id=current_user.id,
        connection_id=connection_id,
        db=db,
    )
    if not settings.metric_monitoring_enabled:
        overview["watchlists"] = []
        overview["alerts"] = []
    return CopilotOverviewResponse(**overview)


@router.get("/artifacts", response_model=CopilotArtifactListResponse)
async def list_copilot_artifacts(
    connection_id: str | None = Query(default=None),
    source_id: str | None = Query(default=None),
    artifact_type: str | None = Query(default=None),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    items = await copilot_service.list_artifacts(
        user_id=current_user.id,
        connection_id=source_id or connection_id,
        artifact_type=artifact_type,
        db=db,
    )
    return CopilotArtifactListResponse(items=items)


@router.patch("/artifacts/{artifact_id}", response_model=CopilotArtifactResponse)
async def update_copilot_artifact(
    artifact_id: str,
    request: CopilotArtifactUpdate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    artifact = await copilot_service.update_artifact(
        artifact_id=artifact_id,
        user_id=current_user.id,
        title=request.title,
        summary=request.summary,
        workspace=request.workspace,
        project=request.project,
        db=db,
    )
    if not artifact:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
    _log_copilot_event(
        action="copilot.artifact_updated",
        status="success",
        user=current_user,
        resource_id=artifact_id,
        details={
            "title": request.title,
            "workspace": request.workspace,
            "project": request.project,
        },
    )
    return artifact


@router.post(
    "/artifacts/{artifact_id}/duplicate",
    response_model=CopilotArtifactResponse,
)
async def duplicate_copilot_artifact(
    artifact_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    artifact = await copilot_service.duplicate_artifact(
        artifact_id=artifact_id,
        user_id=current_user.id,
        db=db,
    )
    if not artifact:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
    _log_copilot_event(
        action="copilot.artifact_duplicated",
        status="success",
        user=current_user,
        resource_id=artifact.id,
        details={"source_artifact_id": artifact_id},
    )
    return artifact


@router.delete("/artifacts/{artifact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_copilot_artifact(
    artifact_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    deleted = await copilot_service.delete_artifact(
        artifact_id=artifact_id,
        user_id=current_user.id,
        db=db,
    )
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
    _log_copilot_event(
        action="copilot.artifact_deleted",
        status="success",
        user=current_user,
        resource_id=artifact_id,
    )


@router.post("/investigations", response_model=CopilotArtifactResponse)
async def create_investigation(
    request: InvestigationRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        result = await _run_metered_copilot_request(
            user_id=current_user.id,
            prompt=request.prompt,
            request_kind="copilot_investigation",
            db=db,
            action=lambda: copilot_service.generate_investigation(
                user_id=current_user.id,
                connection_id=request.connection_id,
                prompt=request.prompt,
                session_id=request.session_id,
                db=db,
            ),
        )
        _log_copilot_event(
            action="copilot.investigation_created",
            status="success",
            user=current_user,
            resource_id=getattr(result, "id", None),
            details={
                "connection_id": request.connection_id,
                "session_id": request.session_id,
                "prompt_preview": activity_service.preview_text(request.prompt),
                "title": getattr(result, "title", None),
            },
        )
        return result
    except HTTPException:
        raise
    except Exception as exc:
        _log_copilot_event(
            action="copilot.investigation_created",
            status="error",
            user=current_user,
            details={
                "connection_id": request.connection_id,
                "session_id": request.session_id,
                "prompt_preview": activity_service.preview_text(request.prompt),
                **activity_service.exception_details(exc),
            },
        )
        raise _to_http_error(exc) from exc


@router.post("/briefings", response_model=CopilotArtifactResponse)
async def create_briefing(
    request: ExecutiveBriefingRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        result = await _run_metered_copilot_request(
            user_id=current_user.id,
            prompt=request.prompt,
            request_kind="copilot_briefing",
            db=db,
            action=lambda: copilot_service.generate_briefing(
                user_id=current_user.id,
                connection_id=request.connection_id,
                prompt=request.prompt,
                db=db,
            ),
        )
        _log_copilot_event(
            action="copilot.briefing_created",
            status="success",
            user=current_user,
            resource_id=getattr(result, "id", None),
            details={
                "connection_id": request.connection_id,
                "prompt_preview": activity_service.preview_text(request.prompt),
                "title": getattr(result, "title", None),
            },
        )
        return result
    except HTTPException:
        raise
    except Exception as exc:
        _log_copilot_event(
            action="copilot.briefing_created",
            status="error",
            user=current_user,
            details={
                "connection_id": request.connection_id,
                "prompt_preview": activity_service.preview_text(request.prompt),
                **activity_service.exception_details(exc),
            },
        )
        raise _to_http_error(exc) from exc


@router.post("/recommendations", response_model=CopilotArtifactResponse)
async def create_recommendation(
    request: RecommendationRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        result = await _run_metered_copilot_request(
            user_id=current_user.id,
            prompt=request.prompt,
            request_kind="copilot_recommendation",
            db=db,
            action=lambda: copilot_service.generate_recommendations(
                user_id=current_user.id,
                connection_id=request.connection_id,
                prompt=request.prompt,
                session_id=request.session_id,
                db=db,
            ),
        )
        _log_copilot_event(
            action="copilot.recommendation_created",
            status="success",
            user=current_user,
            resource_id=getattr(result, "id", None),
            details={
                "connection_id": request.connection_id,
                "session_id": request.session_id,
                "prompt_preview": activity_service.preview_text(request.prompt),
                "title": getattr(result, "title", None),
            },
        )
        return result
    except HTTPException:
        raise
    except Exception as exc:
        _log_copilot_event(
            action="copilot.recommendation_created",
            status="error",
            user=current_user,
            details={
                "connection_id": request.connection_id,
                "session_id": request.session_id,
                "prompt_preview": activity_service.preview_text(request.prompt),
                **activity_service.exception_details(exc),
            },
        )
        raise _to_http_error(exc) from exc


@router.post("/scenarios", response_model=CopilotArtifactResponse)
async def create_scenario(
    request: ScenarioRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        result = await _run_metered_copilot_request(
            user_id=current_user.id,
            prompt=request.prompt,
            request_kind="copilot_scenario",
            db=db,
            action=lambda: copilot_service.generate_scenario(
                user_id=current_user.id,
                connection_id=request.connection_id,
                prompt=request.prompt,
                session_id=request.session_id,
                db=db,
            ),
        )
        _log_copilot_event(
            action="copilot.scenario_created",
            status="success",
            user=current_user,
            resource_id=getattr(result, "id", None),
            details={
                "connection_id": request.connection_id,
                "session_id": request.session_id,
                "prompt_preview": activity_service.preview_text(request.prompt),
                "title": getattr(result, "title", None),
            },
        )
        return result
    except HTTPException:
        raise
    except Exception as exc:
        _log_copilot_event(
            action="copilot.scenario_created",
            status="error",
            user=current_user,
            details={
                "connection_id": request.connection_id,
                "session_id": request.session_id,
                "prompt_preview": activity_service.preview_text(request.prompt),
                **activity_service.exception_details(exc),
            },
        )
        raise _to_http_error(exc) from exc


@router.post("/dashboards", response_model=CopilotArtifactResponse)
async def build_dashboard(
    request: DashboardBuildRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        result = await _run_metered_copilot_request(
            user_id=current_user.id,
            prompt=request.prompt,
            request_kind="copilot_dashboard",
            db=db,
            action=lambda: copilot_service.build_dashboard(
                user_id=current_user.id,
                connection_id=request.connection_id,
                source_ids=request.source_ids,
                prompt=request.prompt,
                db=db,
            ),
        )
        _log_copilot_event(
            action="copilot.dashboard_created",
            status="success",
            user=current_user,
            resource_id=getattr(result, "id", None),
            details={
                "connection_id": request.connection_id,
                "source_id": request.source_id,
                "source_ids": request.source_ids,
                "prompt_preview": activity_service.preview_text(request.prompt),
                "title": getattr(result, "title", None),
            },
        )
        return result
    except HTTPException:
        raise
    except Exception as exc:
        _log_copilot_event(
            action="copilot.dashboard_created",
            status="error",
            user=current_user,
            details={
                "connection_id": request.connection_id,
                "source_id": request.source_id,
                "source_ids": request.source_ids,
                "prompt_preview": activity_service.preview_text(request.prompt),
                **activity_service.exception_details(exc),
            },
        )
        raise _to_http_error(exc) from exc


@router.get("/memories", response_model=list[CopilotMemoryResponse])
async def list_memories(
    connection_id: str | None = Query(default=None),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    return await copilot_service.list_memories(
        user_id=current_user.id,
        connection_id=connection_id,
        db=db,
    )


@router.post("/memories", response_model=CopilotMemoryResponse, status_code=status.HTTP_201_CREATED)
async def create_memory(
    request: CopilotMemoryCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    memory = await copilot_service.create_memory(
        user_id=current_user.id,
        connection_id=request.connection_id,
        title=request.title,
        category=request.category,
        content=request.content,
        db=db,
    )
    _log_copilot_event(
        action="copilot.memory_created",
        status="success",
        user=current_user,
        resource_id=getattr(memory, "id", None),
        details={
            "connection_id": request.connection_id,
            "category": request.category,
            "title": request.title,
            "content_preview": activity_service.preview_text(request.content),
        },
    )
    return memory


@router.delete("/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    deleted = await copilot_service.delete_memory(
        memory_id=memory_id,
        user_id=current_user.id,
        db=db,
    )
    if not deleted:
        _log_copilot_event(
            action="copilot.memory_deleted",
            status="warning",
            user=current_user,
            resource_id=memory_id,
            details={"reason": "not_found"},
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found")
    _log_copilot_event(
        action="copilot.memory_deleted",
        status="success",
        user=current_user,
        resource_id=memory_id,
    )
    return None


@router.get("/watchlists", response_model=list[CopilotWatchlistResponse])
async def list_watchlists(
    connection_id: str | None = Query(default=None),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    if not settings.metric_monitoring_enabled:
        return []

    return await copilot_service.list_watchlists(
        user_id=current_user.id,
        connection_id=connection_id,
        db=db,
    )


@router.get("/alerts", response_model=list[CopilotWatchlistResponse])
async def list_alerts(
    connection_id: str | None = Query(default=None),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    if not settings.metric_monitoring_enabled:
        return []

    watchlists = await copilot_service.list_watchlists(
        user_id=current_user.id,
        connection_id=connection_id,
        db=db,
    )
    return [watchlist for watchlist in watchlists if watchlist.last_status == "alert"]


@router.post("/watchlists", response_model=CopilotWatchlistResponse, status_code=status.HTTP_201_CREATED)
async def create_watchlist(
    request: CopilotWatchlistCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    if not settings.metric_monitoring_enabled:
        raise _metric_monitoring_coming_soon()

    try:
        result = await _run_metered_copilot_request(
            user_id=current_user.id,
            prompt=request.prompt,
            request_kind="copilot_watchlist",
            db=db,
            action=lambda: copilot_service.create_watchlist(
                user_id=current_user.id,
                connection_id=request.connection_id,
                prompt=request.prompt,
                comparator=request.comparator,
                threshold_value=request.threshold_value,
                db=db,
            ),
        )
        _log_copilot_event(
            action="copilot.watchlist_created",
            status="success",
            user=current_user,
            resource_id=getattr(result, "id", None),
            details={
                "connection_id": request.connection_id,
                "comparator": request.comparator,
                "threshold_value": request.threshold_value,
                "prompt_preview": activity_service.preview_text(request.prompt),
                "title": getattr(result, "title", None),
            },
        )
        return result
    except HTTPException:
        raise
    except Exception as exc:
        _log_copilot_event(
            action="copilot.watchlist_created",
            status="error",
            user=current_user,
            details={
                "connection_id": request.connection_id,
                "comparator": request.comparator,
                "threshold_value": request.threshold_value,
                "prompt_preview": activity_service.preview_text(request.prompt),
                **activity_service.exception_details(exc),
            },
        )
        raise _to_http_error(exc) from exc


@router.post("/watchlists/{watchlist_id}/evaluate", response_model=CopilotWatchlistResponse)
async def evaluate_watchlist(
    watchlist_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    if not settings.metric_monitoring_enabled:
        raise _metric_monitoring_coming_soon()

    try:
        result = await copilot_service.evaluate_watchlist(
            watchlist_id=watchlist_id,
            user_id=current_user.id,
            db=db,
        )
        _log_copilot_event(
            action="copilot.watchlist_evaluated",
            status="success",
            user=current_user,
            resource_id=watchlist_id,
            details={
                "last_status": getattr(result, "last_status", None),
                "last_value": getattr(result, "last_value", None),
            },
        )
        return result
    except Exception as exc:
        _log_copilot_event(
            action="copilot.watchlist_evaluated",
            status="error",
            user=current_user,
            resource_id=watchlist_id,
            details=activity_service.exception_details(exc),
        )
        raise _to_http_error(exc) from exc
