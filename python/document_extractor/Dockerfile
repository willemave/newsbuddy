FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    CRAWL4_AI_BASE_DIRECTORY=/tmp/crawl4ai \
    HOME=/tmp/home \
    PATH="/service/.venv/bin:$PATH"

WORKDIR /service

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

COPY pyproject.toml uv.lock /service/
RUN uv sync --frozen --no-dev --no-install-project \
    && /service/.venv/bin/playwright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright

COPY newsly_document_extractor /service/newsly_document_extractor
RUN uv pip install --python /service/.venv/bin/python --no-deps . \
    && cd /tmp \
    && /service/.venv/bin/python -c "import newsly_document_extractor"

ARG NEWSLY_BUILD_SHA=development
LABEL org.opencontainers.image.title="Newsly document extractor" \
    org.opencontainers.image.revision="${NEWSLY_BUILD_SHA}"

RUN useradd --create-home --uid 10002 extractor
USER extractor

ENV DOCUMENT_EXTRACTOR_BIND_HOST=0.0.0.0 \
    DOCUMENT_EXTRACTOR_PORT=8200 \
    DOCUMENT_EXTRACTOR_ENVIRONMENT=production

EXPOSE 8200

HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=5 \
  CMD curl --fail --silent --show-error http://127.0.0.1:8200/health/ready || exit 1

ENTRYPOINT ["/service/.venv/bin/newsly-document-extractor"]
