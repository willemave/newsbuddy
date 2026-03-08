# Ubuntu 24.04 setup: Supervisor + Nginx for `news_app`

This guide installs Supervisor and Nginx, wires them to your repo’s scripts, and starts:
- server: `scripts/start_server.sh` (uvicorn on 127.0.0.1:8000)
- workers: `scripts/start_workers.sh`
- scrapers: `scripts/start_scrapers.sh` (looped on an interval under Supervisor)

> Adjust domain names, paths, and environment values as needed.

---

## 1) Install packages

```bash
sudo apt update
sudo apt install -y supervisor nginx git python3-venv python3-pip
```

## 2) App user, directories, and code

```bash
# System user + log directory
sudo adduser --system --group --home /opt/news_app newsapp
sudo mkdir -p /var/log/news_app
sudo chown -R newsapp:newsapp /var/log/news_app

# Deploy your code into /opt/news_app (pick one approach)
# a) Copy/rsync from your machine, or
# b) Clone a repository (example):
# sudo -u newsapp git clone https://your.git.repo.git /opt/news_app

# Optional: keep app logs under /var/log/news_app while app writes to ./logs
sudo -u newsapp mkdir -p /opt/news_app/logs
sudo rm -rf /opt/news_app/logs 2>/dev/null || true
sudo ln -sfn /var/log/news_app /opt/news_app/logs
```

## 3) Python environment and dependencies

```bash
sudo -u newsapp bash -lc '
  cd /opt/news_app
  python3 -m venv .venv
  . .venv/bin/activate
  pip install --upgrade pip wheel
  pip install -r requirements.txt
'
```

Ensure `/opt/news_app/.env` exists (Pydantic Settings reads it automatically):

```bash
sudo -u newsapp tee /opt/news_app/.env >/dev/null <<'EOF'
# Example values — change for your environment
DATABASE_URL=sqlite:////opt/news_app/news_app.db
# DATABASE_URL=postgresql+psycopg2://user:pass@localhost:5432/news_app
APP_NAME=News Aggregator
LOG_LEVEL=INFO
EOF
```

## 4) Supervisor programs

Create `/etc/supervisor/conf.d/news_app.conf`:

```ini
[program:news_app_server]
command=/bin/bash -lc "/opt/news_app/scripts/start_server.sh"
directory=/opt/news_app
user=newsapp
autostart=true
autorestart=true
stopsignal=TERM
startretries=3
stdout_logfile=/var/log/news_app/server.log
stderr_logfile=/var/log/news_app/server.err.log
environment=ENVIRONMENT="production"

[program:news_app_workers_content]
command=/bin/bash -lc "/opt/news_app/.venv/bin/python /opt/news_app/scripts/run_workers.py --queue content --worker-slot 1 --stats-interval 60"
directory=/opt/news_app
user=newsapp
autostart=true
autorestart=true
stopasgroup=true
killasgroup=true
stopsignal=TERM
stdout_logfile=/var/log/news_app/workers-content.log
stderr_logfile=/var/log/news_app/workers-content.err.log
environment=ENVIRONMENT="production"

[program:news_app_workers_transcribe]
command=/bin/bash -lc "/opt/news_app/.venv/bin/python /opt/news_app/scripts/run_workers.py --queue transcribe --worker-slot 1 --stats-interval 60"
directory=/opt/news_app
user=newsapp
autostart=true
autorestart=true
stopasgroup=true
killasgroup=true
stopsignal=TERM
stdout_logfile=/var/log/news_app/workers-transcribe.log
stderr_logfile=/var/log/news_app/workers-transcribe.err.log
environment=ENVIRONMENT="production"

[program:news_app_workers_onboarding]
command=/bin/bash -lc "/opt/news_app/.venv/bin/python /opt/news_app/scripts/run_workers.py --queue onboarding --worker-slot 1 --stats-interval 60"
directory=/opt/news_app
user=newsapp
autostart=true
autorestart=true
stopasgroup=true
killasgroup=true
stopsignal=TERM
stdout_logfile=/var/log/news_app/workers-onboarding.log
stderr_logfile=/var/log/news_app/workers-onboarding.err.log
environment=ENVIRONMENT="production"

[program:news_app_workers_chat]
command=/bin/bash -lc "/opt/news_app/.venv/bin/python /opt/news_app/scripts/run_workers.py --queue chat --worker-slot 1 --stats-interval 60"
directory=/opt/news_app
user=newsapp
autostart=true
autorestart=true
stopasgroup=true
killasgroup=true
stopsignal=TERM
stdout_logfile=/var/log/news_app/workers-chat.log
stderr_logfile=/var/log/news_app/workers-chat.err.log
environment=ENVIRONMENT="production"

; Scrapers are one-shot; run them periodically (default: every 15m = 900s)
[program:news_app_scrapers]
command=/bin/bash -lc 'while true; do /opt/news_app/scripts/start_scrapers.sh --show-stats; sleep ${SCRAPER_INTERVAL_SECONDS:-900}; done'
directory=/opt/news_app
user=newsapp
autostart=true
autorestart=true
stopsignal=INT
stdout_logfile=/var/log/news_app/scrapers.log
stderr_logfile=/var/log/news_app/scrapers.err.log
environment=ENVIRONMENT="production",SCRAPER_INTERVAL_SECONDS="900"

[program:news_app_queue_watchdog]
command=/bin/bash -lc "/opt/news_app/scripts/start_queue_watchdog.sh"
directory=/opt/news_app
user=newsapp
autostart=true
autorestart=true
stopasgroup=true
killasgroup=true
stopsignal=TERM
stdout_logfile=/var/log/news_app/watchdog.log
stderr_logfile=/var/log/news_app/watchdog.err.log
environment=ENVIRONMENT="production",QUEUE_WATCHDOG_TRANSCRIBE_STALE_HOURS="2",QUEUE_WATCHDOG_PROCESS_CONTENT_STALE_HOURS="2",QUEUE_WATCHDOG_ALERT_THRESHOLD="1"
```

Enable and load Supervisor programs:

```bash
sudo systemctl enable --now supervisor
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl status
```

Start/verify:

```bash
sudo supervisorctl start news_app_server
sudo supervisorctl start news_app_workers_content
sudo supervisorctl start news_app_workers_transcribe
sudo supervisorctl start news_app_workers_onboarding
sudo supervisorctl start news_app_workers_chat
sudo supervisorctl start news_app_scrapers
sudo supervisorctl start news_app_queue_watchdog
sudo supervisorctl status
```

Tail logs:

```bash
sudo tail -n 100 -f \
  /var/log/news_app/server.log \
  /var/log/news_app/workers.log \
  /var/log/news_app/scrapers.log
```

## 5) Nginx reverse proxy

Create `/etc/nginx/sites-available/news_app`:

```nginx
server {
    listen 80;
    server_name news.example.com;  # TODO: change to your domain or server IP

    client_max_body_size 25m;

    # Generated images served directly by Nginx (persisted across deploys)
    location /static/images/ {
        alias /data/images/;
        expires 30d;
        access_log off;
        add_header Cache-Control "public, max-age=2592000";
    }

    # Static files served directly by Nginx
    location /static/ {
        alias /opt/news_app/static/;
        expires 30d;
        access_log off;
        add_header Cache-Control "public, max-age=2592000";
    }

    # Proxy to FastAPI (uvicorn) on 127.0.0.1:8000
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket/streaming
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        # Timeouts for longer requests/streaming
        proxy_connect_timeout 60s;
        proxy_send_timeout    120s;
        proxy_read_timeout    120s;
    }

    access_log /var/log/nginx/news_app.access.log;
    error_log  /var/log/nginx/news_app.error.log;
}
```

Enable the site and reload Nginx:

```bash
sudo ln -sf /etc/nginx/sites-available/news_app /etc/nginx/sites-enabled/news_app
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

### Optional: HTTPS (Certbot)

```bash
# Recommended on Ubuntu: snap-based Certbot
sudo snap install core; sudo snap refresh core
sudo snap install --classic certbot
sudo ln -s /snap/bin/certbot /usr/bin/certbot

# Obtain and install a certificate for your domain
sudo certbot --nginx -d news.example.com
# Auto-renew runs via systemd timer; test with:
sudo certbot renew --dry-run
```

## 6) Operational tips

- Change `SCRAPER_INTERVAL_SECONDS` in Supervisor to adjust scrape frequency.
- Ensure `.env` has a valid `DATABASE_URL` (SQLite or Postgres).
- Server binds to `127.0.0.1:8000` (from `scripts/start_server.sh`); Nginx handles public traffic.
- Generated images are stored under `/data/images` by default; override with `IMAGES_BASE_DIR` if needed.
- For one-off scrapes instead of the loop, disable `news_app_scrapers` and use cron, e.g.:

```bash
# Every 15 minutes via cron
( sudo crontab -u newsapp -l 2>/dev/null; echo "*/15 * * * * cd /opt/news_app && /bin/bash -lc './scripts/start_scrapers.sh --show-stats' >> /var/log/news_app/scrapers-cron.log 2>&1" ) | sudo crontab -u newsapp -
```

---

That’s it — your server, workers, and scrapers are now managed by Supervisor and fronted by Nginx.
