#!/usr/bin/env bash

# Shared helpers for local launchers that execute the Rust application runtime.
# This file is sourced; it is not a standalone entrypoint.

newsly_resolve_env_file() {
  local repository_root="$1"
  local explicit_path="${2:-}"

  if [[ -n "${explicit_path}" ]]; then
    printf '%s\n' "${explicit_path}"
    return
  fi
  if [[ -n "${NEWSLY_ENV_FILE:-}" ]]; then
    printf '%s\n' "${NEWSLY_ENV_FILE}"
    return
  fi

  local candidate
  for candidate in \
    "${repository_root}/.env" \
    "${repository_root}/.env.docker.local" \
    "${repository_root}/.env.docker"
  do
    if [[ -f "${candidate}" ]]; then
      printf '%s\n' "${candidate}"
      return
    fi
  done
  printf '%s\n' ""
}

newsly_load_dotenv() {
  local env_file="$1"
  if [[ -z "${env_file}" ]]; then
    return
  fi
  if [[ ! -f "${env_file}" ]]; then
    echo "environment file not found: ${env_file}" >&2
    return 1
  fi

  local line key value
  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line#export }"
    [[ -z "${line}" || "${line}" == \#* || "${line}" != *=* ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    if [[ ! "${key}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
      echo "invalid environment key in ${env_file}: ${key}" >&2
      return 1
    fi
    if [[ "${value}" == \"*\" && "${value}" == *\" ]]; then
      value="${value:1:${#value}-2}"
    elif [[ "${value}" == \'*\' && "${value}" == *\' ]]; then
      value="${value:1:${#value}-2}"
    fi
    export "${key}=${value}"
  done < "${env_file}"
  export NEWSLY_ENV_FILE="${env_file}"
}

newsly_normalize_database_environment() {
  if [[ -n "${NEWSLY_DATABASE_URL:-}" && -z "${DATABASE_URL:-}" ]]; then
    export DATABASE_URL="${NEWSLY_DATABASE_URL}"
  fi
  if [[ "${DATABASE_URL:-}" == postgresql+psycopg://* ]]; then
    export DATABASE_URL="postgresql://${DATABASE_URL#postgresql+psycopg://}"
  elif [[ "${DATABASE_URL:-}" == postgresql+asyncpg://* ]]; then
    export DATABASE_URL="postgresql://${DATABASE_URL#postgresql+asyncpg://}"
  fi
}

newsly_require_database_url() {
  if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "DATABASE_URL is required; export it or provide --env-file" >&2
    return 1
  fi
}

newsly_parse_api_base_url() {
  local base_url="${1%/}"
  local scheme authority host port use_https

  case "${base_url}" in
    http://*)
      scheme="http"
      authority="${base_url#http://}"
      port="80"
      use_https="false"
      ;;
    https://*)
      scheme="https"
      authority="${base_url#https://}"
      port="443"
      use_https="true"
      ;;
    *)
      echo "API base URL must start with http:// or https://: ${base_url}" >&2
      return 1
      ;;
  esac

  if [[ -z "${authority}" || "${authority}" == */* || "${authority}" == *\?* || "${authority}" == *\#* || "${authority}" == *@* ]]; then
    echo "API base URL must be an origin without credentials, path, query, or fragment: ${base_url}" >&2
    return 1
  fi
  if [[ "${authority}" == *:* ]]; then
    host="${authority%:*}"
    port="${authority##*:}"
  else
    host="${authority}"
  fi
  if [[ -z "${host}" || ! "${port}" =~ ^[1-9][0-9]*$ || "${port}" -gt 65535 ]]; then
    echo "API base URL has an invalid host or port: ${base_url}" >&2
    return 1
  fi

  printf '%s\t%s\t%s\t%s\n' "${scheme}" "${host}" "${port}" "${use_https}"
}
