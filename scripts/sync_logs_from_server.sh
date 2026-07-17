#!/bin/bash
# Sync logs from remote server to local machine
# Usage: ./scripts/sync_logs_from_server.sh

set -e

# Configuration
REMOTE_USER="willem"
REMOTE_HOST="news-app-server"
REMOTE_LOGS_DIR="/data/logs"
REMOTE_APP_DIR="/opt/news_app"
LOCAL_LOGS_DIR="./logs_from_server"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}=== Syncing logs from ${REMOTE_HOST} ===${NC}"

# Create local logs directory if it doesn't exist
mkdir -p "$LOCAL_LOGS_DIR"

# Sync main application logs from /data/logs
echo -e "\n${GREEN}Syncing application logs from ${REMOTE_LOGS_DIR}...${NC}"
rsync -avz --progress \
  "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_LOGS_DIR}/" \
  "${LOCAL_LOGS_DIR}/"

# Sync service logs from /var/log/news_app (server, workers, scrapers)
echo -e "\n${GREEN}Syncing service logs from /var/log/news_app...${NC}"
if ssh "${REMOTE_USER}@${REMOTE_HOST}" "test -d /var/log/news_app"; then
  mkdir -p "$LOCAL_LOGS_DIR/service_logs"
  rsync -avz --progress \
    "${REMOTE_USER}@${REMOTE_HOST}:/var/log/news_app/*.log" \
    "${LOCAL_LOGS_DIR}/service_logs/" 2>/dev/null || echo -e "${YELLOW}Some service logs may not exist${NC}"

  # Also get any rotated logs
  rsync -avz --progress \
    "${REMOTE_USER}@${REMOTE_HOST}:/var/log/news_app/*.log.*" \
    "${LOCAL_LOGS_DIR}/service_logs/" 2>/dev/null || true
else
  echo -e "${YELLOW}No service logs found at /var/log/news_app${NC}"
fi

# Also sync supervisor logs if they exist (mentioned in CLAUDE.md section 18)
echo -e "\n${GREEN}Checking for supervisor logs...${NC}"
if ssh "${REMOTE_USER}@${REMOTE_HOST}" "test -d /var/log/newsly"; then
  echo -e "${GREEN}Syncing supervisor logs from /var/log/newsly...${NC}"
  mkdir -p "$LOCAL_LOGS_DIR/supervisor"
  rsync -avz --progress \
    "${REMOTE_USER}@${REMOTE_HOST}:/var/log/newsly/" \
    "${LOCAL_LOGS_DIR}/supervisor/"
else
  echo -e "${YELLOW}No supervisor logs found at /var/log/newsly${NC}"
fi

# Get recent application logs from the app directory if they exist there too
echo -e "\n${GREEN}Checking for logs in ${REMOTE_APP_DIR}/logs...${NC}"
if ssh "${REMOTE_USER}@${REMOTE_HOST}" "test -d ${REMOTE_APP_DIR}/logs"; then
  echo -e "${GREEN}Syncing logs from ${REMOTE_APP_DIR}/logs...${NC}"
  mkdir -p "$LOCAL_LOGS_DIR/app_logs"
  rsync -avz --progress \
    "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_APP_DIR}/logs/" \
    "${LOCAL_LOGS_DIR}/app_logs/"
else
  echo -e "${YELLOW}No logs found at ${REMOTE_APP_DIR}/logs${NC}"
fi

echo -e "\n${BLUE}=== Sync complete ===${NC}"
echo -e "${GREEN}Logs synced to: ${LOCAL_LOGS_DIR}${NC}"
echo -e "\n${BLUE}Log directory structure:${NC}"
tree -L 2 "$LOCAL_LOGS_DIR" 2>/dev/null || ls -lah "$LOCAL_LOGS_DIR"

echo -e "\n${YELLOW}Service logs (server/workers/scrapers):${NC}"
ls -lah "$LOCAL_LOGS_DIR/service_logs/" 2>/dev/null || echo "No service logs found"

echo -e "\n${YELLOW}Recent error logs:${NC}"
find "$LOCAL_LOGS_DIR" -name "*.jsonl" -o -name "*error*.log" | head -10

echo -e "\n${GREEN}You can now reference these logs locally!${NC}"
echo -e "${BLUE}Key log locations:${NC}"
echo -e "  - Service logs: ${LOCAL_LOGS_DIR}/service_logs/"
echo -e "  - Error logs: ${LOCAL_LOGS_DIR}/errors/"
echo -e "  - App logs: ${LOCAL_LOGS_DIR}/"
