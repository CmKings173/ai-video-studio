"""Shared, runtime-configured retention eligibility rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any


@dataclass(frozen=True)
class RetentionPolicy:
    pending_hours: int
    failed_hours: int
    deleted_hours: int

    @classmethod
    def from_settings(cls, settings: Any) -> RetentionPolicy:
        return cls(
            pending_hours=int(getattr(settings, "pending_asset_retention_hours", 24)),
            failed_hours=int(getattr(settings, "retention_failed_hours", 24)),
            deleted_hours=int(getattr(settings, "deleted_asset_retention_hours", 168)),
        )

    def eligible(self, asset: Any, now: datetime) -> bool:
        status = asset.status
        if status == "DELETED":
            timestamp = asset.deleted_at
            age = self.deleted_hours
        elif status in {"PENDING", "PENDING_UPLOAD", "VALIDATING"}:
            timestamp = asset.created_at
            age = self.pending_hours
        elif status == "FAILED":
            timestamp = asset.created_at
            age = self.failed_hours
        else:
            return False
        return timestamp is not None and timestamp < now - timedelta(hours=age)
