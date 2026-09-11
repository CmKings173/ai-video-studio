"""Operator health report for database, object storage, ComfyUI and worker leases."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime

from sqlalchemy import func, select, text

from apps.api.app.core.config import get_settings
from apps.api.app.db.models import FinalVideo, SceneGeneration
from apps.api.app.db.session import SessionFactory
from apps.api.app.integrations.comfy_adapter import ComfyAdapter
from apps.api.app.integrations.minio import AssetStore


async def report() -> dict:
    settings = get_settings()
    postgres = False
    queue_counts: dict[str, int] = {}
    stale_leases = 0
    try:
        async with SessionFactory() as session:
            await session.execute(text("SELECT 1"))
            postgres = True
            queue_counts["generation_queued"] = (
                await session.scalar(
                    select(func.count())
                    .select_from(SceneGeneration)
                    .where(SceneGeneration.status.in_({"CREATED", "QUEUED"}))
                )
                or 0
            )
            queue_counts["assembly_queued"] = (
                await session.scalar(
                    select(func.count())
                    .select_from(FinalVideo)
                    .where(FinalVideo.status == "QUEUED")
                )
                or 0
            )
            stale_leases = (
                await session.scalar(
                    select(func.count())
                    .select_from(SceneGeneration)
                    .where(
                        SceneGeneration.lease_expires_at.is_not(None),
                        SceneGeneration.lease_expires_at < func.now(),
                        SceneGeneration.status.not_in({"COMPLETED", "FAILED", "CANCELLED"}),
                    )
                )
                or 0
            )
    except Exception:
        postgres = False
    minio, comfyui = await asyncio.gather(
        AssetStore(settings).health(), ComfyAdapter(settings).health()
    )
    return {
        "checked_at": datetime.now(UTC).isoformat(),
        "postgres": postgres,
        "minio": minio,
        "comfyui": comfyui,
        "queue_counts": queue_counts,
        "stale_generation_leases": stale_leases,
        "healthy": postgres and minio and comfyui and stale_leases == 0,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a summary")
    args = parser.parse_args()
    value = await report()
    print(json.dumps(value, ensure_ascii=False, sort_keys=True) if args.json else value)
    return 0 if value["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
