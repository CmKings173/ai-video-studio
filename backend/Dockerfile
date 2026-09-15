FROM ghcr.io/astral-sh/uv:0.11.32 AS uv
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
WORKDIR /app

COPY --from=uv /uv /uvx /bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

COPY apps ./apps
COPY workers ./workers
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
COPY workflows ./workflows
COPY pyproject.toml uv.lock ./
RUN uv pip install --system --no-cache .

RUN useradd --create-home --uid 10001 studio \
    && mkdir -p /workspace \
    && chown -R studio:studio /app /workspace
USER studio

CMD ["uvicorn", "apps.api.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
