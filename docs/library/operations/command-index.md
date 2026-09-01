# Operations Command Index

This is the current entrypoint index. Retired Python backend and bare-metal
Supervisor commands are available only through repository history.

## Runtime and database

- `scripts/start_services.sh` — local Rust API, workers, scheduler, isolated extractor, and SQLx migration launcher.
- `scripts/run_sqlx_migrations.sh` — apply embedded SQLx migrations or explicitly adopt a verified legacy restore.
- `scripts/setup_local_postgres.sh` — install/start local PostgreSQL and write the local native URL.
- `scripts/dev.sh` — background local-service convenience wrapper around `start_services.sh`.
- `scripts/backup_database.sh` — create and retain PostgreSQL custom-format backups.

`start_services.sh all --local-e2e --port <PORT>` is the bounded full-stack
profile for local end-to-end work. It constrains every process's SQLx pool,
executes the extractor directly after a frozen `uv` sync so it remains a
supervised child, and shuts down the complete child set if any service exits.
Environment-specific pool settings remain available for ordinary local runs.

## Operations and deploy

- `.github/workflows/docker-racknerd-deploy.yml` — supported exact-SHA production deploy.
- `.github/workflows/e2b-template-publish.yml` — manual, quality-gated publication of the canonical E2B template from a current-main SHA.
- `scripts/build_agent_vm_template.sh` — network-free template validation/dry-run and explicit E2B publication with a source receipt.
- `scripts/deploy_blue_green.sh` — remote blue-green API and singleton worker/scheduler rollout.
- `scripts/deploy/switch-api-slot.sh` — atomic Nginx upstream switch.
- `scripts/deploy/push_envs.sh` — explicit env-only host sync outside an application deploy.
- `scripts/sync_production_state.sh` — supported production-to-local state snapshot entrypoint.
- `scripts/sync_logs_from_server.sh` and `scripts/view_remote_errors.sh` — narrow SSH log helpers.
- `scripts/start_bgutil_provider.sh` — optional pinned YouTube proof-of-origin helper for native media testing.

Use `newsly-admin` for ownership, health, task, usage, eval, and bounded
read-only database-inspection operations. Use the Rust admin HTTP surface or
container logs after narrowing a runtime issue.

## User CLI

- `cargo run --manifest-path rust/Cargo.toml -p newsly-cli --bin newsbuddy -- <command>` — run the authenticated user-facing CLI from source.
- `cargo install --locked --path rust/crates/newsly-cli` — install the `newsbuddy` binary from the current checkout.
- `cargo test --manifest-path rust/Cargo.toml -p newsly-cli` — run the CLI command, transport, config, output, polling, and library-safety tests.

`newsbuddy` remains separate from the operator-only `newsly-admin` binary. Its
external Homebrew formula is maintained in another repository and must be
updated separately when publishing the Rust build.

## Contracts and guardrails

- `scripts/export_openapi_schema.sh` — export Rust public OpenAPI.
- `scripts/regenerate_public_contracts.sh` — refresh Rust OpenAPI plus generated app Swift and Share Extension artifacts.
- `scripts/check_public_contracts.sh` — fail on public contract, agent-operation inventory, or native-client generation drift.
- `scripts/architecture_guard.sh` — check retired-authority absence, module ratchets, formatting, and contracts.
- `scripts/check_module_size_guardrails.sh` — enforce non-generated Swift module line limits without a project Python dependency.

## iOS helpers

- `scripts/codex_bootstrap.sh` — fetch locked Rust/Python-island/Node dependencies and resolve Swift packages.
- `scripts/codex_run_ios.sh` — build, install, create a Rust debug user, and launch the current Simulator app.
- `scripts/axe_simulator_smoke.sh` — create a Rust debug user and run a lightweight AXe Simulator smoke.

Both iOS launchers accept `--api-base-url http://127.0.0.1:<PORT>`; use an
explicit non-default port when another checkout or process owns port 8000.

## Script ownership and intentional non-runtime tooling

- `scripts/lib/rust_runtime.sh` is the sourced environment/origin helper for
  Rust launchers; it is not an executable entrypoint.
- `docker/entrypoint.sh` and `docker/supervisord.worker-programs.conf` are
  image-private dispatch and worker-supervision configuration. API and
  scheduler modes execute their Rust binaries directly.
- `scripts/deploy/common.sh` is sourced only by deploy tooling.
- `python/evals/scripts/*.py` are retained offline model/evaluation pipelines;
  `python/document_extractor` exposes only its package console entrypoint.
- `docs/generate_architecture.sh`, `.agents/skills/*/scripts`, and the root
  `package.json` CSS commands are documentation, agent, and asset-build tools,
  not backend runtimes.
- `docs/brand-exploration-2026-08/*.{py,js}` is frozen historical design
  tooling and is excluded from application images.

There is no repository Go entrypoint, general Python backend launcher, client
contract wrapper, or separate API/scheduler Docker wrapper. The architecture
guard rejects those retired duplicate authorities.
