# Official multi-platform index; Docker selects the target platform's binaries.
# https://docs.astral.sh/uv/guides/integration/docker/#installing-uv
FROM ghcr.io/astral-sh/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c AS uv
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /app

COPY --from=uv /uv /uvx /bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

# Consume the lock before source changes invalidate the dependency layer.
# Source imports from /app; skip the project and its unlocked Hatch build requirements.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --no-cache

COPY apps ./apps
COPY workers ./workers
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
COPY workflows ./workflows

RUN useradd --create-home --uid 10001 studio \
    && mkdir -p /workspace \
    && chown -R studio:studio /app /workspace
USER studio

CMD ["uvicorn", "apps.api.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
