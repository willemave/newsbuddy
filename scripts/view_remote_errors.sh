#!/bin/bash
# Quick view of recent errors from remote server without full sync
# Usage: ./scripts/view_remote_errors.sh [hours_back]

REMOTE_USER="willem"
REMOTE_HOST="news-app-server"
REMOTE_LOGS_DIR="/data/logs"
HOURS_BACK=${1:-24}  # Default to last 24 hours

echo "=== Recent errors from ${REMOTE_HOST} (last ${HOURS_BACK} hours) ==="

# View recent error files
echo -e "\n--- Error log files ---"
ssh "${REMOTE_USER}@${REMOTE_HOST}" "find ${REMOTE_LOGS_DIR}/errors -type f -mmin -$((HOURS_BACK * 60)) 2>/dev/null | sort"

# Show tail of recent JSONL error files
echo -e "\n--- Recent error entries ---"
ssh "${REMOTE_USER}@${REMOTE_HOST}" "find ${REMOTE_LOGS_DIR}/errors -name '*.jsonl' -mmin -$((HOURS_BACK * 60)) -exec tail -n 5 {} \; 2>/dev/null"

# Show recent LLM JSON errors if they exist
echo -e "\n--- Recent LLM JSON errors ---"
ssh "${REMOTE_USER}@${REMOTE_HOST}" "test -f ${REMOTE_LOGS_DIR}/errors/llm_json_errors.log && tail -n 20 ${REMOTE_LOGS_DIR}/errors/llm_json_errors.log || echo 'No LLM errors found'"

# Show recent service logs (server, workers, scrapers)
echo -e "\n--- Recent service log errors (server/workers/scrapers) ---"
for logfile in server.log workers.log scrapers.log; do
  echo -e "\n${logfile}:"
  ssh "${REMOTE_USER}@${REMOTE_HOST}" "test -f /var/log/news_app/${logfile} && tail -n 50 /var/log/news_app/${logfile} | grep -i 'error\|exception\|failed\|traceback' | tail -n 10 || echo 'Log not found or no errors'"
done

# Disk usage of logs
echo -e "\n--- Log directory disk usage ---"
ssh "${REMOTE_USER}@${REMOTE_HOST}" "du -sh ${REMOTE_LOGS_DIR}/* 2>/dev/null | sort -h"
ssh "${REMOTE_USER}@${REMOTE_HOST}" "test -d /var/log/news_app && du -sh /var/log/news_app/*.log 2>/dev/null | sort -h || echo 'No service logs found'"
