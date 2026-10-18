#!/usr/bin/env bash
# Read-only probe shared by the checked-out workflow and the remote deploy script.
# Pass the versions from maintenance-migrations as individual arguments. The workflow
# sends this script over SSH stdin so it never depends on an older remote checkout.
set -euo pipefail

unknown() {
  printf 'unknown\n'
  exit 0
}

[[ $# -gt 0 ]] || unknown
versions=""
for version in "$@"; do
  [[ "${version}" =~ ^[0-9]{14}$ ]] || unknown
  versions+="${versions:+,}(${version}::bigint)"
done

postgres_scalar() {
  docker exec newsly-postgres sh -c \
    'PGOPTIONS="-c default_transaction_read_only=on" psql --no-psqlrc --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --tuples-only --no-align --command "$1"' \
    sh "$1" 2>/dev/null
}

table_state="$(postgres_scalar "SELECT to_regclass('public._sqlx_migrations') IS NOT NULL;")" || unknown
case "${table_state}" in
  f) printf 'fresh\n'; exit 0 ;;
  t) ;;
  *) unknown ;;
esac

state="$(postgres_scalar "
WITH required(version) AS (VALUES ${versions}),
pending AS (
    SELECT version FROM required
    WHERE NOT EXISTS (
        SELECT 1 FROM public._sqlx_migrations applied
        WHERE applied.version = required.version AND applied.success
    )
)
SELECT CASE
    WHEN EXISTS (SELECT 1 FROM public._sqlx_migrations WHERE NOT success) THEN 'unknown'
    WHEN NOT EXISTS (SELECT 1 FROM public._sqlx_migrations) THEN 'fresh'
    WHEN EXISTS (SELECT 1 FROM pending)
        THEN 'pending ' || (SELECT string_agg(version::text, ',' ORDER BY version) FROM pending)
    ELSE 'none'
END;")" || unknown

case "${state}" in
  fresh|none|unknown) printf '%s\n' "${state}" ;;
  *)
    [[ "${state}" =~ ^pending\ [0-9]{14}(,[0-9]{14})*$ ]] || unknown
    # Treat malformed/unexpected output as unknown, never as permission to migrate.
    IFS=',' read -r -a pending_versions <<< "${state#pending }"
    for pending in "${pending_versions[@]}"; do
      found=false
      for version in "$@"; do
        if [[ "${version}" == "${pending}" ]]; then found=true; break; fi
      done
      [[ "${found}" == true ]] || unknown
    done
    printf '%s\n' "${state}"
    ;;
esac
