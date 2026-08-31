#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

for retired_path in app admin migrations tests pyproject.toml uv.lock supervisor.conf crontab; do
  if [[ -e "$retired_path" ]]; then
    echo "retired backend authority was reintroduced: $retired_path" >&2
    exit 1
  fi
done

for duplicate_entrypoint in \
  client/newsly/scripts/regenerate_api_contracts.sh \
  docker/run-api.sh \
  docker/run-scheduler.sh \
  scripts/update-docs-from-commit.sh
do
  if [[ -e "$duplicate_entrypoint" ]]; then
    echo "retired duplicate entrypoint was reintroduced: $duplicate_entrypoint" >&2
    exit 1
  fi
done

if rg -n \
  --glob '*.sh' \
  --glob '!architecture_guard.sh' \
  '(uvicorn|python(3)?[[:space:]]+-m[[:space:]]+app|uv[[:space:]]+run[[:space:]]+-m[[:space:]]+admin|alembic[[:space:]]+(upgrade|downgrade|revision)|(^|[[:space:]])go[[:space:]]+(run|build|test))' \
  scripts docker
then
  echo "retired Python or Go backend launcher exists in active runtime tooling" >&2
  exit 1
fi

for required_python_island in python/document_extractor python/evals; do
  if [[ ! -d "$required_python_island" ]]; then
    echo "required Python island is missing: $required_python_island" >&2
    exit 1
  fi
done

for python_child in python/*; do
  case "$python_child" in
    python/document_extractor | python/evals) ;;
    *)
      echo "unapproved Python island was introduced: $python_child" >&2
      exit 1
      ;;
  esac
done

invalid_python_path=false
while IFS= read -r python_path; do
  [[ -f "$python_path" ]] || continue
  case "$python_path" in
    python/document_extractor/* | python/evals/*) ;;
    docs/brand-exploration-2026-08/*)
      # Historical, offline design-asset generators. This exact docs-only
      # directory is not packaged, imported, or present in production images.
      ;;
    *)
      echo "Newsly-owned Python exists outside an approved island: $python_path" >&2
      invalid_python_path=true
      ;;
  esac
done < <(git ls-files --cached --others --exclude-standard -- '*.py' '*.pyi')

while IFS= read -r python_path; do
  [[ -f "$python_path" ]] || continue
  case "$python_path" in
    *.py | *.pyi)
      # The source-extension inventory above already checked this path.
      continue
      ;;
    python/document_extractor/* | python/evals/*) ;;
    docs/brand-exploration-2026-08/*) ;;
    *)
      echo "Python shebang exists outside an approved island: $python_path" >&2
      invalid_python_path=true
      ;;
  esac
done < <(git grep -Il --untracked -e '^#!.*python' -- . 2>/dev/null || true)

if [[ "$invalid_python_path" == true ]]; then
  exit 1
fi

scripts/check_module_size_guardrails.sh
scripts/check_ios_wire_boundaries.sh
scripts/build_agent_vm_template.sh --check >/dev/null
cargo fmt --manifest-path rust/Cargo.toml --all -- --check
scripts/check_public_contracts.sh
