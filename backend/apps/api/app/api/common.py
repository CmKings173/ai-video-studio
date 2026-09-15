from fastapi import Query


def pagination(
    page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100)
) -> tuple[int, int]:
    return page, page_size


def apply_patch(model, payload, expected: int) -> None:
    if model.revision != expected:
        from apps.api.app.core.errors import AppError

        raise AppError(
            "REVISION_CONFLICT",
            "Resource changed since it was loaded",
            412,
            {"expected": expected, "actual": model.revision},
        )
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(model, field, value)
    model.revision += 1
