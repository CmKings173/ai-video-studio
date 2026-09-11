FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

COPY apps ./apps
COPY workers ./workers
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
COPY workflows ./workflows
COPY infra ./infra
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 studio \
    && mkdir -p /workspace \
    && chown -R studio:studio /app /workspace
USER studio

CMD ["uvicorn", "apps.api.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
