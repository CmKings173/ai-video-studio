"""Refresh locked rows without discarding verified edits in the current unit of work."""

from copy import deepcopy
from typing import TypeVar

from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.errors import AppError

T = TypeVar("T")


async def lock_revisioned_row(
    session: AsyncSession,
    model: type[T],
    identity: str,
    *,
    immutable_fields: tuple[str, ...] = (),
) -> T | None:
    """Caller chooses canonical lock order; this function never flushes.

    A clean identity-map entry can be refreshed directly. For a dirty entry,
    lock a column projection first so SQLAlchemy cannot erase its history.
    Accept its pending intent only against the original revision and original
    values of every changed field. Unknown baselines fail closed. The caller's
    normal persistence boundary writes the edits after all locks/validation.
    """
    mapper = inspect(model)
    current = session.identity_map.get(mapper.identity_key_from_primary_key((identity,)))
    changes = {}
    baseline = {}
    if current is not None and session.is_modified(current, include_collections=True):
        state = inspect(current)
        for attribute in mapper.column_attrs:
            history = state.attrs[attribute.key].history
            if history.has_changes():
                changes[attribute.key] = deepcopy(getattr(current, attribute.key))
                if history.deleted:
                    baseline[attribute.key] = deepcopy(history.deleted[0])
        revision_history = state.attrs.revision.history
        original_revision = (
            revision_history.deleted[0] if revision_history.deleted else state.dict.get("revision")
        )
        forbidden = {column.key for column in mapper.primary_key} | set(immutable_fields)
        with session.no_autoflush:
            database = (
                (
                    await session.execute(
                        select(*model.__table__.c).where(model.id == identity).with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
        if database is None:
            return None
        if (
            original_revision is None
            or database["revision"] != original_revision
            or forbidden.intersection(changes)
            or any(key not in baseline or database[key] != baseline[key] for key in changes)
        ):
            raise AppError(
                "REVISION_CONFLICT",
                "Pending edits conflict with the locked database row",
                412,
                {"entity": model.__name__, "id": identity},
            )
    with session.no_autoflush:
        locked = await session.scalar(
            select(model)
            .where(model.id == identity)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    if locked is not None:
        for key, value in changes.items():
            setattr(locked, key, value)
    return locked
