#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/common.sh"

# SSH connection multiplexing for persistent connection
SSH_CONTROL_PATH="/tmp/ssh-news-deploy-$$"

establish_ssh_connection() {
  echo "→ Establishing persistent SSH connection to $REMOTE_HOST"
  ssh -M -o ControlPath="$SSH_CONTROL_PATH" -o ControlPersist=10m -fN "$REMOTE_HOST"

  # Start sudo keepalive loop on remote host to prevent timeout
  ssh -S "$SSH_CONTROL_PATH" -tt "$REMOTE_HOST" "sudo -v && nohup bash -c 'while true; do sleep 50; sudo -nv; done' >/dev/null 2>&1 &"
  echo "→ SSH connection established and sudo authenticated (keepalive active)"
}

# Sync the entire app repo to a remote host and set ownership to newsapp.
# Safe by default: excludes caches/venvs/git/node_modules/dbs.
#
# Defaults:
#   Host: willem@192.3.250.10
#   Remote app dir: /opt/news_app
#   Owner: newsapp:newsapp
#   Remote staging: /tmp/news_app_sync
#
# Options:
#   -h, --host USER@HOST            SSH target (default: willem@192.3.250.10)
#   -d, --dir  /remote/path         Remote app dir (default: /opt/news_app)
#   -o, --owner user:group          chown target (default: newsapp:newsapp)
#       --staging /tmp/path         Remote staging dir (default: /tmp/news_app_sync)
#       --no-delete                 Do not delete remote files removed locally
#       --install                   Create/activate venv via uv and install deps
#       --python-version 3.13       Python version for uv venv (default: 3.13)
#       --force-env                 Force deletion/recreation of the remote .venv
#       --debug                     Verbose output; enable local and remote tracing
#       --restart-supervisor        Reread/update and restart programs
#       --programs "a b c"          Supervisor program names (default: news_app_server news_app_workers_content news_app_workers_image news_app_workers_transcribe news_app_workers_onboarding news_app_workers_twitter news_app_workers_chat news_app_queue_watchdog news_app_bgutil_provider)
#       --promote-user USER         Run remote promote step as this user (default: root)
#       --extra-exclude PATTERN     Additional rsync exclude (can repeat)
#       --source-env FILE           Source env file for --env-only (default: .env.racknerd)
#       --no-crontab                Skip installing remote crontab from REMOTE_DIR/crontab
#       --dry-run                   Show what would be done by rsync
#       --env-only                  Only sync .env.racknerd (skip full app sync)
#
# Example:
#   scripts/deploy/push_app.sh --install --restart-supervisor
#   scripts/deploy/push_app.sh --env-only  # Quick env update

REMOTE_HOST="willem@192.3.250.10"
REMOTE_DIR="/opt/news_app"
OWNER_GROUP="newsapp:newsapp"
REMOTE_STAGING="/tmp/news_app_sync"
RSYNC_DELETE=true
DO_INSTALL=false
PY_VER="3.13"
DEBUG=false
RESTART_SUP=false
PROGRAMS=(
  news_app_server
  news_app_workers_content
  news_app_workers_image
  news_app_workers_transcribe
  news_app_workers_onboarding
  news_app_workers_twitter
  news_app_workers_chat
  news_app_queue_watchdog
  news_app_bgutil_provider
)
REQUIRED_PROGRAMS=(
  news_app_server
  news_app_workers_content
  news_app_workers_transcribe
  news_app_workers_onboarding
  news_app_workers_twitter
  news_app_workers_chat
)
DRY_RUN=false
PROMOTE_USER="root"
ENV_REFRESHED=false
FORCE_ENV=false
REMOVE_REMOTE_VENV_REASON=""
ENV_ONLY=false
SOURCE_ENV_FILE=".env.racknerd"
INSTALL_CRONTAB=true

EXCLUDES=(
  ".git/"
  ".venv/"
  "env/"
  "node_modules/"
  "__pycache__/"
  ".ruff_cache/"
  ".pytest_cache/"
  ".benchmarks/"
  "*.pyc"
  "*.pyo"
  "*.db"
  "*.sqlite"
  ".DS_Store"
  "logs/"
  "ai-memory/"
  "data/"
  "archive/"
  "client/"   # iOS client not needed on server
  ".env"      # deploy uses .env.racknerd instead of local dev env
)

validate_supervisor_state() {
  local -a expected_programs=("$@")
  if [[ ${#expected_programs[@]} -eq 0 ]]; then
    echo "No expected supervisor programs configured for validation" >&2
    exit 1
  fi

  local expected_csv required_csv
  expected_csv="$(IFS=,; echo "${expected_programs[*]}")"
  required_csv="$(IFS=,; echo "${REQUIRED_PROGRAMS[*]}")"

  local remote_cmd
  printf -v remote_cmd 'EXPECTED_CSV=%q REQUIRED_CSV=%q bash -s' "$expected_csv" "$required_csv"
  ssh -S "$SSH_CONTROL_PATH" -tt "$REMOTE_HOST" "$remote_cmd" <<'REMOTE'
set -euo pipefail

IFS=',' read -r -a expected <<< "${EXPECTED_CSV:-}"
IFS=',' read -r -a required <<< "${REQUIRED_CSV:-}"

if [[ ${#required[@]} -eq 0 ]]; then
  echo "No required supervisor programs configured" >&2
  exit 1
fi
if [[ ${#expected[@]} -eq 0 ]]; then
  echo "No expected supervisor programs configured" >&2
  exit 1
fi

status_output=""
attempts=60
for ((i=1; i<=attempts; i++)); do
  status_output="$(sudo supervisorctl status)"
  all_required_running=true

  for required_program in "${required[@]}"; do
    matches="$(printf '%s\n' "$status_output" | awk -v p="$required_program" '$1 == p || index($1, p ":") == 1' || true)"
    if [[ -z "$matches" ]]; then
      all_required_running=false
      break
    fi
    if ! printf '%s\n' "$matches" | awk '$2 == "RUNNING" {found=1} END {exit found ? 0 : 1}'; then
      all_required_running=false
      break
    fi
  done

  if [[ "$all_required_running" == "true" ]]; then
    break
  fi

  if [[ "$i" -eq "$attempts" ]]; then
    echo "Required supervisor programs did not all reach RUNNING within ${attempts}s" >&2
    printf '%s\n' "$status_output"
    exit 1
  fi
  sleep 1
done

missing_expected=()
for expected_program in "${expected[@]}"; do
  if ! printf '%s\n' "$status_output" | awk -v p="$expected_program" '$1 == p || index($1, p ":") == 1 {found=1} END {exit found ? 0 : 1}'; then
    missing_expected+=("$expected_program")
  fi
done

if [[ ${#missing_expected[@]} -gt 0 ]]; then
  echo "Missing expected supervisor programs: ${missing_expected[*]}" >&2
  printf '%s\n' "$status_output"
  exit 1
fi

for required_program in "${required[@]}"; do
  matches="$(printf '%s\n' "$status_output" | awk -v p="$required_program" '$1 == p || index($1, p ":") == 1' || true)"
  if ! printf '%s\n' "$matches" | awk '$2 == "RUNNING" {found=1} END {exit found ? 0 : 1}'; then
    echo "Required supervisor program is not RUNNING: $required_program" >&2
    printf '%s\n' "$matches"
    exit 1
  fi
done

echo "Supervisor validation passed"
printf '%s\n' "$status_output"
REMOTE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--host)
      require_option_value "$1" "${2:-}"
      REMOTE_HOST="$2"
      shift 2
      ;;
    -d|--dir)
      require_option_value "$1" "${2:-}"
      REMOTE_DIR="$2"
      shift 2
      ;;
    -o|--owner)
      require_option_value "$1" "${2:-}"
      OWNER_GROUP="$2"
      shift 2
      ;;
    --staging)
      require_option_value "$1" "${2:-}"
      REMOTE_STAGING="$2"
      shift 2
      ;;
    --no-delete) RSYNC_DELETE=false; shift ;;
    --install) DO_INSTALL=true; shift ;;
    --python-version)
      require_option_value "$1" "${2:-}"
      PY_VER="$2"
      shift 2
      ;;
    --force-env) FORCE_ENV=true; shift ;;
    --debug) DEBUG=true; shift ;;
    --restart-supervisor) RESTART_SUP=true; shift ;;
    --programs)
      require_option_value "$1" "${2:-}"
      IFS=' ' read -r -a PROGRAMS <<< "$2"
      shift 2
      ;;
    --promote-user)
      require_option_value "$1" "${2:-}"
      PROMOTE_USER="$2"
      shift 2
      ;;
    --extra-exclude)
      require_option_value "$1" "${2:-}"
      EXCLUDES+=("$2")
      shift 2
      ;;
    --source-env)
      require_option_value "$1" "${2:-}"
      SOURCE_ENV_FILE="$2"
      shift 2
      ;;
    --no-crontab) INSTALL_CRONTAB=false; shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    --env-only) ENV_ONLY=true; shift ;;
    -\?|--help|-h)
      sed -n '1,80p' "$0" | sed -n '1,50p' | sed 's/^# \{0,1\}//' ; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

# Set trap to clean up SSH connection and sudo keepalive on exit
cleanup() {
  # Kill remote sudo keepalive loop
  ssh -S "$SSH_CONTROL_PATH" "$REMOTE_HOST" "pkill -f 'while true; do sleep 50; sudo -nv; done'" 2>/dev/null || true
  # Close SSH connection
  ssh -O exit -o ControlPath="$SSH_CONTROL_PATH" "$REMOTE_HOST" 2>/dev/null || true
  rm -f "$SSH_CONTROL_PATH"
}
trap cleanup EXIT

# Resolve repo root (this script lives in scripts/deploy/)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_ROOT="$( cd "$SCRIPT_DIR/../.." && pwd )"
cd "$REPO_ROOT"

LOCAL_UV_LOCK_HASH=""
if [[ -f "uv.lock" ]]; then
  LOCAL_UV_LOCK_HASH="$(sha256sum uv.lock | awk '{print $1}')"
fi

if "$DEBUG"; then
  set -x
  echo "DEBUG: REMOTE_HOST=$REMOTE_HOST REMOTE_DIR=$REMOTE_DIR OWNER_GROUP=$OWNER_GROUP REMOTE_STAGING=$REMOTE_STAGING"
  echo "DEBUG: RSYNC_DELETE=$RSYNC_DELETE DO_INSTALL=$DO_INSTALL PY_VER=$PY_VER RESTART_SUP=$RESTART_SUP"
fi

split_owner_group "$OWNER_GROUP"
SERVICE_USER="$DEPLOY_SERVICE_USER"
SERVICE_GROUP="$DEPLOY_SERVICE_GROUP"

# Establish persistent SSH connection and authenticate sudo once
establish_ssh_connection

# Handle --env-only: just sync .env.racknerd and copy to .env
if "$ENV_ONLY"; then
  echo "→ Syncing only $SOURCE_ENV_FILE to $REMOTE_HOST:$REMOTE_DIR"
  deploy_sync_env_file \
    "$REMOTE_HOST" \
    "$REMOTE_DIR" \
    "$OWNER_GROUP" \
    "$SOURCE_ENV_FILE" \
    "$REMOTE_STAGING" \
    "22" \
    "$SSH_CONTROL_PATH"

  if "$RESTART_SUP"; then
    echo "→ Restarting supervisor programs"
    ssh -S "$SSH_CONTROL_PATH" -tt "$REMOTE_HOST" "sudo supervisorctl restart all"
    echo "→ Validating required supervisor programs"
    validate_supervisor_state "${PROGRAMS[@]}"
  fi

  echo "✅ Env sync completed to $REMOTE_HOST:$REMOTE_DIR"
  exit 0
fi

# Check remote uv.lock hash to determine if venv needs refresh
REMOTE_UV_LOCK_HASH=""
REMOTE_HASH_CMD_SUDO=$(printf "sudo -n sha256sum %q" "$REMOTE_DIR/uv.lock")
REMOTE_HASH_CMD=$(printf "sha256sum %q" "$REMOTE_DIR/uv.lock")
if REMOTE_HASH_OUTPUT=$(ssh -S "$SSH_CONTROL_PATH" "$REMOTE_HOST" "$REMOTE_HASH_CMD_SUDO" 2>/dev/null); then
  REMOTE_UV_LOCK_HASH="$(printf '%s' "$REMOTE_HASH_OUTPUT" | awk '{print $1}' | tr -d '\r')"
elif REMOTE_HASH_OUTPUT=$(ssh -S "$SSH_CONTROL_PATH" "$REMOTE_HOST" "$REMOTE_HASH_CMD" 2>/dev/null); then
  REMOTE_UV_LOCK_HASH="$(printf '%s' "$REMOTE_HASH_OUTPUT" | awk '{print $1}' | tr -d '\r')"
else
  REMOTE_UV_LOCK_HASH=""
fi

SHOULD_REMOVE_REMOTE_VENV=false
if [[ -n "$LOCAL_UV_LOCK_HASH" || -n "$REMOTE_UV_LOCK_HASH" ]]; then
  if [[ "$LOCAL_UV_LOCK_HASH" != "$REMOTE_UV_LOCK_HASH" ]]; then
    SHOULD_REMOVE_REMOTE_VENV=true
    REMOVE_REMOTE_VENV_REASON="uv.lock changed"
  fi
fi

if "$FORCE_ENV"; then
  if [[ -n "$REMOVE_REMOTE_VENV_REASON" ]]; then
    REMOVE_REMOTE_VENV_REASON="forced by --force-env; $REMOVE_REMOTE_VENV_REASON"
  else
    REMOVE_REMOTE_VENV_REASON="forced by --force-env"
  fi
  SHOULD_REMOVE_REMOTE_VENV=true
fi

echo "→ Preparing remote directories on $REMOTE_HOST"
# Staging can be created without sudo; app dir typically needs sudo
ssh -S "$SSH_CONTROL_PATH" "$REMOTE_HOST" "mkdir -p '$REMOTE_STAGING' && chmod 755 '$REMOTE_STAGING'" || true
ssh -S "$SSH_CONTROL_PATH" -tt "$REMOTE_HOST" "sudo mkdir -p '$REMOTE_DIR' && sudo chown '$OWNER_GROUP' '$REMOTE_DIR' && sudo chmod 755 '$REMOTE_DIR' && echo 'Remote app dir prepared: $REMOTE_DIR'"

echo "→ Rsync to staging: $REMOTE_HOST:$REMOTE_STAGING (delete=$RSYNC_DELETE)"

RSYNC_ARGS=(-az)
"$DRY_RUN" && RSYNC_ARGS+=(-n -v)
"$RSYNC_DELETE" && RSYNC_ARGS+=(--delete)

for pat in "${EXCLUDES[@]}"; do
  RSYNC_ARGS+=(--exclude "$pat")
done

# Trailing slash sends repo contents (not the top-level dir)
rsync "${RSYNC_ARGS[@]}" -e "ssh -S $SSH_CONTROL_PATH" ./ "$REMOTE_HOST:$REMOTE_STAGING/"

echo "→ Promoting staging to app dir with proper ownership (rsync --chown)"
REMOTE_PROMOTE_SCRIPT="/tmp/news_app_promote.sh"
REMOTE_PROMOTE_USER="$PROMOTE_USER"

ssh -S "$SSH_CONTROL_PATH" "$REMOTE_HOST" "cat > '$REMOTE_PROMOTE_SCRIPT'" <<'REMOTE_SCRIPT'
#!/usr/bin/env bash
set -euo pipefail

REMOTE_STAGING="$1"
REMOTE_DIR="$2"
OWNER_GROUP="$3"
REMOTE_DELETE="${4:-true}"
REMOTE_DEBUG="${5:-false}"

if [[ "$REMOTE_DEBUG" == "true" ]]; then
  set -x
fi

echo "[remote] whoami: $(whoami)"
RSYNC_VERSION_LINE="$(rsync --version 2>/dev/null | head -n1 || true)"
if [[ -n "$RSYNC_VERSION_LINE" ]]; then
  echo "[remote] rsync: $RSYNC_VERSION_LINE"
else
  echo "[remote] rsync version check failed"
fi

if [[ ! -d "$REMOTE_STAGING" ]]; then
  echo "[remote] staging dir $REMOTE_STAGING missing; refusing to sync to avoid clearing $REMOTE_DIR" >&2
  exit 1
fi

if [[ -z "$(find "$REMOTE_STAGING" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
  echo "[remote] staging dir $REMOTE_STAGING is empty; refusing to sync to avoid clearing $REMOTE_DIR" >&2
  exit 1
fi

RSYNC_OPTS=(-a)
if [[ "$REMOTE_DELETE" == "true" ]]; then
  RSYNC_OPTS+=(--delete)
else
  echo "[remote] remote delete disabled (sync may leave old files)"
fi

PROTECTED_PATHS=(
  ".venv/"
  ".env"
  ".env.racknerd"
  "archive/"
  "data/"
  "logs/"
  "secrets/"
)
for protected_path in "${PROTECTED_PATHS[@]}"; do
  RSYNC_OPTS+=(--filter="P $protected_path")
done

if [[ $(id -u) -eq 0 ]]; then
  RSYNC_OPTS+=(--chown="$OWNER_GROUP")
else
  echo "[remote] running without root; skipping --chown"
fi

echo "[remote] staging -> app: $REMOTE_STAGING -> $REMOTE_DIR (owner $OWNER_GROUP)"
echo "[remote] running: rsync ${RSYNC_OPTS[*]} $REMOTE_STAGING/ -> $REMOTE_DIR/"
rsync "${RSYNC_OPTS[@]}" "$REMOTE_STAGING/" "$REMOTE_DIR/"
echo "[remote] rsync promotion done"
REMOTE_SCRIPT

ssh -S "$SSH_CONTROL_PATH" "$REMOTE_HOST" "chmod 750 '$REMOTE_PROMOTE_SCRIPT'"
if ! ssh -S "$SSH_CONTROL_PATH" -tt "$REMOTE_HOST" "sudo -u ${REMOTE_PROMOTE_USER} '$REMOTE_PROMOTE_SCRIPT' '$REMOTE_STAGING' '$REMOTE_DIR' '$OWNER_GROUP' '$RSYNC_DELETE' '$DEBUG'"; then
  PROMOTE_EXIT=$?
  ssh -S "$SSH_CONTROL_PATH" "$REMOTE_HOST" "rm -f '$REMOTE_PROMOTE_SCRIPT'" || true
  exit "$PROMOTE_EXIT"
fi
ssh -S "$SSH_CONTROL_PATH" "$REMOTE_HOST" "rm -f '$REMOTE_PROMOTE_SCRIPT'" || true

if "$SHOULD_REMOVE_REMOTE_VENV"; then
  VENV_REASON=${REMOVE_REMOTE_VENV_REASON:-uv.lock changed}
  echo "→ Removing remote virtualenv at $REMOTE_DIR/.venv ($VENV_REASON)"
  REMOVE_VENV_CMD=$(printf "sudo bash -lc %q" "if [[ -d '$REMOTE_DIR/.venv' ]]; then rm -rf '$REMOTE_DIR/.venv'; fi")
  ssh -S "$SSH_CONTROL_PATH" -tt "$REMOTE_HOST" "$REMOVE_VENV_CMD"
else
  echo "→ uv.lock unchanged; preserving remote virtualenv"
fi

if "$DO_INSTALL"; then
  echo "→ Installing Python deps with uv in remote venv (Python $PY_VER)"
  ENV_SCRIPT="$REMOTE_DIR/scripts/setup_uv_env.sh"
  printf -v REMOTE_CMD 'sudo -u %q -H bash -lc %q' \
    "$SERVICE_USER" \
    "set -euo pipefail; if [[ ! -x \"$ENV_SCRIPT\" ]]; then echo 'Env setup script missing or not executable: $ENV_SCRIPT' >&2; exit 1; fi; \"$ENV_SCRIPT\" --python-version \"$PY_VER\""
  ssh -S "$SSH_CONTROL_PATH" -tt "$REMOTE_HOST" "$REMOTE_CMD"
  ENV_REFRESHED=true
fi

if [[ "$ENV_REFRESHED" != "true" && "$SHOULD_REMOVE_REMOTE_VENV" == "true" ]]; then
  echo "→ Rebuilding remote uv environment via setup script (venv was removed)"
  ENV_SCRIPT="$REMOTE_DIR/scripts/setup_uv_env.sh"
  printf -v REMOTE_CMD 'sudo -u %q -H bash -lc %q' \
    "$SERVICE_USER" \
    "set -euo pipefail; if [[ ! -x \"$ENV_SCRIPT\" ]]; then echo 'Env setup script missing or not executable: $ENV_SCRIPT' >&2; exit 1; fi; \"$ENV_SCRIPT\" --python-version \"$PY_VER\""
  ssh -S "$SSH_CONTROL_PATH" -tt "$REMOTE_HOST" "$REMOTE_CMD"
  ENV_REFRESHED=true
fi

PLAYWRIGHT_BIN="$REMOTE_DIR/.venv/bin/playwright"
printf -v REMOTE_PLAYWRIGHT_CMD 'set -euo pipefail; if [[ -x %q ]]; then %q install chromium; else echo "Playwright CLI not found at %s; skipping install" >&2; fi' \
  "$PLAYWRIGHT_BIN" "$PLAYWRIGHT_BIN" "$PLAYWRIGHT_BIN"
ssh -S "$SSH_CONTROL_PATH" -tt "$REMOTE_HOST" "$(printf "sudo -u %q -H bash -lc %q" "$SERVICE_USER" "$REMOTE_PLAYWRIGHT_CMD")"

echo "→ Copying .env.racknerd to .env via sudo cp"
CP_ENV_CMD=$(printf "bash -lc %q" "cd '$REMOTE_DIR' && if [[ -f .env.racknerd ]]; then sudo cp .env.racknerd .env && sudo chown '$SERVICE_USER:$SERVICE_GROUP' .env && sudo chmod 600 .env; else echo 'Warning: .env.racknerd missing; skipping copy' >&2; fi")
ssh -S "$SSH_CONTROL_PATH" -tt "$REMOTE_HOST" "$CP_ENV_CMD"

if "$INSTALL_CRONTAB"; then
  echo "→ Installing crontab for $SERVICE_USER from $REMOTE_DIR/crontab"
  printf -v REMOTE_CRON_CMD 'set -euo pipefail; CRON_FILE=%q; TARGET_USER=%q; if [[ ! -f "$CRON_FILE" ]]; then echo "Crontab file missing: $CRON_FILE" >&2; exit 1; fi; sudo crontab -u "$TARGET_USER" "$CRON_FILE"; echo "Installed crontab for $TARGET_USER from $CRON_FILE"' \
    "$REMOTE_DIR/crontab" \
    "$SERVICE_USER"
  ssh -S "$SSH_CONTROL_PATH" -tt "$REMOTE_HOST" "$(printf "bash -lc %q" "$REMOTE_CRON_CMD")"
else
  echo "→ Skipping crontab installation (--no-crontab)"
fi

if "$RESTART_SUP"; then
  echo "→ Installing Supervisor config from repo sample"
  printf -v REMOTE_SUP_INSTALL_CMD 'set -euo pipefail; SOURCE_CONF=%q; TARGET_CONF=%q; if [[ ! -f "$SOURCE_CONF" ]]; then echo "Supervisor config missing: $SOURCE_CONF" >&2; exit 1; fi; sudo cp "$SOURCE_CONF" "$TARGET_CONF"; sudo chown root:root "$TARGET_CONF"; sudo chmod 644 "$TARGET_CONF"' \
    "$REMOTE_DIR/supervisor.conf" \
    "/etc/supervisor/conf.d/news_app.conf"
  ssh -S "$SSH_CONTROL_PATH" -tt "$REMOTE_HOST" "$(printf "bash -lc %q" "$REMOTE_SUP_INSTALL_CMD")"

  echo "→ Reloading Supervisor configuration"
  printf -v REMOTE_SUP_CMD 'set -euo pipefail; sudo supervisorctl reread && sudo supervisorctl update'
  ssh -S "$SSH_CONTROL_PATH" -tt "$REMOTE_HOST" "$(printf "bash -lc %q" "$REMOTE_SUP_CMD")"

  echo "→ Restarting all supervisor programs"
  ssh -S "$SSH_CONTROL_PATH" -tt "$REMOTE_HOST" "sudo supervisorctl restart all"

  echo "→ Validating required supervisor programs"
  validate_supervisor_state "${PROGRAMS[@]}"
fi

echo "✅ App sync completed to $REMOTE_HOST:$REMOTE_DIR"
