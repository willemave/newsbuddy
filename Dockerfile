# syntax=docker/dockerfile:1.7

FROM rust:1.94.1-bookworm AS newsly-rust-chef

ARG SCCACHE_VERSION=0.17.0
ARG TARGETARCH

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl lld \
    && case "${TARGETARCH}" in \
        amd64) \
          sccache_arch="x86_64"; \
          sccache_sha="67c4a96dd237c1f518f6b36083f270f9976d516f1e57fce891755ea782e50006" \
          ;; \
        arm64) \
          sccache_arch="aarch64"; \
          sccache_sha="821a86343191aa1cbab74bd42f9e93c9a63bf85e4742945f40d3ae84193c1c77" \
          ;; \
        *) echo "unsupported build architecture: ${TARGETARCH}" >&2; exit 1 ;; \
      esac \
    && sccache_archive="sccache-v${SCCACHE_VERSION}-${sccache_arch}-unknown-linux-musl.tar.gz" \
    && curl --fail --silent --show-error --location \
      "https://github.com/mozilla/sccache/releases/download/v${SCCACHE_VERSION}/${sccache_archive}" \
      --output "/tmp/${sccache_archive}" \
    && echo "${sccache_sha}  /tmp/${sccache_archive}" | sha256sum --check --strict \
    && tar --extract --gzip --file "/tmp/${sccache_archive}" --directory /tmp \
    && install --mode 0755 \
      "/tmp/sccache-v${SCCACHE_VERSION}-${sccache_arch}-unknown-linux-musl/sccache" \
      /usr/local/bin/sccache \
    && rm -rf /var/lib/apt/lists/* /tmp/sccache-v* /tmp/sccache-*.tar.gz

RUN cargo install cargo-chef --version 0.1.78 --locked

WORKDIR /workspace/rust

ENV RUSTC_WRAPPER=/usr/local/bin/sccache \
    RUSTFLAGS="-C link-arg=-fuse-ld=lld" \
    SCCACHE_DIR=/workspace/.cache/sccache \
    SCCACHE_CACHE_SIZE=10G

FROM newsly-rust-chef AS newsly-rust-planner

COPY rust/ /workspace/rust/
RUN cargo chef prepare --recipe-path recipe.json

FROM newsly-rust-chef AS newsly-rust-builder

COPY --from=newsly-rust-planner /workspace/rust/recipe.json recipe.json
COPY contracts/ /workspace/contracts/
COPY e2b.Dockerfile /workspace/e2b.Dockerfile
RUN --mount=type=cache,id=newsly-sccache,target=/workspace/.cache/sccache,sharing=locked \
    cargo chef cook --locked --profile release-service --recipe-path recipe.json

COPY rust/ /workspace/rust/

RUN --mount=type=cache,id=newsly-sccache,target=/workspace/.cache/sccache,sharing=locked \
    cargo build --locked --profile release-service \
    --package newsly-db \
    --package newsly-api \
    --package newsly-admin \
    --package newsly-worker \
    --package newsly-scheduler \
    --package newsly-account-deletion-worker --bins \
    && sccache --show-stats

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

COPY --from=newsly-rust-builder /workspace/rust/target/release-service/newsly-db /usr/local/bin/newsly-db
COPY --from=newsly-rust-builder /workspace/rust/target/release-service/newsly-api /usr/local/bin/newsly-api
COPY --from=newsly-rust-builder /workspace/rust/target/release-service/newsly-admin /usr/local/bin/newsly-admin
COPY --from=newsly-rust-builder /workspace/rust/target/release-service/newsly-scheduler /usr/local/bin/newsly-scheduler
COPY --from=newsly-rust-builder /workspace/rust/target/release-service/newsly-worker /usr/local/bin/newsly-worker
COPY --from=newsly-rust-builder /workspace/rust/target/release-service/newsly-account-deletion-worker /usr/local/bin/newsly-account-deletion-worker

RUN for worker in \
      content media audio image discussion news-item scrape summarization x-sync agent-data \
      feed-backfill feed-discovery onboarding-discovery briefing-refresh chat run-llm-task; \
      do ln -s newsly-worker "/usr/local/bin/newsly-${worker}-worker"; done \
    && chmod +x /usr/local/bin/newsly-* /app/docker/*.sh

ARG NEWSLY_BUILD_SHA
RUN test -n "${NEWSLY_BUILD_SHA}"
ENV NEWSLY_APPLICATION_SHA=${NEWSLY_BUILD_SHA}

VOLUME ["/data"]

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=10s --start-period=20s --retries=5 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

ENTRYPOINT ["/app/docker/entrypoint.sh"]
