# RackNerd Docker Deploy

This is the supported production path for the split Docker runtime on the
RackNerd production host.

## Local

```bash
cp .env.example .env
docker compose up --build -d
docker compose logs -f api workers scheduler document-extractor
```

For the default native local runtime:

```bash
./scripts/start_services.sh all --env-file .env
./scripts/start_services.sh server --env-file .env
./scripts/start_services.sh workers --env-file .env
```

## RackNerd env file

RackNerd uses `.env.racknerd` directly. PostgreSQL runs in its own container;
the API, workers, and scheduler connect to it over the private
`newsly-internal` Docker network.

```bash
NEWSLY_DATABASE_URL=postgresql://newsly:...@postgres:5432/newsly
NEWSLY_PUBLIC_BASE_URL=https://news.willemsavenue.com
APPLE_TEAM_ID=...
APPLE_KEY_ID=...
APPLE_PRIVATE_KEY=...
APPLE_CLIENT_ID=org.willemaw.newsly
MEDIA_BASE_DIR=/data/media
LOGS_BASE_DIR=/data/logs
IMAGES_BASE_DIR=/data/images
CONTENT_BODY_LOCAL_ROOT=/data/content_bodies
PODCAST_SCRATCH_DIR=/data/scratch
PERSONAL_MARKDOWN_ROOT=/data/personal_markdown
```

Then set at minimum:

- `POSTGRES_PASSWORD`
- `JWT_SECRET_KEY`
- `ADMIN_PASSWORD`
- your provider API keys

The `NEWSLY_PUBLIC_BASE_URL` line is optional: production Compose defaults it to
the RackNerd HTTPS origin shown above unless `.env.racknerd` overrides it.

The API launcher trusts forwarded headers from loopback and Docker's private
`172.16.0.0/12` range by default. Set `NEWSLY_FORWARDED_ALLOW_IPS` only if the
production Docker network uses a different, explicitly known subnet.

## Deploy flow

GitHub Actions:

1. builds the Docker image with the service release profile, `lld`, and a
   persistent `sccache` mount
2. pushes it to GHCR and pulls it on RackNerd
3. starts or verifies the external PostgreSQL container
4. runs the exact-image embedded SQLx migrations once
5. starts the inactive API slot and waits for its direct health check
6. drains the singleton workers and scheduler so no old binary can claim work
   emitted by the new API
7. atomically switches Nginx to that slot
8. verifies the configured public HTTPS origin reaches the new slot, rolling
   Nginx back if that probe fails
9. updates the single workers and scheduler containers
10. retains the previous API slot and image as the immediate rollback target

The build job uses `ubuntu-latest` by default. Set the repository variable
`NEWSLY_BUILD_RUNNER` to the label of a persistent self-hosted Linux builder to
retain BuildKit and compilation state between releases. GitHub-hosted runners
restore the same compilation cache through the workflow cache. Docker layer
exports use the minimal cache graph so source-dependent binary layers are not
uploaded as reusable dependency cache.

The production services are defined in `docker-compose.production.yml`.
`scripts/deploy_blue_green.sh` owns the release sequence. The active slot is
recorded in `/opt/newsly/state/active-api-slot`; Nginx reads
`/etc/nginx/newsly-active-upstream.conf`.

### SQLx baseline-adoption recovery

Normal deploys leave `NEWSLY_SQLX_BASELINE_ADOPTION` unset or set to `false`.
The retained adoption path is reserved for an eligible database at frozen
Alembic head `20260829_02` that has never been adopted, such as a verified
legacy-database restoration.

For that deliberate operation, set `NEWSLY_SQLX_BASELINE_ADOPTION=true` for one
deploy only. The deploy script records the running API, worker, and scheduler
set; stops and drains those writers; and invokes the exact-image `newsly-db
baseline` command with the maintenance-barrier attestation. Adoption refuses to
write SQLx history unless the Alembic head, migration history, normalized
schema/data catalog, and role/grant policy all match the committed baseline
evidence.

After adoption succeeds, remove the flag before the next deploy. The host marker
at `/opt/newsly/state/sqlx-baseline-adopted` makes enabling the one-shot flag
again a hard error. If adoption fails before the authority migration is applied,
the deploy restores the recorded prior containers. Once Rust authority is applied,
or when its state cannot be proven, failure is fail-closed: the release does not
advance and the prior application writers remain stopped.

The host Nginx configuration and atomic slot-switch helper live at
`scripts/deploy/newsly-nginx.conf` and
`scripts/deploy/switch-api-slot.sh`. Install them as
`/etc/nginx/nginx.conf` and `/opt/newsly/bin/switch-api-slot`, respectively,
when provisioning a replacement host.

Operator commands use `newsly-admin` from the stable `newsly-workers` container
for database settings and the Rust admin surface or container runtime for logs. The default SSH target is the local
`news-app-server` alias, which should point at the current production host.

## Availability invariants

- Exactly one API slot receives new requests, but the previous healthy slot
  remains running after a deploy.
- Nginx is reloaded only after the inactive slot passes `/health`; existing
  connections drain under Nginx's graceful reload.
- PostgreSQL and `/data` outlive every app container replacement.
- Workers and the scheduler are singletons. They drain before the HTTP switch
  and are replaced after it, preventing mixed API/worker task semantics while
  HTTP remains near-hitless. A failure while draining them, before target routing
  is attempted, restores the recorded writers. Once target switching begins, the
  old writers remain stopped on every failure—even if the prior route is restored—
  because the target API may already have emitted new-version tasks.
- Migrations run before the API switch. Migrations used in this deploy path
  must remain compatible with the currently active API until Nginx switches.
