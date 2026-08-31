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

1. builds the Docker image
2. pushes it to GHCR and pulls it on RackNerd
3. starts or verifies the external PostgreSQL container
4. runs the exact-image embedded SQLx migrations once
5. starts the inactive API slot and waits for its direct health check
6. atomically switches Nginx to that slot
7. verifies the configured public HTTPS origin reaches the new slot, rolling
   Nginx back if that probe fails
8. updates the single workers and scheduler containers
9. retains the previous API slot and image as the immediate rollback target

The production services are defined in `docker-compose.production.yml`.
`scripts/deploy_blue_green.sh` owns the release sequence. The active slot is
recorded in `/opt/newsly/state/active-api-slot`; Nginx reads
`/etc/nginx/newsly-active-upstream.conf`.

### One-time SQLx baseline adoption

An existing production database at frozen Alembic head `20260829_02` must be adopted exactly once.
Set `NEWSLY_SQLX_BASELINE_ADOPTION=true` in `.env.racknerd` for that deploy only. The deploy script
records the running API/worker/scheduler set, stops and drains those writers, and invokes the
exact-image `newsly-db baseline` command with the maintenance-barrier attestation. Adoption refuses
to write SQLx history unless the Alembic head, migration history, normalized schema/data catalog,
and role/grant policy all match the committed baseline evidence.

After the adoption deploy succeeds, remove `NEWSLY_SQLX_BASELINE_ADOPTION` (or set it to `false`)
before the next deploy. The host marker at `/opt/newsly/state/sqlx-baseline-adopted` makes leaving
the one-shot flag enabled a hard error. Once the maintenance barrier begins, an adoption failure
fails closed: the deploy does not restart a retired Python writer set or advance the release.

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
- Workers and the scheduler are singletons. They are replaced after the HTTP
  switch, so HTTP deploys are near-hitless while background processing has a
  short controlled restart.
- Migrations run before the API switch. Migrations used in this deploy path
  must remain compatible with the currently active API until Nginx switches.
