"""Contract tests for the split production Docker runtime."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def _production_compose() -> dict:
    return yaml.safe_load((REPO_ROOT / "docker-compose.production.yml").read_text())


def test_production_compose_keeps_postgres_outside_app_containers() -> None:
    compose = _production_compose()
    services = compose["services"]

    assert services["postgres"]["container_name"] == "newsly-postgres"
    assert services["postgres"]["volumes"] == ["/data/postgres:/var/lib/postgresql/data:z"]
    assert services["postgres"]["ports"] == ["127.0.0.1:5432:5432"]

    for service_name in ("api_blue", "api_green", "workers", "scheduler", "migrate"):
        assert services[service_name]["environment"]["DATABASE_URL"].startswith(
            "${NEWSLY_DATABASE_URL:"
        )


def test_production_compose_defines_blue_green_api_slots() -> None:
    services = _production_compose()["services"]

    assert services["api_blue"]["container_name"] == "newsly-api-blue"
    assert services["api_blue"]["ports"] == ["127.0.0.1:8001:8000"]
    assert services["api_green"]["container_name"] == "newsly-api-green"
    assert services["api_green"]["ports"] == ["127.0.0.1:8002:8000"]
    assert services["api_blue"]["environment"]["NEWSLY_RUNTIME_MODE"] == "api"
    assert services["api_green"]["environment"]["NEWSLY_RUNTIME_MODE"] == "api"


def test_production_compose_runs_workers_scheduler_and_migrations_separately() -> None:
    services = _production_compose()["services"]

    assert services["workers"]["environment"]["NEWSLY_RUNTIME_MODE"] == "workers"
    assert services["scheduler"]["environment"]["NEWSLY_RUNTIME_MODE"] == "scheduler"
    assert services["migrate"]["environment"]["NEWSLY_RUNTIME_MODE"] == "migrate"
    assert services["workers"]["stop_grace_period"] == "10m"
    assert services["workers"]["healthcheck"] == {"disable": True}
    assert services["scheduler"]["healthcheck"] == {"disable": True}


def test_entrypoint_supports_external_database_runtime_roles() -> None:
    entrypoint = (REPO_ROOT / "docker/entrypoint.sh").read_text()

    assert "api|workers|scheduler|migrate" in entrypoint
    assert "DATABASE_URL is required" in entrypoint
    assert "NEWSLY_WAIT_FOR_BOOTSTRAP=false" in entrypoint


def test_blue_green_deploy_orders_migration_health_switch_and_workers() -> None:
    deploy_script = (REPO_ROOT / "scripts/deploy_blue_green.sh").read_text()

    migration = deploy_script.index("compose --profile ops run --rm --no-deps migrate")
    inactive_api = deploy_script.index('compose up -d --no-deps "${target_service}"')
    health = deploy_script.index('"http://127.0.0.1:${target_port}/health"')
    switch = deploy_script.index('"${switch_script}" "${target_slot}"')
    background = deploy_script.index("compose up -d --no-deps workers scheduler")

    assert migration < inactive_api < health < switch < background


def test_nginx_routes_through_atomic_active_upstream() -> None:
    nginx_config = (REPO_ROOT / "scripts/deploy/newsly-nginx.conf").read_text()
    switch_script = (REPO_ROOT / "scripts/deploy/switch-api-slot.sh").read_text()

    assert "include /etc/nginx/newsly-active-upstream.conf" in nginx_config
    assert "proxy_pass http://newsly_backend" in nginx_config
    assert "listen 443 ssl http2" in nginx_config
    assert "racknerd-3b1b61d.willemsavenue.com" in nginx_config
    assert switch_script.index('"http://127.0.0.1:${port}/health"') < (
        switch_script.index('install -m 644 "${candidate}" "${upstream_file}"')
    )
    assert "rollback_upstream" in switch_script
    assert "systemctl reload nginx" in switch_script
    assert switch_script.index("systemctl reload nginx") < switch_script.index(
        "http://127.0.0.1/health"
    )


def test_github_deploy_uses_blue_green_release_script() -> None:
    workflow = (REPO_ROOT / ".github/workflows/docker-racknerd-deploy.yml").read_text()

    assert "scripts/deploy_blue_green.sh" in workflow
    assert "install -m 755" in workflow
    assert "/opt/newsly/bin/switch-api-slot" in workflow
    assert "newsly-api-${active_slot}" in workflow
    assert "http://127.0.0.1/health" in workflow
    assert "docker compose --env-file .env.racknerd up -d --no-build" not in workflow
