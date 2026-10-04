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

Nginx is the production proxy trust boundary. The Rust API uses
`x-forwarded-proto` to infer the public scheme only when `PUBLIC_BASE_URL` is
unset.

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

The command selects online deployment, baseline adoption, or council retirement once before
migration preparation. Each mode owns its drain and rollback rules; backup creation and
verification are shared. The existing image argument, maintenance flag, and adoption environment
variable select the mode.

### Council retirement maintenance cutover

Migration `20261002010000` requires an explicit maintenance barrier on a populated database.
Normal blue-green deployment refuses this pending migration while old writers remain online.
Before scheduling the cutover, run the exact target image's read-only `newsly-db
verify-council-retirement` command. It reports processing message IDs and malformed session IDs
without transcript content. Drain council work with the old worker before proceeding; stuck or
orphaned rows require separately authorized operator investigation and repair. The migration
never silently fails accepted work. Stop council-producing API traffic while draining if needed.

For the authorized release, invoke `scripts/deploy_blue_green.sh <exact-image>
--maintenance-cutover`. This explicit mode rechecks readiness, stops both API slots, workers and
scheduler, then checks again. It creates a fresh custom-format database backup under
`/opt/newsly/state/maintenance-backups/`, verifies its archive inventory and checksum, and runs
the exact-image migration with the barrier attestation. It starts only the new runtime and
disables automatic rollback/restoration of old containers, including after ambiguous migration
failure. A failure after entering the barrier leaves old writers stopped for manual recovery.
This mode is separate from baseline adoption and cannot be combined with it.

Conversion keeps parent IDs and selected follow-up message IDs; initial/retry council rows and
alternate follow-ups remain in archived children. New database constraints reject attempts by an
old binary to recreate active council state. Retained columns do not authorize restarting an old
runtime after this cutover. Local databases synced from production also require stopped local
writers and `newsly-db migrate --maintenance-barrier-confirmed` for this pending migration.

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
when provisioning a replacement host. Rust owns `/` and `/robots.txt`; Nginx
proxies those routes and serves only the file-backed image alias. The 2026-10-03
reference cleanup must be installed and checked with `nginx -t` during a separately
authorized host update; editing this file does not update the live host.

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
