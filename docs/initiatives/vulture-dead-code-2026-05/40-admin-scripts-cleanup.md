# Vulture Admin and Script Cleanup

**Date:** 2026-05-12  
**Scope:** Goal 6 admin and script cleanup.

## What Changed

- Removed the unused admin SQLite database sync stub from `admin/cli.py`.
- Removed the unused remote schema-model loader from `admin/remote_ops.py`.
- Removed stale benchmark/test-data fields from:
  - `scripts/benchmark_fluxdev_prompt_study.py`
  - `scripts/benchmark_infographic_model_options.py`
  - `scripts/generate_test_data.py`
- Fixed direct execution for `scripts/benchmark_infographic_model_options.py --help`.
- Whitelisted `sqlite3.Connection.row_factory` as a legitimate runtime attribute assignment in `vulture_whitelist.py`.

## Verification

```bash
uv run vulture
```

Result: pass, exit code `0`.

```bash
uv run ruff check admin/cli.py admin/remote_ops.py scripts/benchmark_fluxdev_prompt_study.py scripts/benchmark_infographic_model_options.py scripts/export_title_clustering_dataset.py scripts/generate_test_data.py tests/admin/test_remote_ops_usage.py vulture_whitelist.py
```

Result: pass.

```bash
uv run pytest tests/admin -v
```

Result: 52 passed.

Script smoke checks:

```bash
uv run python scripts/benchmark_fluxdev_prompt_study.py --help
uv run python scripts/benchmark_infographic_model_options.py --help
uv run python scripts/export_title_clustering_dataset.py --help
uv run python scripts/generate_test_data.py --help
```

Result: all pass.

## Goal 7 Input

The configured Vulture scan is now clean. The next pass can focus on enforcement policy and a durable repo command for advisory or CI use.
