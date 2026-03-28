#!/bin/bash
# Startup script for running task processing workers.
# This processes scraped content through the sequential task processor.

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"

# Change to project root
cd "$PROJECT_ROOT"
echo "Working directory: $(pwd)"

# Check if .env file exists
if [ ! -f ".env" ]; then
    echo "ERROR: .env file not found at $PROJECT_ROOT/.env"
    echo ""
    echo "Please ensure:"
    echo "1. .env file exists in the project root: $PROJECT_ROOT/"
    echo "2. Copy from .env.example if needed: cp .env.example .env"
    echo "3. Configure DATABASE_URL and other required variables"
    exit 1
fi

# If there is a project venv, use it, otherwise assume the current env is correct
if [ -f ".venv/bin/activate" ]; then
    echo "Activating project .venv"
    # shellcheck source=/dev/null
    source .venv/bin/activate
else
    echo "No .venv found, using current Python environment: $(python -c 'import sys; print(sys.executable)')"
fi

# Function to run commands with nice output
run_command() {
    local description="$1"
    shift
    echo ""
    echo "============================================================"
    echo "Running: $description"
    echo "Command: $*"
    echo "============================================================"
    
    if "$@"; then
        return 0
    else
        echo "ERROR: $description failed!"
        return 1
    fi
}

# Display database target for transparency
DATABASE_TARGET=$(PROJECT_ROOT="$PROJECT_ROOT" python <<'PY'
import os
from pathlib import Path
from sqlalchemy.engine.url import make_url
from app.core.settings import get_settings

project_root = Path(os.environ["PROJECT_ROOT"]).resolve()
settings = get_settings()
url = str(settings.database_url)
parsed = make_url(url)

if parsed.drivername.startswith("sqlite"):
    database = parsed.database or ""
    db_path = Path(database).expanduser()
    if not db_path.is_absolute():
        db_path = (project_root / db_path).resolve()
    else:
        db_path = db_path.resolve()
    print(db_path)
else:
    print(url)
PY
)
echo "Database target: ${DATABASE_TARGET}"

# Check if alembic.ini exists
if [ ! -f "alembic.ini" ]; then
    echo "WARNING: alembic.ini not found. Skipping migration check."
else
    # Run migrations (idempotent - safe to run multiple times)
    echo ""
    echo "🔄 Running database migrations..."
    if ! run_command "Alembic migrations" python -m alembic upgrade head; then
        echo ""
        echo "⚠️  Migration failed! Continuing anyway, but may encounter errors."
        echo "    Check that DATABASE_URL is correct and database is accessible."
    else
        echo "✅ Migrations completed successfully!"
    fi
fi

# Ensure only Playwright Chromium browser is installed; other browsers not needed
echo ""
echo "Ensuring Playwright Chromium browser is available (other browsers not required)..."
if ! run_command "Install Playwright Chromium browser" .venv/bin/playwright install chromium; then
    exit 1
fi

# Parse command line arguments
MAX_TASKS=""
DEBUG_ENABLED=false
STATS_INTERVAL="30"
CONTENT_WORKERS="${CONTENT_WORKER_PROCS:-2}"
TRANSCRIBE_WORKERS="${TRANSCRIBE_WORKER_PROCS:-1}"
ONBOARDING_WORKERS="${ONBOARDING_WORKER_PROCS:-1}"
TWITTER_WORKERS="${TWITTER_WORKER_PROCS:-1}"
CHAT_WORKERS="${CHAT_WORKER_PROCS:-1}"

while [[ $# -gt 0 ]]; do
    case $1 in
        --debug)
            DEBUG_ENABLED=true
            shift
            ;;
        --max-tasks)
            MAX_TASKS="$2"
            shift 2
            ;;
        --stats-interval)
            STATS_INTERVAL="$2"
            shift 2
            ;;
        --content-workers)
            CONTENT_WORKERS="$2"
            shift 2
            ;;
        --transcribe-workers)
            TRANSCRIBE_WORKERS="$2"
            shift 2
            ;;
        --onboarding-workers)
            ONBOARDING_WORKERS="$2"
            shift 2
            ;;
        --twitter-workers)
            TWITTER_WORKERS="$2"
            shift 2
            ;;
        --chat-workers)
            CHAT_WORKERS="$2"
            shift 2
            ;;
        --no-stats)
            STATS_INTERVAL="0"
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [options]"
            echo ""
            echo "Options:"
            echo "  --debug              Enable debug logging"
            echo "  --max-tasks N        Process at most N tasks then exit"
            echo "  --stats-interval N   Show stats every N seconds (default: 30)"
            echo "  --content-workers N  Number of content queue workers (default: 2)"
            echo "  --transcribe-workers N  Number of transcribe queue workers (default: 1)"
            echo "  --onboarding-workers N  Number of onboarding queue workers (default: 1)"
            echo "  --twitter-workers N  Number of Twitter queue workers (default: 1)"
            echo "  --chat-workers N     Number of chat queue workers (default: 1)"
            echo "  --no-stats           Disable periodic stats display"
            echo "  -h, --help           Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Run '$0 --help' for usage information"
            exit 1
            ;;
    esac
done

# Check database connection
echo ""
echo "🔍 Checking database connection..."
if ! python -c "from app.core.db import init_db; init_db()" 2>/dev/null; then
    echo "❌ Database connection failed!"
    echo ""
    echo "Please ensure:"
    echo "1. Database is running"
    echo "2. DATABASE_URL is correctly set in .env"
    echo "3. Database exists and is accessible"
    exit 1
fi
echo "✅ Database connection successful!"

# Check queue status
echo ""
echo "📊 Checking task queue status..."
QUEUE_CHECK=$(python -c "
from app.core.db import init_db
from app.services.queue import get_queue_service
init_db()
queue = get_queue_service()
stats = queue.get_queue_stats()
pending_by_queue = stats.get('pending_by_queue', {})
by_status = stats.get('by_status', {})
pending_total = sum(pending_by_queue.values())
print(f'pending_total:{pending_total}')
print(f'pending_content:{pending_by_queue.get(\"content\", 0)}')
print(f'pending_transcribe:{pending_by_queue.get(\"transcribe\", 0)}')
print(f'pending_onboarding:{pending_by_queue.get(\"onboarding\", 0)}')
print(f'pending_twitter:{pending_by_queue.get(\"twitter\", 0)}')
print(f'pending_chat:{pending_by_queue.get(\"chat\", 0)}')
print(f'completed:{by_status.get(\"completed\", 0)}')
print(f'failed:{by_status.get(\"failed\", 0)}')
" 2>/dev/null)

if [ -z "$QUEUE_CHECK" ]; then
    echo "⚠️  Could not check queue status. Proceeding anyway..."
else
    PENDING_TOTAL=$(echo "$QUEUE_CHECK" | grep "pending_total:" | cut -d: -f2)
    PENDING_CONTENT=$(echo "$QUEUE_CHECK" | grep "pending_content:" | cut -d: -f2)
    PENDING_TRANSCRIBE=$(echo "$QUEUE_CHECK" | grep "pending_transcribe:" | cut -d: -f2)
    PENDING_ONBOARDING=$(echo "$QUEUE_CHECK" | grep "pending_onboarding:" | cut -d: -f2)
    PENDING_TWITTER=$(echo "$QUEUE_CHECK" | grep "pending_twitter:" | cut -d: -f2)
    PENDING_CHAT=$(echo "$QUEUE_CHECK" | grep "pending_chat:" | cut -d: -f2)
    COMPLETED=$(echo "$QUEUE_CHECK" | grep "completed:" | cut -d: -f2)
    FAILED=$(echo "$QUEUE_CHECK" | grep "failed:" | cut -d: -f2)
    
    echo "  Pending tasks (total): $PENDING_TOTAL"
    echo "    content: $PENDING_CONTENT"
    echo "    transcribe: $PENDING_TRANSCRIBE"
    echo "    onboarding: $PENDING_ONBOARDING"
    echo "    twitter: $PENDING_TWITTER"
    echo "    chat: $PENDING_CHAT"
    echo "  Completed: $COMPLETED"
    echo "  Failed: $FAILED"
    
    if [ "$PENDING_TOTAL" = "0" ]; then
        echo ""
        echo "⚠️  No pending tasks in queue!"
        echo "💡 Run './scripts/start_scrapers.sh' first to populate content"
        echo "↪️  Continuing without prompt; workers will wait for new tasks."
    fi
fi

if ! [[ "$CONTENT_WORKERS" =~ ^[0-9]+$ ]] || ! [[ "$TRANSCRIBE_WORKERS" =~ ^[0-9]+$ ]] || ! [[ "$ONBOARDING_WORKERS" =~ ^[0-9]+$ ]] || ! [[ "$TWITTER_WORKERS" =~ ^[0-9]+$ ]] || ! [[ "$CHAT_WORKERS" =~ ^[0-9]+$ ]]; then
    echo "❌ Worker counts must be non-negative integers"
    exit 1
fi

TOTAL_WORKERS=$((CONTENT_WORKERS + TRANSCRIBE_WORKERS + ONBOARDING_WORKERS + TWITTER_WORKERS + CHAT_WORKERS))
if [ "$TOTAL_WORKERS" -le 0 ]; then
    echo "❌ At least one worker must be enabled"
    exit 1
fi

# Show what we're about to run
echo ""
echo "🚀 Starting task processing workers (multi-queue)..."

if [ -n "$MAX_TASKS" ]; then
    echo "Max tasks per worker: $MAX_TASKS"
else
    echo "Max tasks: unlimited (run until interrupted)"
fi

if [ "$DEBUG_ENABLED" = true ]; then
    echo "Debug mode: ENABLED"
fi

if [ "$STATS_INTERVAL" != "0" ]; then
    echo "Stats interval: every $STATS_INTERVAL seconds"
else
    echo "Stats display: DISABLED"
fi
echo "Worker pools: content=$CONTENT_WORKERS transcribe=$TRANSCRIBE_WORKERS onboarding=$ONBOARDING_WORKERS twitter=$TWITTER_WORKERS chat=$CHAT_WORKERS"

echo ""
echo "Press Ctrl+C to stop gracefully"
echo ""

PIDS=()
LABELS=()

launch_workers() {
    local queue="$1"
    local count="$2"
    local slot=1

    while [ "$slot" -le "$count" ]; do
        local cmd=(python scripts/run_workers.py --queue "$queue" --worker-slot "$slot" --stats-interval "$STATS_INTERVAL")
        if [ "$DEBUG_ENABLED" = true ]; then
            cmd+=(--debug)
        fi
        if [ -n "$MAX_TASKS" ]; then
            cmd+=(--max-tasks "$MAX_TASKS")
        fi

        echo "▶️  Launching ${queue} worker ${slot}: ${cmd[*]}"
        "${cmd[@]}" &
        PIDS+=("$!")
        LABELS+=("${queue}#${slot}")
        slot=$((slot + 1))
    done
}

graceful_shutdown() {
    echo ""
    echo "✋ Workers stopped by user; terminating child processes..."
    for pid in "${PIDS[@]}"; do
        kill -TERM "$pid" 2>/dev/null || true
    done
    wait
    exit 0
}

trap graceful_shutdown INT TERM

launch_workers "content" "$CONTENT_WORKERS"
launch_workers "transcribe" "$TRANSCRIBE_WORKERS"
launch_workers "onboarding" "$ONBOARDING_WORKERS"
launch_workers "twitter" "$TWITTER_WORKERS"
launch_workers "chat" "$CHAT_WORKERS"

EXIT_CODE=0
for idx in "${!PIDS[@]}"; do
    pid="${PIDS[$idx]}"
    label="${LABELS[$idx]}"
    if wait "$pid"; then
        echo "✅ Worker exited cleanly: $label"
    else
        code=$?
        echo "❌ Worker failed: $label (exit=$code)"
        EXIT_CODE=1
    fi
done

if [ "$EXIT_CODE" -ne 0 ]; then
    echo ""
    echo "❌ One or more workers failed"
    exit 1
fi

echo ""
echo "✅ Task processing completed!"

# Show final stats
echo ""
echo "📊 Final queue status:"
python -c "
from app.core.db import init_db
from app.services.queue import get_queue_service
init_db()
queue = get_queue_service()
stats = queue.get_queue_stats()
by_status = stats.get('by_status', {})
pending_by_queue = stats.get('pending_by_queue', {})
pending = sum(pending_by_queue.values())
print(f'  Pending tasks (total): {pending}')
print(f'    content: {pending_by_queue.get(\"content\", 0)}')
print(f'    transcribe: {pending_by_queue.get(\"transcribe\", 0)}')
print(f'    onboarding: {pending_by_queue.get(\"onboarding\", 0)}')
print(f'    chat: {pending_by_queue.get(\"chat\", 0)}')
print(f'  Completed: {by_status.get(\"completed\", 0)}')
print(f'  Failed: {by_status.get(\"failed\", 0)}')
" 2>/dev/null || echo "  Could not retrieve final stats"
