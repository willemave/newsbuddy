FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_HOME=/app \
    NEWSLY_DATA_ROOT=/data \
    PGDATA=/data/postgres \
    POSTGRES_PORT=5432 \
    PORT=8000 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        ffmpeg \
        git \
        libpq-dev \
        postgresql \
        postgresql-client \
        sqlite3 \
        supervisor \
        util-linux \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip uv

COPY pyproject.toml uv.lock README.md /app/

RUN uv sync --frozen --no-dev --no-editable --no-install-project

COPY . /app

RUN uv sync --frozen --no-dev --no-editable \
    && python -m playwright install --with-deps chromium \
    && chmod +x /app/docker/*.sh /app/scripts/*.sh

VOLUME ["/data"]

EXPOSE 8000 5432

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=5 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

ENTRYPOINT ["/app/docker/entrypoint.sh"]
