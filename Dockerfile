# syntax=docker/dockerfile:1.7

FROM rust:1.94.1-bookworm AS newsly-rust-chef

RUN cargo install cargo-chef --version 0.1.78 --locked

WORKDIR /workspace/rust

FROM newsly-rust-chef AS newsly-rust-planner

COPY rust/ /workspace/rust/
RUN cargo chef prepare --recipe-path recipe.json

FROM newsly-rust-chef AS newsly-rust-builder

COPY --from=newsly-rust-planner /workspace/rust/recipe.json recipe.json
COPY contracts/ /workspace/contracts/
COPY e2b.Dockerfile /workspace/e2b.Dockerfile
RUN cargo chef cook --locked --release --recipe-path recipe.json

COPY rust/ /workspace/rust/

ARG NEWSLY_BUILD_SHA
ENV NEWSLY_BUILD_SHA=${NEWSLY_BUILD_SHA}

RUN test -n "${NEWSLY_BUILD_SHA}" \
    && cargo build --locked --release --jobs 1 \
    --package newsly-db \
    --package newsly-api \
    --package newsly-admin \
    --package newsly-worker \
    --package newsly-scheduler \
    --package newsly-account-deletion-worker \
    --bins

FROM debian:bookworm-slim

ARG YT_DLP_GIT_SHA=fdcc954df4955267ec1627cbeb347b661a110e7c
ARG BGUTIL_YTDLP_POT_PROVIDER_VERSION=1.3.1

ENV APP_HOME=/app \
    ADMIN_STATIC_DIR=/app/static \
    NEWSLY_DATA_ROOT=/data \
    PORT=8000 \
    NEWSLY_RUST_LOG_FORMAT=json

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        ffmpeg \
        git \
        python3-venv \
        supervisor \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/newsly-yt-dlp \
    && /opt/newsly-yt-dlp/bin/python -m pip install --no-cache-dir \
        "yt-dlp @ git+https://github.com/yt-dlp/yt-dlp.git@${YT_DLP_GIT_SHA}" \
        "bgutil-ytdlp-pot-provider==${BGUTIL_YTDLP_POT_PROVIDER_VERSION}" \
    && ln -s /opt/newsly-yt-dlp/bin/yt-dlp /usr/local/bin/yt-dlp

COPY docker/ /app/docker/
COPY contracts/ /app/contracts/
COPY rust/assets/admin-static/ /app/static/

COPY --from=newsly-rust-builder /workspace/rust/target/release/newsly-db /usr/local/bin/newsly-db
COPY --from=newsly-rust-builder /workspace/rust/target/release/newsly-api /usr/local/bin/newsly-api
COPY --from=newsly-rust-builder /workspace/rust/target/release/newsly-admin /usr/local/bin/newsly-admin
COPY --from=newsly-rust-builder /workspace/rust/target/release/newsly-scheduler /usr/local/bin/newsly-scheduler
COPY --from=newsly-rust-builder /workspace/rust/target/release/newsly-worker /usr/local/bin/newsly-content-worker
COPY --from=newsly-rust-builder /workspace/rust/target/release/audio_episode_worker /usr/local/bin/newsly-audio-worker
COPY --from=newsly-rust-builder /workspace/rust/target/release/discussion_worker /usr/local/bin/newsly-discussion-worker
COPY --from=newsly-rust-builder /workspace/rust/target/release/image_worker /usr/local/bin/newsly-image-worker
COPY --from=newsly-rust-builder /workspace/rust/target/release/feed_backfill_worker /usr/local/bin/newsly-feed-backfill-worker
COPY --from=newsly-rust-builder /workspace/rust/target/release/feed_discovery_worker /usr/local/bin/newsly-feed-discovery-worker
COPY --from=newsly-rust-builder /workspace/rust/target/release/media_worker /usr/local/bin/newsly-media-worker
COPY --from=newsly-rust-builder /workspace/rust/target/release/news_item_worker /usr/local/bin/newsly-news-item-worker
COPY --from=newsly-rust-builder /workspace/rust/target/release/scrape_worker /usr/local/bin/newsly-scrape-worker
COPY --from=newsly-rust-builder /workspace/rust/target/release/onboarding_discovery_worker /usr/local/bin/newsly-onboarding-discovery-worker
COPY --from=newsly-rust-builder /workspace/rust/target/release/summarization_worker /usr/local/bin/newsly-summarization-worker
COPY --from=newsly-rust-builder /workspace/rust/target/release/x_sync_worker /usr/local/bin/newsly-x-sync-worker
COPY --from=newsly-rust-builder /workspace/rust/target/release/briefing_refresh_worker /usr/local/bin/newsly-briefing-refresh-worker
COPY --from=newsly-rust-builder /workspace/rust/target/release/chat_worker /usr/local/bin/newsly-chat-worker
COPY --from=newsly-rust-builder /workspace/rust/target/release/run_llm_task_worker /usr/local/bin/newsly-run-llm-task-worker
COPY --from=newsly-rust-builder /workspace/rust/target/release/newsly-account-deletion-worker /usr/local/bin/newsly-account-deletion-worker

RUN chmod +x /usr/local/bin/newsly-* /app/docker/*.sh

VOLUME ["/data"]

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=10s --start-period=20s --retries=5 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

ENTRYPOINT ["/app/docker/entrypoint.sh"]
