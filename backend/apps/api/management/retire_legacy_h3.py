"""Retire exactly five legacy H3 identities, preserving any history found at apply time.

Dry run is the default. The operator must back up the original application DB before --apply.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import JSON, MetaData, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.api.app.core.config import get_settings
from apps.api.app.db.base import Base
from apps.api.app.db.models import WorkflowRecord
from apps.api.app.providers.minimax_h3_director.graph_identity import is_director_graph

LEGACY_H3_CODES = frozenset(
    {
        "H3_T2V_STANDARD",
        "H3_I2V_STANDARD",
        "H3_FIRST_LAST_STANDARD",
        "H3_R2V_1_IMAGE_REFERENCE",
        "H3_LAST_FRAME_STANDARD",
    }
)


def _contains_identity(value, identities):
    if isinstance(value, str):
        return value in identities
    if isinstance(value, dict):
        return any(_contains_identity(item, identities) for item in value.values())
    if isinstance(value, list):
        return any(_contains_identity(item, identities) for item in value)
    return False


async def _dependency_tables(session):
    connection = await session.connection()
    if connection.dialect.name == "postgresql":
        metadata = MetaData()
        await connection.run_sync(lambda sync: metadata.reflect(bind=sync))
        return list(metadata.tables.values())
    return list(Base.metadata.tables.values())


async def retire_legacy_h3(session, *, apply=False):
    """Caller owns a READ COMMITTED transaction; never commits or repoints history.

    PostgreSQL FOR UPDATE conflicts with FK insert KEY SHARE locks. JSON-bearing
    tables are locked in SHARE mode before workflow rows to close JSON-only write races.
    The following dependency queries therefore see any writer committed before locking.
    """
    tables = await _dependency_tables(session)
    json_tables = [
        table
        for table in tables
        if table.name != "workflow_registry"
        and any(isinstance(column.type, JSON) for column in table.columns)
    ]
    connection = await session.connection()
    if apply and connection.dialect.name == "postgresql":
        await session.execute(text("SET LOCAL lock_timeout = '15s'"))
        await session.execute(text("SET LOCAL statement_timeout = '60s'"))
        # Snapshot isolation would miss a dependency committed while waiting for a row.
        isolation = await session.scalar(text("SHOW transaction_isolation"))
        if isolation != "read committed":
            raise RuntimeError("Retirement apply requires READ COMMITTED isolation")
        preparer = connection.dialect.identifier_preparer
        for table in sorted(json_tables, key=lambda table: table.fullname):
            await session.execute(text(f"LOCK TABLE {preparer.format_table(table)} IN SHARE MODE"))
    query = select(WorkflowRecord).where(WorkflowRecord.code.in_(LEGACY_H3_CODES))
    query = query.order_by(WorkflowRecord.id)
    if apply:
        query = query.with_for_update()
    records = list((await session.scalars(query)).all())
    result = []
    for record in records:
        if is_director_graph(record.workflow):
            raise RuntimeError("Refusing to retire a Director graph under a legacy code")
        fk_dependencies = {}
        for table in tables:
            for fk in table.foreign_keys:
                if fk.column.table.name == "workflow_registry" and fk.column.name == "id":
                    count = await session.scalar(
                        select(func.count()).select_from(table).where(fk.parent == record.id)
                    )
                    if count:
                        fk_dependencies[f"{table.fullname}.{fk.parent.name}"] = count
        identities = {record.id, record.code, record.workflow_hash, record.slot_map_hash} - {""}
        json_dependencies = {}
        for table in json_tables:
            for column in table.columns:
                if not isinstance(column.type, JSON):
                    continue
                count = 0
                values = await session.stream_scalars(select(column))
                async for value in values:
                    if _contains_identity(value, identities):
                        count += 1
                if count:
                    json_dependencies[f"{table.fullname}.{column.name}"] = count
        referenced = bool(fk_dependencies or json_dependencies)
        result.append(
            {
                "id": record.id,
                "code": record.code,
                "version": record.version,
                "enabled_before": record.enabled,
                "fk_dependencies": fk_dependencies,
                "json_dependencies": json_dependencies,
                "action": "retain_historical_identity_disable"
                if referenced
                else "delete_unreferenced",
            }
        )
        if apply:
            if referenced:
                record.enabled = False
            else:
                await session.delete(record)
    if apply:
        await session.flush()
    return result


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Recheck and retire in one transaction"
    )
    args = parser.parse_args()
    url = make_url(get_settings().database_url)
    print(json.dumps({"host": url.host, "port": url.port, "database": url.database}))
    engine = create_async_engine(url, isolation_level="READ COMMITTED")
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session, session.begin():
            result = await retire_legacy_h3(session, apply=args.apply)
        print(json.dumps({"applied": args.apply, "historical_rows": result}, indent=2))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
