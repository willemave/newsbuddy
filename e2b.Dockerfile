FROM e2bdev/code-interpreter@sha256:442ec598ec8ca4ed01b5bb24ad6e4f2e6ac80fd88f1564ff9a01e82da18e1e3b

ENV NODE_PATH=/opt/newsly-agent/node_modules
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

COPY rust/Cargo.toml rust/Cargo.lock /opt/newsly-vm-bootstrap-build/
COPY rust/crates /opt/newsly-vm-bootstrap-build/crates

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential ca-certificates curl git jq ripgrep xz-utils \
    && curl --fail --location --silent --show-error \
        https://static.rust-lang.org/dist/2026-03-26/rust-1.94.1-x86_64-unknown-linux-gnu.tar.xz \
        --output /tmp/rust-toolchain.tar.xz \
    && echo "294b3d81fa72e62581276290c60c81eb8b58498d333d422ca1dfc432877d0c40  /tmp/rust-toolchain.tar.xz" \
        | sha256sum --check --strict \
    && tar --extract --xz --file /tmp/rust-toolchain.tar.xz --directory /tmp \
        rust-1.94.1-x86_64-unknown-linux-gnu/install.sh \
        rust-1.94.1-x86_64-unknown-linux-gnu/components \
        rust-1.94.1-x86_64-unknown-linux-gnu/rustc \
        rust-1.94.1-x86_64-unknown-linux-gnu/cargo \
        rust-1.94.1-x86_64-unknown-linux-gnu/rust-std-x86_64-unknown-linux-gnu \
    && /tmp/rust-1.94.1-x86_64-unknown-linux-gnu/install.sh \
        --prefix=/opt/newsly-rust \
        --components=rustc,cargo,rust-std-x86_64-unknown-linux-gnu \
    && cd /opt/newsly-vm-bootstrap-build \
    && PATH=/opt/newsly-rust/bin:$PATH cargo build --locked --release --package newsly-vm-bootstrap \
    && install -o root -g root -m 0755 \
        target/release/newsly-vm-bootstrap /usr/local/bin/newsly-vm-bootstrap \
    && rm -rf \
        /opt/newsly-rust \
        /root/.cargo \
        /opt/newsly-vm-bootstrap-build \
        /tmp/rust-1.94.1-x86_64-unknown-linux-gnu \
        /tmp/rust-toolchain.tar.xz \
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
    && newsly-vm-bootstrap --help >/dev/null \
    && rm -rf /var/lib/apt/lists/*

RUN ln -s /opt/newsly-agent/node_modules /node_modules \
    && mkdir -p /home/user/.cache \
    && ln -s /opt/ms-playwright /home/user/.cache/ms-playwright \
    && chown user:user /home/user/.cache

USER user
WORKDIR /data/workspace
