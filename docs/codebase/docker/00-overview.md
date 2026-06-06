# docker/

Source folder: `docker`

## Purpose
Container entrypoints and supervisor configuration for the staging/production-style runtime.

## Runtime behavior
- `entrypoint.sh` starts Supervisor, which coordinates Postgres, bootstrap, API, workers, queue watchdog, and scheduler processes.
- `supervisord.conf` is the full single-container runtime: Postgres, API, content workers, media workers, image, onboarding, backfill, discussion, twitter, chat, audio-episode, queue-watchdog, and scheduler programs.
- `supervisord.server.conf` is the server profile variant.
- Worker entrypoints call the Python queue processor for named task queues; scheduler and watchdog entrypoints call the corresponding scripts.
- `crontab` and `supercronic.py` support cron-style scheduled tasks where the deployment profile needs them.

## Important files
| Path | Purpose |
|---|---|
| `docker/entrypoint.sh` | Container startup wrapper. |
| `docker/supervisord.conf` | Main multi-process container graph. |
| `docker/supervisord.server.conf` | Server-specific supervisor graph. |
| `docker/run-api.sh` | Starts the FastAPI server. |
| `docker/run-bootstrap.sh` | Runs one-time startup/bootstrap tasks such as migrations. |
| `docker/run-postgres.sh` | Starts the bundled Postgres process for containerized deployments. |
| `docker/run-worker.sh` | Starts one queue worker for a queue/slot pair. |
| `docker/run-scheduler.sh` | Starts scheduled scraper/integration work. |
| `docker/run-queue-watchdog.sh` | Starts queue recovery/watchdog work. |
| `docker/wait-for-bootstrap.sh` | Coordinates services that must wait for bootstrap completion. |

## Integration points
- Local development should use normal local services and local PostgreSQL; Docker is the staging/production-style runtime.
- Queue names must stay aligned with `app/pipeline/task_specs.py` and `app/services/queue.py`.
- Operator inspection should go through `admin`, whose log tail defaults to the unified `newsly` container stream.

## Excluded local files
`docker/local-data/` is local runtime state and is intentionally not documented as source.
