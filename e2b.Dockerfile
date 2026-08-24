FROM e2bdev/code-interpreter@sha256:442ec598ec8ca4ed01b5bb24ad6e4f2e6ac80fd88f1564ff9a01e82da18e1e3b

ENV NODE_PATH=/opt/newsly-agent/node_modules
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl git jq ripgrep ca-certificates \
    && mkdir -p /opt/newsly-agent /opt/ms-playwright \
    && cd /opt/newsly-agent \
    && npm init --yes \
    && npm install --save-exact playwright@1.62.1 \
    && npx playwright install --with-deps chromium \
    && chmod -R a+rX /opt/newsly-agent /opt/ms-playwright \
    && mkdir -p /data/workspace \
    && chown root:root /data \
    && chmod 0755 /data \
    && chown user:user /data/workspace \
    && chmod 0770 /data/workspace \
    && rm -rf /var/lib/apt/lists/*

RUN ln -s /opt/newsly-agent/node_modules /node_modules \
    && mkdir -p /home/user/.cache \
    && ln -s /opt/ms-playwright /home/user/.cache/ms-playwright \
    && chown user:user /home/user/.cache

USER user
WORKDIR /data/workspace
