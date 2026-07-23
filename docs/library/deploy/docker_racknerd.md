# RackNerd Docker Deploy

This is the supported production path for the split Docker runtime on the
RackNerd production host.

## Local

```bash
cp .env.docker.example .env.docker.local
docker compose --env-file .env.docker.local up --build -d
docker compose logs -f newsly
```

For non-Docker local runs that still use the Docker-style env file:

```bash
./scripts/start_services.sh all --env-file .env.docker.local
./scripts/start_services.sh server --env-file .env.docker.local
./scripts/start_services.sh workers --env-file .env.docker.local
```

## RackNerd env file

RackNerd uses `.env.racknerd` directly. PostgreSQL runs in its own container;
the API, workers, and scheduler connect to it over the private
`newsly-internal` Docker network.

```bash
NEWSLY_DATABASE_URL=postgresql+psycopg://newsly:...@postgres:5432/newsly
CORS_ALLOW_ORIGINS=https://racknerd-3b1b61d.willemsavenue.com
NEWSLY_PUBLIC_BASE_URL=https://racknerd-3b1b61d.willemsavenue.com
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
- `CORS_ALLOW_ORIGINS` with the public production origin, because wildcard CORS is rejected when `ENVIRONMENT=production`
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
4. runs Alembic migrations once
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

The host Nginx configuration and atomic slot-switch helper live at
`scripts/deploy/newsly-nginx.conf` and
`scripts/deploy/switch-api-slot.sh`. Install them as
`/etc/nginx/nginx.conf` and `/opt/newsly/bin/switch-api-slot`, respectively,
when provisioning a replacement host.

Operator commands use the stable `newsly-workers` container for app code,
database settings, and shared file logs. The default SSH target is the local
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
