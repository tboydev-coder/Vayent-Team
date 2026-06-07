"""Admin dashboard and platform administration routes."""
from __future__ import annotations

import asyncio
from datetime import date

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    WebSocket,
    status,
)
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    get_current_admin_user,
    get_current_super_admin_user,
    has_admin_access,
)
from app.auth.jwt import extract_user_id_from_token
from app.database import get_db_context, get_db_session
from app.models import User
from app.schemas import (
    ActivityLogPageResponse,
    AdminDashboardResponse,
    AdminNotificationResponse,
    AdminNotificationStatusRequest,
    AdminRoleUpdateRequest,
    AdminUserNotesRequest,
    AdminUserPageResponse,
    AdminUserResponse,
    AdminUserStatusRequest,
    FeatureFlagResponse,
    FeatureFlagUpdateRequest,
    TokenAdjustmentPageResponse,
    TokenAdjustmentRequest,
    TokenAdjustmentResponse,
)
from app.services.admin_dashboard_service import admin_dashboard_service

router = APIRouter(prefix="/admin", tags=["Admin"])


async def require_admin_csrf(
    request: Request,
    x_admin_csrf: str | None = Header(default=None, alias="X-Admin-CSRF"),
) -> None:
    """Require an explicit admin write header for mutating admin APIs."""
    if request.method in {"POST", "PATCH", "PUT", "DELETE"} and x_admin_csrf != "1":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing admin CSRF guard header",
        )


def _admin_error(exc: ValueError) -> HTTPException:
    detail = str(exc)
    status_code = status.HTTP_404_NOT_FOUND if "not found" in detail.lower() else status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=status_code, detail=detail)


@router.get("/dashboard", response_model=AdminDashboardResponse)
async def get_admin_dashboard(
    range_name: str = Query(default="month", alias="range"),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Return the single aggregated executive admin dashboard payload."""
    return await admin_dashboard_service.dashboard(
        db,
        range_name=range_name,
        start_date=start_date,
        end_date=end_date,
    )


@router.get("/users", response_model=AdminUserPageResponse)
async def list_admin_users(
    search: str | None = Query(default=None),
    role: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Search and filter users for administration."""
    return await admin_dashboard_service.list_users(
        db,
        search=search,
        role=role,
        status=status_filter,
        page=page,
        page_size=page_size,
    )


@router.patch(
    "/users/{user_id}/role",
    response_model=AdminUserResponse,
    dependencies=[Depends(require_admin_csrf)],
)
async def update_user_role(
    user_id: str,
    request: AdminRoleUpdateRequest,
    current_user: User = Depends(get_current_super_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Promote or demote users; super admins only."""
    try:
        return await admin_dashboard_service.update_admin_role(
            db,
            actor=current_user,
            user_id=user_id,
            is_admin=request.is_admin,
            is_super_admin=request.is_super_admin,
        )
    except ValueError as exc:
        raise _admin_error(exc) from exc


@router.patch(
    "/users/{user_id}/status",
    response_model=AdminUserResponse,
    dependencies=[Depends(require_admin_csrf)],
)
async def update_user_status(
    user_id: str,
    request: AdminUserStatusRequest,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Suspend or reactivate users."""
    try:
        return await admin_dashboard_service.update_user_status(
            db,
            actor=current_user,
            user_id=user_id,
            is_suspended=request.is_suspended,
            reason=request.reason,
        )
    except ValueError as exc:
        raise _admin_error(exc) from exc


@router.patch(
    "/users/{user_id}/notes",
    response_model=AdminUserResponse,
    dependencies=[Depends(require_admin_csrf)],
)
async def update_user_notes(
    user_id: str,
    request: AdminUserNotesRequest,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Save internal customer-support notes on a user."""
    try:
        return await admin_dashboard_service.update_user_notes(
            db,
            actor=current_user,
            user_id=user_id,
            admin_notes=request.admin_notes,
        )
    except ValueError as exc:
        raise _admin_error(exc) from exc


@router.post(
    "/tokens/adjust",
    response_model=TokenAdjustmentResponse,
    dependencies=[Depends(require_admin_csrf)],
)
async def adjust_user_tokens(
    request: TokenAdjustmentRequest,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Add or deduct tokens manually with audit logging."""
    try:
        return await admin_dashboard_service.adjust_tokens(
            db,
            actor=current_user,
            user_id=request.user_id,
            adjustment_type=request.adjustment_type,
            amount=request.amount,
            reason=request.reason,
        )
    except ValueError as exc:
        raise _admin_error(exc) from exc


@router.get("/tokens/history", response_model=TokenAdjustmentPageResponse)
async def list_token_adjustments(
    user_id: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Return token adjustment history."""
    return await admin_dashboard_service.list_token_adjustments(
        db,
        user_id=user_id,
        page=page,
        page_size=page_size,
    )


@router.get("/activity-logs", response_model=ActivityLogPageResponse)
async def list_activity_logs(
    search: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    action: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Search and filter support/security activity logs."""
    return await admin_dashboard_service.list_activity_logs(
        db,
        search=search,
        severity=severity,
        action=action,
        user_id=user_id,
        page=page,
        page_size=page_size,
    )


@router.get("/activity-logs/export")
async def export_activity_logs(
    search: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    action: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Export filtered activity logs as CSV."""
    csv_payload = await admin_dashboard_service.export_activity_logs_csv(
        db,
        search=search,
        severity=severity,
        action=action,
        user_id=user_id,
    )
    return Response(
        content=csv_payload,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=vayent-activity-logs.csv"},
    )


@router.get("/notifications", response_model=list[AdminNotificationResponse])
async def list_notifications(
    status_filter: str | None = Query(default=None, alias="status"),
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """List persisted admin notifications."""
    return await admin_dashboard_service.list_notifications(db, status=status_filter)


@router.patch(
    "/notifications/{notification_id}",
    response_model=AdminNotificationResponse,
    dependencies=[Depends(require_admin_csrf)],
)
async def update_notification_status(
    notification_id: str,
    request: AdminNotificationStatusRequest,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Acknowledge or resolve an admin notification."""
    try:
        return await admin_dashboard_service.update_notification_status(
            db,
            actor=current_user,
            notification_id=notification_id,
            status=request.status,
        )
    except ValueError as exc:
        raise _admin_error(exc) from exc


@router.patch(
    "/feature-flags/{flag_key}",
    response_model=FeatureFlagResponse,
    dependencies=[Depends(require_admin_csrf)],
)
async def update_feature_flag(
    flag_key: str,
    request: FeatureFlagUpdateRequest,
    current_user: User = Depends(get_current_super_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Update feature flags; super admins only."""
    try:
        return await admin_dashboard_service.update_feature_flag(
            db,
            actor=current_user,
            flag_key=flag_key,
            is_enabled=request.is_enabled,
            rollout_percentage=request.rollout_percentage,
        )
    except ValueError as exc:
        raise _admin_error(exc) from exc


@router.websocket("/ws")
async def admin_dashboard_stream(websocket: WebSocket):
    """Push periodic dashboard snapshots to admins over websocket."""
    token = websocket.query_params.get("access_token")
    user_id = extract_user_id_from_token(token or "", expected_token_type="access")
    if not user_id:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    try:
        async with get_db_context() as db:
            user = await db.get(User, user_id)
            if not user or not has_admin_access(user):
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return
    except Exception:
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    await websocket.accept()
    try:
        while True:
            async with get_db_context() as db:
                payload = await admin_dashboard_service.dashboard(db, range_name="month")
            await websocket.send_json(jsonable_encoder(payload))
            await asyncio.sleep(15)
    except Exception:
        await websocket.close()
