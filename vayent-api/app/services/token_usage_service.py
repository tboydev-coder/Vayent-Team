"""Plan-based AI token tracking and enforcement."""
from __future__ import annotations

import math
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import TokenUsageLog, User


class TokenLimitExceededError(Exception):
    """Raised when a user has exhausted their token allowance."""


class TokenUsageService:
    """Reserve, finalize, and reset token usage counters safely."""

    def __init__(self) -> None:
        self.settings = get_settings()

    def estimate_request_tokens(
        self,
        *parts: Any,
        completion_allowance: int | None = None,
    ) -> int:
        """Approximate token usage before an AI call is made."""
        serialized = []
        for part in parts:
            if part is None:
                continue
            if isinstance(part, str):
                serialized.append(part)
            else:
                serialized.append(str(part))

        payload_length = sum(len(chunk) for chunk in serialized)
        prompt_estimate = math.ceil(payload_length / 4) if payload_length else 0
        return prompt_estimate + (
            completion_allowance or self.settings.chat_completion_token_budget
        )

    def _period_start(self, reference: date | None = None) -> date:
        return reference or datetime.utcnow().date()

    def _plan_type(self, user: User) -> str:
        return user.effective_plan_type

    def get_limit_for_user(self, user: User) -> int | None:
        manual_balance = getattr(user, "manual_token_balance", 0) or 0
        if self._plan_type(user) == "paid":
            base_limit = self.settings.paid_daily_token_limit or None
        else:
            base_limit = self.settings.free_daily_token_limit

        if base_limit is None:
            return None
        return base_limit + manual_balance

    def _reset_usage_if_needed(self, user: User) -> None:
        period_start = self._period_start()
        if user.token_reset_date != period_start:
            user.monthly_token_usage = 0
            user.reserved_token_usage = 0
            user.token_reset_date = period_start

        if not user.plan_type:
            user.plan_type = "paid" if user.is_premium else "free"

        if getattr(user, "manual_token_balance", 0) is None:
            user.manual_token_balance = 0

    async def sync_user_usage_window(self, user_id: str, db: AsyncSession) -> User | None:
        """Refresh the user's billing window when read-only endpoints fetch profile data."""
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            return None

        original_reset_date = user.token_reset_date
        original_usage = user.monthly_token_usage
        original_reserved = user.reserved_token_usage
        original_plan = user.plan_type

        self._reset_usage_if_needed(user)

        if (
            user.token_reset_date != original_reset_date
            or user.monthly_token_usage != original_usage
            or user.reserved_token_usage != original_reserved
            or user.plan_type != original_plan
        ):
            await db.commit()
            await db.refresh(user)

        return user

    async def _lock_user(self, user_id: str, db: AsyncSession) -> User:
        result = await db.execute(
            select(User).where(User.id == user_id).with_for_update()
        )
        user = result.scalar_one()
        self._reset_usage_if_needed(user)
        return user

    async def reserve_tokens(
        self,
        user_id: str,
        estimated_tokens: int,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Reserve tokens before a request reaches the AI service."""
        if estimated_tokens <= 0:
            return {"reserved_tokens": 0}

        try:
            user = await self._lock_user(user_id, db)
            limit = self.get_limit_for_user(user)
            committed = user.monthly_token_usage or 0
            reserved = user.reserved_token_usage or 0
            total_in_flight = committed + reserved

            if limit is not None and total_in_flight + estimated_tokens > limit:
                remaining = max(limit - total_in_flight, 0)
                await db.rollback()
                raise TokenLimitExceededError(
                    f"You have reached your daily token limit of {limit:,}. Upgrade your plan to continue."
                    if remaining == 0
                    else (
                        f"This request would exceed your daily token limit of {limit:,}. "
                        f"You have {remaining:,} tokens left today."
                    )
                )

            user.reserved_token_usage = reserved + estimated_tokens
            await db.commit()

            return {
                "reserved_tokens": estimated_tokens,
                "plan_type": self._plan_type(user),
                "limit": limit,
            }
        except Exception:
            if db.in_transaction():
                await db.rollback()
            raise

    async def release_tokens(
        self,
        user_id: str,
        reserved_tokens: int,
        db: AsyncSession,
    ) -> None:
        """Release reserved tokens when a request fails before completion."""
        if reserved_tokens <= 0:
            return

        try:
            user = await self._lock_user(user_id, db)
            current_reserved = user.reserved_token_usage or 0
            user.reserved_token_usage = max(current_reserved - reserved_tokens, 0)
            await db.commit()
        except Exception:
            if db.in_transaction():
                await db.rollback()
            raise

    async def finalize_tokens(
        self,
        user_id: str,
        reserved_tokens: int,
        prompt_tokens: int,
        completion_tokens: int,
        db: AsyncSession,
        session_id: str | None = None,
        message_id: str | None = None,
        request_kind: str = "chat",
    ) -> dict[str, Any]:
        """Move a reservation into committed usage and persist the audit log."""
        total_tokens = max(prompt_tokens + completion_tokens, 0)

        try:
            user = await self._lock_user(user_id, db)
            current_reserved = user.reserved_token_usage or 0
            user.reserved_token_usage = max(current_reserved - reserved_tokens, 0)
            user.monthly_token_usage = (user.monthly_token_usage or 0) + total_tokens

            usage_log = TokenUsageLog(
                id=str(uuid.uuid4()),
                user_id=user_id,
                session_id=session_id,
                message_id=message_id,
                request_kind=request_kind,
                tokens_used=total_tokens,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
            db.add(usage_log)
            await db.commit()

            limit = self.get_limit_for_user(user)
            remaining_tokens = None
            if limit is not None:
                remaining_tokens = max(limit - (user.monthly_token_usage or 0), 0)

            return {
                "total_tokens": total_tokens,
                "remaining_tokens": remaining_tokens,
                "daily_token_usage": user.monthly_token_usage,
                "plan_type": self._plan_type(user),
            }
        except Exception:
            if db.in_transaction():
                await db.rollback()
            raise


token_usage_service = TokenUsageService()
