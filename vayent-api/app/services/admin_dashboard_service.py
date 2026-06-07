"""Executive admin dashboard aggregation and administration services."""
from __future__ import annotations

import csv
import io
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import check_db_health
from app.models import (
    ActivityLog,
    AdminNotification,
    ChatMessage,
    ChatSession,
    DatabaseConnection,
    FeatureFlag,
    QueryLog,
    TokenAdjustmentLog,
    TokenUsageLog,
    User,
)
from app.schemas import (
    ActivityLogResponse,
    AdminNotificationResponse,
    AdminUserResponse,
    FeatureFlagResponse,
    TokenAdjustmentResponse,
)

SERVICE_STARTED_AT = datetime.utcnow()


@dataclass(frozen=True)
class DateRange:
    """Normalized dashboard date range."""

    label: str
    start: datetime
    end: datetime


DEFAULT_FEATURE_FLAGS = [
    {
        "key": "admin.realtime_stream",
        "name": "Realtime admin stream",
        "description": "Send dashboard refreshes through the admin websocket.",
        "is_enabled": True,
        "rollout_percentage": 100,
    },
    {
        "key": "ai.usage_alerts",
        "name": "AI usage alerts",
        "description": "Notify admins when users approach token limits.",
        "is_enabled": True,
        "rollout_percentage": 100,
    },
    {
        "key": "support.activity_export",
        "name": "Support log export",
        "description": "Allow admins to export filtered activity logs.",
        "is_enabled": True,
        "rollout_percentage": 100,
    },
]


class AdminDashboardService:
    """Aggregate admin analytics and perform platform administration."""

    def __init__(self) -> None:
        self.settings = get_settings()

    async def dashboard(
        self,
        db: AsyncSession,
        *,
        range_name: str = "month",
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict[str, Any]:
        selected_range = self._resolve_range(
            range_name=range_name,
            start_date=start_date,
            end_date=end_date,
        )
        now = datetime.utcnow()
        today_start = datetime.combine(now.date(), time.min)
        week_start = datetime.combine((now - timedelta(days=now.weekday())).date(), time.min)
        month_start = datetime(now.year, now.month, 1)

        total_users = await self._scalar_count(db, select(func.count()).select_from(User))
        new_today = await self._count_users_created(db, today_start, now)
        new_week = await self._count_users_created(db, week_start, now)
        new_month = await self._count_users_created(db, month_start, now)

        daily_growth = self._growth_percent(
            new_today,
            await self._count_users_created(db, today_start - timedelta(days=1), today_start),
        )
        weekly_growth = self._growth_percent(
            new_week,
            await self._count_users_created(db, week_start - timedelta(days=7), week_start),
        )
        monthly_growth = self._growth_percent(
            new_month,
            await self._count_users_created(db, self._previous_month_start(month_start), month_start),
        )

        active_users = {
            "dau": await self._count_active_users(db, today_start, now),
            "wau": await self._count_active_users(db, week_start, now),
            "mau": await self._count_active_users(db, month_start, now),
            "online_now": await self._count_active_users(db, now - timedelta(minutes=5), now),
        }

        total_api_requests = await self._count_activity(
            db,
            selected_range.start,
            selected_range.end,
            action="request.completed",
        )
        failed_requests = await self._count_activity(
            db,
            selected_range.start,
            selected_range.end,
            action="request.completed",
            failed_only=True,
        )

        ai_usage = await self._ai_usage(db, selected_range)
        performance = await self._performance(db, selected_range, total_api_requests, failed_requests)
        retention = await self._retention(db, selected_range, total_users)
        notifications = await self._notifications(db, performance, ai_usage, active_users)
        feature_flags = await self.ensure_default_feature_flags(db)

        paid_users = await self._scalar_count(
            db,
            select(func.count()).select_from(User).where(
                or_(User.plan_type == "paid", User.is_premium.is_(True))
            ),
        )
        admin_count = await self._scalar_count(
            db,
            select(func.count()).select_from(User).where(self._admin_user_filter()),
        )

        failed_login_attempts = await self._scalar_count(
            db,
            select(func.count())
            .select_from(ActivityLog)
            .where(
                ActivityLog.created_at >= selected_range.start,
                ActivityLog.created_at < selected_range.end,
                ActivityLog.endpoint.like("/auth/%"),
                ActivityLog.response_status_code >= 400,
            ),
        )

        return {
            "generated_at": now,
            "range": {
                "label": selected_range.label,
                "start": selected_range.start,
                "end": selected_range.end,
            },
            "overview": {
                "total_users": total_users,
                "new_users": {
                    "today": new_today,
                    "this_week": new_week,
                    "this_month": new_month,
                },
                "total_api_requests": total_api_requests,
                "total_ai_generations": ai_usage["total_ai_requests"],
                "total_query_logs": await self._scalar_count(
                    db,
                    select(func.count()).select_from(QueryLog),
                ),
                "failed_login_attempts": failed_login_attempts,
                "admin_count": admin_count,
                "online_users": active_users["online_now"],
                "average_session_duration_seconds": await self._average_session_duration(db, selected_range),
            },
            "growth": {
                "daily_signups": daily_growth,
                "weekly_signups": weekly_growth,
                "monthly_signups": monthly_growth,
            },
            "active_users": active_users,
            "most_active_users": {
                "by_actions": await self._top_users_by_activity(db, selected_range),
                "by_login_frequency": await self._top_users_by_login(db, selected_range),
                "by_token_usage": await self._top_users_by_tokens(db, selected_range),
                "by_ai_requests": await self._top_users_by_ai_requests(db, selected_range),
            },
            "ai_usage": ai_usage,
            "performance": performance,
            "revenue": {
                "billing_detected": paid_users > 0,
                "paid_users": paid_users,
                "free_users": max(total_users - paid_users, 0),
                "estimated_mrr": None,
                "note": "No billing ledger is configured; revenue metrics use plan flags only.",
            },
            "system_health": await self._system_health(db, performance),
            "security": {
                "failed_login_attempts": failed_login_attempts,
                "unauthorized_admin_attempts": await self._count_admin_route_denials(db, selected_range),
                "super_admin_denials": await self._count_admin_route_denials(
                    db,
                    selected_range,
                    sensitive_only=True,
                ),
                "rate_limited_requests": performance["rate_limited_requests"],
                "suspended_users": await self._scalar_count(
                    db,
                    select(func.count()).select_from(User).where(User.is_suspended.is_(True)),
                ),
            },
            "retention": retention,
            "engagement_trends": await self._engagement_trends(db, selected_range),
            "recent": {
                "registered_users": await self._recent_users(db),
                "failed_actions": await self._recent_failed_actions(db),
                "token_adjustments": await self._recent_token_adjustments(db),
            },
            "notifications": notifications,
            "feature_flags": [self._feature_flag_response(flag).model_dump() for flag in feature_flags],
        }

    async def list_users(
        self,
        db: AsyncSession,
        *,
        search: str | None = None,
        role: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        filters = []
        if search:
            pattern = f"%{search.strip().lower()}%"
            filters.append(
                or_(
                    func.lower(User.email).like(pattern),
                    func.lower(User.username).like(pattern),
                )
            )
        if role == "admin":
            filters.append(self._admin_user_filter())
        elif role == "user":
            filters.append(
                and_(
                    User.is_admin.is_(False),
                    User.is_super_admin.is_(False),
                    self._not_bootstrap_email_filter(),
                )
            )
        elif role == "super_admin":
            filters.append(self._super_admin_user_filter())

        if status == "active":
            filters.append(and_(User.is_active.is_(True), User.is_suspended.is_(False)))
        elif status == "suspended":
            filters.append(User.is_suspended.is_(True))
        elif status == "inactive":
            filters.append(User.is_active.is_(False))

        count_query = select(func.count()).select_from(User)
        item_query = select(User).order_by(User.created_at.desc())
        if filters:
            count_query = count_query.where(*filters)
            item_query = item_query.where(*filters)

        total_items = await self._scalar_count(db, count_query)
        offset = (page - 1) * page_size
        result = await db.execute(item_query.offset(offset).limit(page_size))
        users = list(result.scalars().all())
        metrics = await self._user_metric_maps(db, [user.id for user in users])

        return {
            "items": [
                self._admin_user_response(
                    user,
                    action_count=metrics["actions"].get(user.id, 0),
                    ai_request_count=metrics["ai_requests"].get(user.id, 0),
                    tokens_used=metrics["tokens"].get(user.id, 0),
                )
                for user in users
            ],
            "page": page,
            "page_size": page_size,
            "total_items": total_items,
            "total_pages": self._total_pages(total_items, page_size),
        }

    async def update_admin_role(
        self,
        db: AsyncSession,
        *,
        actor: User,
        user_id: str,
        is_admin: bool,
        is_super_admin: bool,
    ) -> AdminUserResponse:
        user = await self._get_user_or_raise(db, user_id)
        if self._is_bootstrap_user(user) and (not is_admin or not is_super_admin):
            raise ValueError("Default bootstrap admins cannot be demoted.")
        if user.id == actor.id and (not is_admin or not is_super_admin):
            raise ValueError("Super admins cannot remove their own admin access.")

        user.is_admin = bool(is_admin or is_super_admin)
        user.is_super_admin = bool(is_super_admin)
        user.updated_at = datetime.utcnow()
        await self.log_admin_action(
            db,
            actor=actor,
            action="admin.role_updated",
            resource_type="user",
            resource_id=user.id,
            details={
                "target_email": user.email,
                "is_admin": user.is_admin,
                "is_super_admin": user.is_super_admin,
            },
        )
        await db.commit()
        await db.refresh(user)
        return self._admin_user_response(user)

    async def update_user_status(
        self,
        db: AsyncSession,
        *,
        actor: User,
        user_id: str,
        is_suspended: bool,
        reason: str | None = None,
    ) -> AdminUserResponse:
        user = await self._get_user_or_raise(db, user_id)
        if user.id == actor.id and is_suspended:
            raise ValueError("Admins cannot suspend their own account.")

        user.is_suspended = is_suspended
        user.updated_at = datetime.utcnow()
        await self.log_admin_action(
            db,
            actor=actor,
            action="admin.user_suspended" if is_suspended else "admin.user_reactivated",
            resource_type="user",
            resource_id=user.id,
            details={"target_email": user.email, "reason": reason},
            severity="warning" if is_suspended else "info",
        )
        await db.commit()
        await db.refresh(user)
        return self._admin_user_response(user)

    async def update_user_notes(
        self,
        db: AsyncSession,
        *,
        actor: User,
        user_id: str,
        admin_notes: str | None,
    ) -> AdminUserResponse:
        user = await self._get_user_or_raise(db, user_id)
        user.admin_notes = admin_notes
        user.updated_at = datetime.utcnow()
        await self.log_admin_action(
            db,
            actor=actor,
            action="admin.user_notes_updated",
            resource_type="user",
            resource_id=user.id,
            details={"target_email": user.email},
        )
        await db.commit()
        await db.refresh(user)
        return self._admin_user_response(user)

    async def adjust_tokens(
        self,
        db: AsyncSession,
        *,
        actor: User,
        user_id: str,
        adjustment_type: str,
        amount: int,
        reason: str | None = None,
    ) -> TokenAdjustmentResponse:
        user = await self._get_user_or_raise(db, user_id)
        before = self._available_tokens(user)
        if before is not None and adjustment_type == "deduct" and amount > before:
            raise ValueError("Token adjustment would create a negative balance.")

        if adjustment_type == "add":
            user.manual_token_balance = (user.manual_token_balance or 0) + amount
        elif adjustment_type == "deduct":
            remaining_deduction = amount
            manual_balance = user.manual_token_balance or 0
            manual_deduction = min(manual_balance, remaining_deduction)
            user.manual_token_balance = manual_balance - manual_deduction
            remaining_deduction -= manual_deduction
            if remaining_deduction > 0:
                user.monthly_token_usage = (user.monthly_token_usage or 0) + remaining_deduction
        else:
            raise ValueError("Unsupported token adjustment type.")

        user.updated_at = datetime.utcnow()
        after = self._available_tokens(user)
        adjustment = TokenAdjustmentLog(
            id=str(uuid.uuid4()),
            user_id=user.id,
            admin_user_id=actor.id,
            adjustment_type=adjustment_type,
            amount=amount,
            balance_before=before if before is not None else -1,
            balance_after=after if after is not None else -1,
            reason=reason,
        )
        db.add(adjustment)
        await self.log_admin_action(
            db,
            actor=actor,
            action="admin.tokens_adjusted",
            resource_type="user",
            resource_id=user.id,
            details={
                "target_email": user.email,
                "adjustment_type": adjustment_type,
                "amount": amount,
                "balance_before": before,
                "balance_after": after,
                "reason": reason,
            },
        )
        await db.commit()
        await db.refresh(adjustment)
        return self._token_adjustment_response(adjustment, user=user, admin_user=actor)

    async def list_token_adjustments(
        self,
        db: AsyncSession,
        *,
        user_id: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        filters = []
        if user_id:
            filters.append(TokenAdjustmentLog.user_id == user_id)
        count_query = select(func.count()).select_from(TokenAdjustmentLog)
        item_query = select(TokenAdjustmentLog).order_by(TokenAdjustmentLog.created_at.desc())
        if filters:
            count_query = count_query.where(*filters)
            item_query = item_query.where(*filters)

        total_items = await self._scalar_count(db, count_query)
        result = await db.execute(
            item_query.offset((page - 1) * page_size).limit(page_size)
        )
        adjustments = list(result.scalars().all())
        users = await self._users_by_id(
            db,
            list({item.user_id for item in adjustments} | {item.admin_user_id for item in adjustments}),
        )
        return {
            "items": [
                self._token_adjustment_response(
                    item,
                    user=users.get(item.user_id),
                    admin_user=users.get(item.admin_user_id),
                )
                for item in adjustments
            ],
            "page": page,
            "page_size": page_size,
            "total_items": total_items,
            "total_pages": self._total_pages(total_items, page_size),
        }

    async def list_activity_logs(
        self,
        db: AsyncSession,
        *,
        search: str | None = None,
        severity: str | None = None,
        action: str | None = None,
        user_id: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        filters = self._activity_filters(
            search=search,
            severity=severity,
            action=action,
            user_id=user_id,
        )
        count_query = select(func.count()).select_from(ActivityLog)
        item_query = select(ActivityLog).order_by(ActivityLog.created_at.desc())
        if filters:
            count_query = count_query.where(*filters)
            item_query = item_query.where(*filters)

        total_items = await self._scalar_count(db, count_query)
        result = await db.execute(item_query.offset((page - 1) * page_size).limit(page_size))
        logs = list(result.scalars().all())
        return {
            "items": [self._activity_log_response(log) for log in logs],
            "page": page,
            "page_size": page_size,
            "total_items": total_items,
            "total_pages": self._total_pages(total_items, page_size),
        }

    async def export_activity_logs_csv(
        self,
        db: AsyncSession,
        *,
        search: str | None = None,
        severity: str | None = None,
        action: str | None = None,
        user_id: str | None = None,
    ) -> str:
        filters = self._activity_filters(
            search=search,
            severity=severity,
            action=action,
            user_id=user_id,
        )
        query = select(ActivityLog).order_by(ActivityLog.created_at.desc()).limit(5000)
        if filters:
            query = query.where(*filters)
        result = await db.execute(query)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "created_at",
                "severity",
                "actor",
                "action",
                "endpoint",
                "status_code",
                "response_time_ms",
                "ip_address",
                "summary",
            ]
        )
        for log in result.scalars().all():
            response = self._activity_log_response(log)
            writer.writerow(
                [
                    response.created_at.isoformat(),
                    response.severity,
                    response.actor_email or response.actor_username or "",
                    response.action,
                    response.endpoint or "",
                    response.response_status_code or "",
                    response.response_time_ms or "",
                    response.ip_address or "",
                    response.summary,
                ]
            )
        return output.getvalue()

    async def list_notifications(
        self,
        db: AsyncSession,
        *,
        status: str | None = None,
    ) -> list[AdminNotificationResponse]:
        query = select(AdminNotification).order_by(AdminNotification.created_at.desc()).limit(100)
        if status:
            query = query.where(AdminNotification.status == status)
        result = await db.execute(query)
        return [self._notification_response(item) for item in result.scalars().all()]

    async def update_notification_status(
        self,
        db: AsyncSession,
        *,
        actor: User,
        notification_id: str,
        status: str,
    ) -> AdminNotificationResponse:
        notification = await db.get(AdminNotification, notification_id)
        if not notification:
            raise ValueError("Notification not found.")

        notification.status = status
        now = datetime.utcnow()
        if status == "acknowledged":
            notification.acknowledged_at = now
        elif status == "resolved":
            notification.resolved_at = now
        await self.log_admin_action(
            db,
            actor=actor,
            action="admin.notification_updated",
            resource_type="admin_notification",
            resource_id=notification.id,
            details={"status": status, "title": notification.title},
        )
        await db.commit()
        await db.refresh(notification)
        return self._notification_response(notification)

    async def update_feature_flag(
        self,
        db: AsyncSession,
        *,
        actor: User,
        flag_key: str,
        is_enabled: bool,
        rollout_percentage: int,
    ) -> FeatureFlagResponse:
        await self.ensure_default_feature_flags(db)
        result = await db.execute(select(FeatureFlag).where(FeatureFlag.key == flag_key))
        flag = result.scalar_one_or_none()
        if not flag:
            raise ValueError("Feature flag not found.")

        flag.is_enabled = is_enabled
        flag.rollout_percentage = rollout_percentage
        flag.updated_at = datetime.utcnow()
        await self.log_admin_action(
            db,
            actor=actor,
            action="admin.feature_flag_updated",
            resource_type="feature_flag",
            resource_id=flag.key,
            details={
                "is_enabled": flag.is_enabled,
                "rollout_percentage": flag.rollout_percentage,
            },
        )
        await db.commit()
        await db.refresh(flag)
        return self._feature_flag_response(flag)

    async def ensure_default_feature_flags(self, db: AsyncSession) -> list[FeatureFlag]:
        result = await db.execute(select(FeatureFlag))
        existing = {flag.key: flag for flag in result.scalars().all()}
        created = False
        for payload in DEFAULT_FEATURE_FLAGS:
            if payload["key"] in existing:
                continue
            flag = FeatureFlag(id=str(uuid.uuid4()), **payload)
            db.add(flag)
            existing[payload["key"]] = flag
            created = True
        if created:
            await db.commit()
        return list(existing.values())

    async def log_admin_action(
        self,
        db: AsyncSession,
        *,
        actor: User,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        details: dict[str, Any] | None = None,
        severity: str = "info",
        status: str = "success",
    ) -> None:
        db.add(
            ActivityLog(
                id=str(uuid.uuid4()),
                actor_user_id=actor.id,
                actor_username=actor.username,
                actor_email=actor.email,
                action=action,
                status=status,
                severity=severity,
                resource_type=resource_type,
                resource_id=resource_id,
                details=details or {},
            )
        )

    def _resolve_range(
        self,
        *,
        range_name: str,
        start_date: date | None,
        end_date: date | None,
    ) -> DateRange:
        now = datetime.utcnow()
        normalized = (range_name or "month").lower()
        if normalized == "day":
            start = datetime.combine(now.date(), time.min)
        elif normalized == "week":
            start = datetime.combine((now - timedelta(days=now.weekday())).date(), time.min)
        elif normalized == "custom" and start_date and end_date:
            start = datetime.combine(start_date, time.min)
            end = datetime.combine(end_date, time.max)
            return DateRange(label="custom", start=start, end=end)
        else:
            start = datetime(now.year, now.month, 1)
            normalized = "month"
        return DateRange(label=normalized, start=start, end=now)

    async def _scalar_count(self, db: AsyncSession, query) -> int:
        result = await db.execute(query)
        return int(result.scalar_one() or 0)

    async def _count_users_created(self, db: AsyncSession, start: datetime, end: datetime) -> int:
        return await self._scalar_count(
            db,
            select(func.count()).select_from(User).where(
                User.created_at >= start,
                User.created_at < end,
            ),
        )

    async def _count_active_users(self, db: AsyncSession, start: datetime, end: datetime) -> int:
        return await self._scalar_count(
            db,
            select(func.count()).select_from(User).where(
                User.last_seen_at >= start,
                User.last_seen_at < end,
            ),
        )

    async def _count_activity(
        self,
        db: AsyncSession,
        start: datetime,
        end: datetime,
        *,
        action: str | None = None,
        failed_only: bool = False,
    ) -> int:
        filters = [ActivityLog.created_at >= start, ActivityLog.created_at < end]
        if action:
            filters.append(ActivityLog.action == action)
        if failed_only:
            filters.append(ActivityLog.response_status_code >= 400)
        return await self._scalar_count(
            db,
            select(func.count()).select_from(ActivityLog).where(*filters),
        )

    async def _count_security_action(
        self,
        db: AsyncSession,
        selected_range: DateRange,
        action: str,
    ) -> int:
        return await self._scalar_count(
            db,
            select(func.count()).select_from(ActivityLog).where(
                ActivityLog.created_at >= selected_range.start,
                ActivityLog.created_at < selected_range.end,
                ActivityLog.action == action,
            ),
        )

    async def _count_admin_route_denials(
        self,
        db: AsyncSession,
        selected_range: DateRange,
        *,
        sensitive_only: bool = False,
    ) -> int:
        filters = [
            ActivityLog.created_at >= selected_range.start,
            ActivityLog.created_at < selected_range.end,
            ActivityLog.endpoint.like("/admin%"),
            ActivityLog.response_status_code == 403,
        ]
        if sensitive_only:
            filters.append(
                or_(
                    ActivityLog.endpoint.like("%/role"),
                    ActivityLog.endpoint.like("%/feature-flags/%"),
                )
            )
        return await self._scalar_count(
            db,
            select(func.count()).select_from(ActivityLog).where(*filters),
        )

    async def _ai_usage(self, db: AsyncSession, selected_range: DateRange) -> dict[str, Any]:
        filters = [
            TokenUsageLog.created_at >= selected_range.start,
            TokenUsageLog.created_at < selected_range.end,
        ]
        totals_result = await db.execute(
            select(
                func.count(TokenUsageLog.id),
                func.coalesce(func.sum(TokenUsageLog.tokens_used), 0),
                func.coalesce(func.sum(TokenUsageLog.prompt_tokens), 0),
                func.coalesce(func.sum(TokenUsageLog.completion_tokens), 0),
                func.avg(TokenUsageLog.prompt_tokens),
            ).where(*filters)
        )
        total_requests, total_tokens, prompt_tokens, completion_tokens, avg_prompt_tokens = (
            totals_result.one()
        )
        message_result = await db.execute(
            select(func.avg(func.length(ChatMessage.user_prompt))).where(
                ChatMessage.created_at >= selected_range.start,
                ChatMessage.created_at < selected_range.end,
            )
        )
        avg_prompt_length = message_result.scalar_one() or 0
        return {
            "total_prompts": await self._scalar_count(
                db,
                select(func.count()).select_from(ChatMessage).where(
                    ChatMessage.created_at >= selected_range.start,
                    ChatMessage.created_at < selected_range.end,
                ),
            ),
            "total_ai_requests": int(total_requests or 0),
            "total_tokens": int(total_tokens or 0),
            "prompt_tokens": int(prompt_tokens or 0),
            "completion_tokens": int(completion_tokens or 0),
            "average_prompt_tokens": round(float(avg_prompt_tokens or 0), 1),
            "average_prompt_length": round(float(avg_prompt_length or 0), 1),
            "most_used_models": [
                {
                    "model": self.settings.openai_model,
                    "requests": int(total_requests or 0),
                    "tokens": int(total_tokens or 0),
                }
            ],
            "token_consumption_per_user": await self._top_users_by_tokens(db, selected_range, limit=8),
            "top_spending_users": await self._top_users_by_tokens(db, selected_range, limit=5),
            "users_close_to_token_limits": await self._users_close_to_token_limits(db),
        }

    async def _performance(
        self,
        db: AsyncSession,
        selected_range: DateRange,
        total_api_requests: int,
        failed_requests: int,
    ) -> dict[str, Any]:
        filters = [
            ActivityLog.created_at >= selected_range.start,
            ActivityLog.created_at < selected_range.end,
            ActivityLog.action == "request.completed",
        ]
        avg_result = await db.execute(
            select(func.avg(ActivityLog.response_time_ms)).where(*filters)
        )
        rate_limited = await self._scalar_count(
            db,
            select(func.count()).select_from(ActivityLog).where(
                *filters,
                ActivityLog.response_status_code == 429,
            ),
        )
        query_perf = await db.execute(
            select(
                func.avg(QueryLog.execution_time_ms),
                func.max(QueryLog.execution_time_ms),
                func.count(QueryLog.id),
            ).where(
                QueryLog.executed_at >= selected_range.start,
                QueryLog.executed_at < selected_range.end,
            )
        )
        avg_query_ms, max_query_ms, query_count = query_perf.one()
        return {
            "api_response_time_ms": round(float(avg_result.scalar_one() or 0), 1),
            "slowest_endpoints": await self._slowest_endpoints(db, selected_range),
            "most_used_endpoints": await self._most_used_endpoints(db, selected_range),
            "server_uptime_seconds": int((datetime.utcnow() - SERVICE_STARTED_AT).total_seconds()),
            "queue_jobs": {
                "enabled": False,
                "queued": 0,
                "running": 0,
                "failed": 0,
                "note": "No background job queue is configured.",
            },
            "database_query_performance": {
                "average_query_ms": round(float(avg_query_ms or 0), 1),
                "slowest_query_ms": int(max_query_ms or 0),
                "logged_queries": int(query_count or 0),
            },
            "failed_requests": failed_requests,
            "rate_limited_requests": rate_limited,
            "error_rate": round((failed_requests / total_api_requests) * 100, 2)
            if total_api_requests
            else 0,
        }

    async def _system_health(self, db: AsyncSession, performance: dict[str, Any]) -> dict[str, Any]:
        database_ok = await check_db_health()
        connection_count = await self._scalar_count(
            db,
            select(func.count()).select_from(DatabaseConnection).where(
                DatabaseConnection.is_active.is_(True)
            ),
        )
        status_value = "healthy"
        if not database_ok or performance["error_rate"] >= 10:
            status_value = "degraded"
        if performance["error_rate"] >= 25:
            status_value = "critical"
        return {
            "status": status_value,
            "database": database_ok,
            "openai_configured": self.settings.openai_configured,
            "active_connections": connection_count,
            "server_uptime_seconds": performance["server_uptime_seconds"],
            "error_rate": performance["error_rate"],
        }

    async def _retention(
        self,
        db: AsyncSession,
        selected_range: DateRange,
        total_users: int,
    ) -> dict[str, Any]:
        prior_users = await self._scalar_count(
            db,
            select(func.count()).select_from(User).where(User.created_at < selected_range.start),
        )
        returning_users = await self._scalar_count(
            db,
            select(func.count()).select_from(User).where(
                User.created_at < selected_range.start,
                User.last_seen_at >= selected_range.start,
                User.last_seen_at < selected_range.end,
            ),
        )
        inactive_cutoff = datetime.utcnow() - timedelta(days=30)
        inactive_users = await self._scalar_count(
            db,
            select(func.count()).select_from(User).where(
                or_(User.last_seen_at.is_(None), User.last_seen_at < inactive_cutoff)
            ),
        )
        return {
            "returning_users": returning_users,
            "new_users": await self._count_users_created(db, selected_range.start, selected_range.end),
            "retention_rate": round((returning_users / prior_users) * 100, 2)
            if prior_users
            else 0,
            "inactive_users": inactive_users,
            "churn_rate": round((inactive_users / total_users) * 100, 2)
            if total_users
            else 0,
            "drop_off_rate": round((inactive_users / total_users) * 100, 2)
            if total_users
            else 0,
        }

    async def _engagement_trends(self, db: AsyncSession, selected_range: DateRange) -> dict[str, Any]:
        return {
            "signups": await self._daily_count(
                db,
                User.created_at,
                selected_range,
                select(func.date(User.created_at), func.count()).select_from(User),
            ),
            "api_requests": await self._daily_count(
                db,
                ActivityLog.created_at,
                selected_range,
                select(func.date(ActivityLog.created_at), func.count()).select_from(ActivityLog),
                ActivityLog.action == "request.completed",
            ),
            "ai_requests": await self._daily_count(
                db,
                TokenUsageLog.created_at,
                selected_range,
                select(func.date(TokenUsageLog.created_at), func.count()).select_from(TokenUsageLog),
            ),
            "token_usage": await self._daily_sum(
                db,
                TokenUsageLog.created_at,
                TokenUsageLog.tokens_used,
                selected_range,
            ),
        }

    async def _daily_count(self, db: AsyncSession, column, selected_range: DateRange, query, *extra_filters):
        result = await db.execute(
            query.where(
                column >= selected_range.start,
                column < selected_range.end,
                *extra_filters,
            ).group_by(func.date(column)).order_by(func.date(column))
        )
        return [
            {"date": str(day), "value": int(count or 0)}
            for day, count in result.all()
        ]

    async def _daily_sum(
        self,
        db: AsyncSession,
        date_column,
        value_column,
        selected_range: DateRange,
    ) -> list[dict[str, Any]]:
        result = await db.execute(
            select(func.date(date_column), func.coalesce(func.sum(value_column), 0))
            .where(date_column >= selected_range.start, date_column < selected_range.end)
            .group_by(func.date(date_column))
            .order_by(func.date(date_column))
        )
        return [{"date": str(day), "value": int(value or 0)} for day, value in result.all()]

    async def _top_users_by_activity(
        self,
        db: AsyncSession,
        selected_range: DateRange,
        limit: int = 6,
    ) -> list[dict[str, Any]]:
        result = await db.execute(
            select(User.id, User.username, User.email, func.count(ActivityLog.id).label("value"))
            .join(ActivityLog, ActivityLog.actor_user_id == User.id)
            .where(
                ActivityLog.created_at >= selected_range.start,
                ActivityLog.created_at < selected_range.end,
            )
            .group_by(User.id, User.username, User.email)
            .order_by(desc("value"))
            .limit(limit)
        )
        return [self._top_user_payload(row) for row in result.all()]

    async def _top_users_by_login(
        self,
        db: AsyncSession,
        selected_range: DateRange,
        limit: int = 6,
    ) -> list[dict[str, Any]]:
        result = await db.execute(
            select(User.id, User.username, User.email, func.count(ActivityLog.id).label("value"))
            .join(ActivityLog, ActivityLog.actor_user_id == User.id)
            .where(
                ActivityLog.created_at >= selected_range.start,
                ActivityLog.created_at < selected_range.end,
                ActivityLog.endpoint.like("/auth/%"),
            )
            .group_by(User.id, User.username, User.email)
            .order_by(desc("value"))
            .limit(limit)
        )
        return [self._top_user_payload(row) for row in result.all()]

    async def _top_users_by_tokens(
        self,
        db: AsyncSession,
        selected_range: DateRange,
        limit: int = 6,
    ) -> list[dict[str, Any]]:
        result = await db.execute(
            select(User.id, User.username, User.email, func.coalesce(func.sum(TokenUsageLog.tokens_used), 0).label("value"))
            .join(TokenUsageLog, TokenUsageLog.user_id == User.id)
            .where(
                TokenUsageLog.created_at >= selected_range.start,
                TokenUsageLog.created_at < selected_range.end,
            )
            .group_by(User.id, User.username, User.email)
            .order_by(desc("value"))
            .limit(limit)
        )
        return [self._top_user_payload(row) for row in result.all()]

    async def _top_users_by_ai_requests(
        self,
        db: AsyncSession,
        selected_range: DateRange,
        limit: int = 6,
    ) -> list[dict[str, Any]]:
        result = await db.execute(
            select(User.id, User.username, User.email, func.count(TokenUsageLog.id).label("value"))
            .join(TokenUsageLog, TokenUsageLog.user_id == User.id)
            .where(
                TokenUsageLog.created_at >= selected_range.start,
                TokenUsageLog.created_at < selected_range.end,
            )
            .group_by(User.id, User.username, User.email)
            .order_by(desc("value"))
            .limit(limit)
        )
        return [self._top_user_payload(row) for row in result.all()]

    async def _slowest_endpoints(self, db: AsyncSession, selected_range: DateRange) -> list[dict[str, Any]]:
        result = await db.execute(
            select(
                ActivityLog.endpoint,
                func.avg(ActivityLog.response_time_ms).label("avg_ms"),
                func.max(ActivityLog.response_time_ms).label("max_ms"),
                func.count(ActivityLog.id).label("requests"),
            )
            .where(
                ActivityLog.created_at >= selected_range.start,
                ActivityLog.created_at < selected_range.end,
                ActivityLog.action == "request.completed",
                ActivityLog.endpoint.is_not(None),
            )
            .group_by(ActivityLog.endpoint)
            .order_by(desc("avg_ms"))
            .limit(8)
        )
        return [
            {
                "endpoint": endpoint,
                "average_ms": round(float(avg_ms or 0), 1),
                "max_ms": int(max_ms or 0),
                "requests": int(requests or 0),
            }
            for endpoint, avg_ms, max_ms, requests in result.all()
        ]

    async def _most_used_endpoints(self, db: AsyncSession, selected_range: DateRange) -> list[dict[str, Any]]:
        result = await db.execute(
            select(ActivityLog.endpoint, func.count(ActivityLog.id).label("requests"))
            .where(
                ActivityLog.created_at >= selected_range.start,
                ActivityLog.created_at < selected_range.end,
                ActivityLog.action == "request.completed",
                ActivityLog.endpoint.is_not(None),
            )
            .group_by(ActivityLog.endpoint)
            .order_by(desc("requests"))
            .limit(8)
        )
        return [
            {"endpoint": endpoint, "requests": int(requests or 0)}
            for endpoint, requests in result.all()
        ]

    async def _notifications(
        self,
        db: AsyncSession,
        performance: dict[str, Any],
        ai_usage: dict[str, Any],
        active_users: dict[str, Any],
    ) -> list[dict[str, Any]]:
        persisted = await self.list_notifications(db, status="unread")
        dynamic = []
        if performance["error_rate"] >= 10:
            dynamic.append(
                {
                    "id": "dynamic-error-rate",
                    "title": "Elevated API error rate",
                    "message": f"{performance['error_rate']}% of API requests are failing.",
                    "severity": "error" if performance["error_rate"] >= 25 else "warning",
                    "category": "performance",
                    "status": "unread",
                    "metadata": {"error_rate": performance["error_rate"]},
                    "created_at": datetime.utcnow(),
                    "acknowledged_at": None,
                    "resolved_at": None,
                }
            )
        if performance["rate_limited_requests"] > 0:
            dynamic.append(
                {
                    "id": "dynamic-rate-limits",
                    "title": "Rate limiting active",
                    "message": f"{performance['rate_limited_requests']} requests were rate limited.",
                    "severity": "warning",
                    "category": "security",
                    "status": "unread",
                    "metadata": {"rate_limited_requests": performance["rate_limited_requests"]},
                    "created_at": datetime.utcnow(),
                    "acknowledged_at": None,
                    "resolved_at": None,
                }
            )
        if ai_usage["users_close_to_token_limits"]:
            dynamic.append(
                {
                    "id": "dynamic-token-limits",
                    "title": "Users close to token limits",
                    "message": f"{len(ai_usage['users_close_to_token_limits'])} users are nearing token limits.",
                    "severity": "warning",
                    "category": "tokens",
                    "status": "unread",
                    "metadata": {"users": ai_usage["users_close_to_token_limits"][:5]},
                    "created_at": datetime.utcnow(),
                    "acknowledged_at": None,
                    "resolved_at": None,
                }
            )
        if active_users["online_now"] == 0:
            dynamic.append(
                {
                    "id": "dynamic-online-users",
                    "title": "No active sessions detected",
                    "message": "No users have made an authenticated request in the last five minutes.",
                    "severity": "info",
                    "category": "engagement",
                    "status": "unread",
                    "metadata": {},
                    "created_at": datetime.utcnow(),
                    "acknowledged_at": None,
                    "resolved_at": None,
                }
            )
        return [item.model_dump() for item in persisted] + dynamic

    async def _recent_users(self, db: AsyncSession) -> list[dict[str, Any]]:
        result = await db.execute(select(User).order_by(User.created_at.desc()).limit(8))
        return [self._admin_user_response(user).model_dump() for user in result.scalars().all()]

    async def _recent_failed_actions(self, db: AsyncSession) -> list[dict[str, Any]]:
        result = await db.execute(
            select(ActivityLog)
            .where(ActivityLog.severity.in_(["warning", "error", "critical"]))
            .order_by(ActivityLog.created_at.desc())
            .limit(8)
        )
        return [self._activity_log_response(log).model_dump() for log in result.scalars().all()]

    async def _recent_token_adjustments(self, db: AsyncSession) -> list[dict[str, Any]]:
        page = await self.list_token_adjustments(db, page=1, page_size=8)
        return [item.model_dump() for item in page["items"]]

    async def _average_session_duration(self, db: AsyncSession, selected_range: DateRange) -> int:
        result = await db.execute(
            select(
                ActivityLog.session_id,
                func.min(ActivityLog.created_at),
                func.max(ActivityLog.created_at),
            )
            .where(
                ActivityLog.created_at >= selected_range.start,
                ActivityLog.created_at < selected_range.end,
                ActivityLog.session_id.is_not(None),
            )
            .group_by(ActivityLog.session_id)
        )
        durations = [
            int((max_time - min_time).total_seconds())
            for _, min_time, max_time in result.all()
            if min_time and max_time and max_time > min_time
        ]
        if not durations:
            return 0
        return int(sum(durations) / len(durations))

    async def _user_metric_maps(self, db: AsyncSession, user_ids: list[str]) -> dict[str, dict[str, int]]:
        if not user_ids:
            return {"actions": {}, "ai_requests": {}, "tokens": {}}
        actions = await self._group_counts(db, ActivityLog.actor_user_id, ActivityLog.id, user_ids)
        ai_requests = await self._group_counts(db, TokenUsageLog.user_id, TokenUsageLog.id, user_ids)
        token_result = await db.execute(
            select(TokenUsageLog.user_id, func.coalesce(func.sum(TokenUsageLog.tokens_used), 0))
            .where(TokenUsageLog.user_id.in_(user_ids))
            .group_by(TokenUsageLog.user_id)
        )
        tokens = {user_id: int(total or 0) for user_id, total in token_result.all()}
        return {"actions": actions, "ai_requests": ai_requests, "tokens": tokens}

    async def _group_counts(self, db: AsyncSession, key_column, value_column, user_ids: list[str]) -> dict[str, int]:
        result = await db.execute(
            select(key_column, func.count(value_column)).where(key_column.in_(user_ids)).group_by(key_column)
        )
        return {key: int(value or 0) for key, value in result.all()}

    async def _users_by_id(self, db: AsyncSession, user_ids: list[str]) -> dict[str, User]:
        if not user_ids:
            return {}
        result = await db.execute(select(User).where(User.id.in_(user_ids)))
        return {user.id: user for user in result.scalars().all()}

    async def _get_user_or_raise(self, db: AsyncSession, user_id: str) -> User:
        user = await db.get(User, user_id)
        if not user:
            raise ValueError("User not found.")
        return user

    async def _users_close_to_token_limits(self, db: AsyncSession) -> list[dict[str, Any]]:
        result = await db.execute(select(User).where(User.is_active.is_(True)).limit(1000))
        close_users = []
        for user in result.scalars().all():
            limit = user.daily_token_limit
            remaining = user.remaining_tokens
            if limit is None or remaining is None:
                continue
            if limit > 0 and remaining / limit <= 0.15:
                close_users.append(
                    {
                        "id": user.id,
                        "username": user.username,
                        "email": user.email,
                        "remaining_tokens": remaining,
                        "daily_token_limit": limit,
                        "usage_percent": round(((limit - remaining) / limit) * 100, 1),
                    }
                )
        return sorted(close_users, key=lambda item: item["remaining_tokens"])[:10]

    def _admin_user_response(
        self,
        user: User,
        *,
        action_count: int = 0,
        ai_request_count: int = 0,
        tokens_used: int = 0,
    ) -> AdminUserResponse:
        payload = AdminUserResponse.model_validate(user)
        if self._is_bootstrap_user(user):
            payload.is_admin = True
            payload.is_super_admin = True
            payload.admin_role = "super_admin"
        payload.action_count = action_count
        payload.ai_request_count = ai_request_count
        payload.tokens_used = tokens_used
        return payload

    def _activity_log_response(self, log: ActivityLog) -> ActivityLogResponse:
        actor = log.actor_username or log.actor_email or "System"
        summary = f"{actor} performed {log.action} at {log.created_at.isoformat()}"
        return ActivityLogResponse(
            id=log.id,
            actor_user_id=log.actor_user_id,
            actor_username=log.actor_username,
            actor_email=log.actor_email,
            action=log.action,
            status=log.status,
            severity=log.severity,
            resource_type=log.resource_type,
            resource_id=log.resource_id,
            endpoint=log.endpoint,
            method=log.method,
            ip_address=log.ip_address,
            user_agent=log.user_agent,
            request_payload=log.request_payload or {},
            response_status_code=log.response_status_code,
            response_time_ms=log.response_time_ms,
            error_trace=log.error_trace,
            session_id=log.session_id,
            geo_location=log.geo_location,
            details=log.details or {},
            created_at=log.created_at,
            summary=summary,
        )

    def _token_adjustment_response(
        self,
        adjustment: TokenAdjustmentLog,
        *,
        user: User | None = None,
        admin_user: User | None = None,
    ) -> TokenAdjustmentResponse:
        return TokenAdjustmentResponse(
            id=adjustment.id,
            user_id=adjustment.user_id,
            admin_user_id=adjustment.admin_user_id,
            adjustment_type=adjustment.adjustment_type,
            amount=adjustment.amount,
            balance_before=adjustment.balance_before,
            balance_after=adjustment.balance_after,
            reason=adjustment.reason,
            created_at=adjustment.created_at,
            user_email=getattr(user, "email", None),
            admin_email=getattr(admin_user, "email", None),
        )

    def _notification_response(self, notification: AdminNotification) -> AdminNotificationResponse:
        return AdminNotificationResponse(
            id=notification.id,
            title=notification.title,
            message=notification.message,
            severity=notification.severity,
            category=notification.category,
            status=notification.status,
            metadata=notification.metadata_json or {},
            created_at=notification.created_at,
            acknowledged_at=notification.acknowledged_at,
            resolved_at=notification.resolved_at,
        )

    def _feature_flag_response(self, flag: FeatureFlag) -> FeatureFlagResponse:
        return FeatureFlagResponse.model_validate(flag)

    def _available_tokens(self, user: User) -> int | None:
        return user.remaining_tokens

    def _top_user_payload(self, row) -> dict[str, Any]:
        user_id, username, email, value = row
        return {
            "id": user_id,
            "username": username,
            "email": email,
            "value": int(value or 0),
        }

    def _activity_filters(
        self,
        *,
        search: str | None,
        severity: str | None,
        action: str | None,
        user_id: str | None,
    ) -> list[Any]:
        filters = []
        if search:
            pattern = f"%{search.strip().lower()}%"
            filters.append(
                or_(
                    func.lower(ActivityLog.action).like(pattern),
                    func.lower(ActivityLog.actor_email).like(pattern),
                    func.lower(ActivityLog.actor_username).like(pattern),
                    func.lower(ActivityLog.endpoint).like(pattern),
                    func.lower(ActivityLog.resource_id).like(pattern),
                )
            )
        if severity:
            filters.append(ActivityLog.severity == severity)
        if action:
            filters.append(ActivityLog.action == action)
        if user_id:
            filters.append(ActivityLog.actor_user_id == user_id)
        return filters

    def _growth_percent(self, current: int, previous: int) -> dict[str, Any]:
        if previous == 0:
            percent = 100 if current > 0 else 0
        else:
            percent = round(((current - previous) / previous) * 100, 2)
        return {
            "current": current,
            "previous": previous,
            "percent": percent,
            "direction": "increase" if percent >= 0 else "decrease",
        }

    def _previous_month_start(self, current_month_start: datetime) -> datetime:
        previous_month_end = current_month_start - timedelta(days=1)
        return datetime(previous_month_end.year, previous_month_end.month, 1)

    def _total_pages(self, total_items: int, page_size: int) -> int:
        if total_items == 0:
            return 1
        return (total_items + page_size - 1) // page_size

    def _bootstrap_emails(self) -> set[str]:
        return {
            email.strip().lower()
            for email in self.settings.admin_bootstrap_emails
            if email.strip()
        }

    def _is_bootstrap_user(self, user: User) -> bool:
        return (user.email or "").strip().lower() in self._bootstrap_emails()

    def _admin_user_filter(self):
        bootstrap_emails = self._bootstrap_emails()
        conditions = [User.is_admin.is_(True), User.is_super_admin.is_(True)]
        if bootstrap_emails:
            conditions.append(func.lower(User.email).in_(bootstrap_emails))
        return or_(*conditions)

    def _super_admin_user_filter(self):
        bootstrap_emails = self._bootstrap_emails()
        conditions = [User.is_super_admin.is_(True)]
        if bootstrap_emails:
            conditions.append(func.lower(User.email).in_(bootstrap_emails))
        return or_(*conditions)

    def _not_bootstrap_email_filter(self):
        bootstrap_emails = self._bootstrap_emails()
        if not bootstrap_emails:
            return True
        return ~func.lower(User.email).in_(bootstrap_emails)


admin_dashboard_service = AdminDashboardService()
