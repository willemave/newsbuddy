# RackNerd Docker Deploy

This is the supported production path for the Docker-based single-container runtime.

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

RackNerd now uses `.env.racknerd` directly with the same `/data` container paths the old bare-metal setup used:

```bash
DATABASE_URL=postgresql+psycopg://newsly:...@127.0.0.1:5432/newsly
PGDATA=/data/postgres
MEDIA_BASE_DIR=/data/media
LOGS_BASE_DIR=/data/logs
IMAGES_BASE_DIR=/data/images
CONTENT_BODY_LOCAL_ROOT=/data/content_bodies
PODCAST_SCRATCH_DIR=/data/scratch
PERSONAL_MARKDOWN_ROOT=/data/personal_markdown
NEWSLY_DATA_ROOT_HOST_PATH=/data
```

Then set at minimum:

- `POSTGRES_PASSWORD`
- `JWT_SECRET_KEY`
- `ADMIN_PASSWORD`
- your provider API keys

For migrating the existing RackNerd SQLite database in `/data/news_app.db`, keep the file in place. The container mounts host `/data` to container `/data`, so the old SQLite file is visible at the same path.

## Deploy flow

GitHub Actions:

1. builds the Docker image
2. streams it to RackNerd with `docker load`
3. stops the legacy Supervisor services and host cron entries
4. runs `docker compose --env-file .env.racknerd up -d`
5. waits for the `newsly` container health check to turn healthy

## RackNerd migration note

The old one-off SQLite-to-Postgres migration step has been removed. Current RackNerd
deployments assume the application is already running against PostgreSQL and that any
data restore has been handled with standard Postgres tooling before `docker compose up`.
