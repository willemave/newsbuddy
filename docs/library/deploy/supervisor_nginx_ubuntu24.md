# Ubuntu 24.04 setup: Supervisor + Nginx for `news_app`

This guide matches the current production shape and the deploy flow in
[`/.github/workflows/bare-metal-deploy.yml`](../../../.github/workflows/bare-metal-deploy.yml).

Supervisor manages:
- `news_app_server`
- `news_app_workers_content`
- `news_app_workers_image`
- `news_app_workers_transcribe`
- `news_app_workers_onboarding`
- `news_app_workers_chat`
- optional `news_app_queue_watchdog`
- optional `news_app_bgutil_provider`

Cron manages:
- `scripts/run_scrapers.py`
- `scripts/run_daily_news_digest.py`
- `scripts/run_feed_discovery.py`

## 1) Install packages

```bash
sudo apt update
sudo apt install -y supervisor nginx git python3-venv python3-pip
```

## 2) App user, directories, and code

```bash
sudo adduser --system --group --home /opt/news_app newsapp
sudo mkdir -p /var/log/news_app
sudo chown -R newsapp:newsapp /var/log/news_app

sudo -u newsapp mkdir -p /opt/news_app/logs
sudo rm -rf /opt/news_app/logs 2>/dev/null || true
sudo ln -sfn /var/log/news_app /opt/news_app/logs
```

## 3) Python environment and env file

For manual setup:

```bash
sudo -u newsapp -H bash -lc '
  cd /opt/news_app
  python3 -m venv .venv
  . .venv/bin/activate
  pip install --upgrade pip wheel
  pip install -r requirements.txt
'
```

For production deploys, GitHub Actions recreates the env with:

```bash
sudo -u newsapp -H bash -lc 'cd /opt/news_app && ./scripts/setup_uv_env.sh --python-version 3.13 --recreate'
```

Ensure `/opt/news_app/.env` exists. In production the deploy copies `.env.racknerd` to `.env` if present.

## 4) Supervisor programs

Create `/etc/supervisor/conf.d/news_app.conf` using the repo sample in
[`supervisor.conf`](../../../supervisor.conf). The deploy scripts now install this file from the repo before `supervisorctl reread`.

Key programs:

```ini
[program:news_app_server]
command=/bin/bash -lc "/opt/news_app/scripts/start_server.sh"

[program:news_app_workers_content]
command=/bin/bash -lc "/opt/news_app/.venv/bin/python /opt/news_app/scripts/run_workers.py --queue content --worker-slot %(process_num)s --stats-interval 60"

[program:news_app_workers_image]
command=/bin/bash -lc "/opt/news_app/.venv/bin/python /opt/news_app/scripts/run_workers.py --queue image --worker-slot 1 --stats-interval 60"

[program:news_app_workers_transcribe]
command=/bin/bash -lc "/opt/news_app/.venv/bin/python /opt/news_app/scripts/run_workers.py --queue transcribe --worker-slot %(process_num)s --stats-interval 60"

[program:news_app_workers_onboarding]
command=/bin/bash -lc "/opt/news_app/.venv/bin/python /opt/news_app/scripts/run_workers.py --queue onboarding --worker-slot 1 --stats-interval 60"

[program:news_app_workers_chat]
command=/bin/bash -lc "/opt/news_app/.venv/bin/python /opt/news_app/scripts/run_workers.py --queue chat --worker-slot 1 --stats-interval 60"

[program:news_app_bgutil_provider]
command=/bin/bash -lc "/opt/news_app/scripts/start_bgutil_provider.sh"
```

Enable and load Supervisor:

```bash
sudo systemctl enable --now supervisor
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl status
```

Start or restart:

```bash
sudo supervisorctl start news_app_server
sudo supervisorctl start news_app_workers_content
sudo supervisorctl start news_app_workers_image
sudo supervisorctl start news_app_workers_transcribe
sudo supervisorctl start news_app_workers_onboarding
sudo supervisorctl start news_app_workers_chat
sudo supervisorctl status
```

Tail logs:

```bash
sudo tail -n 100 -f \
  /var/log/news_app/server.log \
  /var/log/news_app/workers.log \
  /var/log/news_app/workers_image.log
```

## 5) Cron-managed schedulers

The deploy workflow syncs the repo [`crontab`](../../../crontab) onto the
`newsapp` user every deploy.

Install manually if needed:

```bash
sudo -u newsapp -H crontab /opt/news_app/crontab
sudo -u newsapp -H crontab -l
```

`run_daily_news_digest.py` is cron-driven, not Supervisor-driven.

Current production cadence:

```cron
0 */3 * * * cd /opt/news_app && /opt/news_app/.venv/bin/python scripts/run_daily_news_digest.py --lookback-hours 6 >> /var/log/news_app/daily-news-digest.log 2>&1
```

The scheduler polls every 3 hours and enqueues users whose latest local digest
checkpoint fell within the recent lookback window. Per-user checkpoint cadence
comes from `news_digest_interval_hours` with supported values `3`, `6`, or `12`.

## 6) GitHub Actions deploy behavior

The bare-metal deploy workflow:
1. rsyncs the repo to `/opt/news_app`
2. refreshes `.env` from `.env.racknerd` when present
3. recreates the uv virtualenv
4. runs Alembic migrations
5. syncs the repo `crontab`
6. runs `supervisorctl reread`, `update`, `start`

The workflow restarts only programs listed in `DEPLOY_PROGRAMS`. That variable must include the
image worker after this queue split. Minimum value:

```text
news_app_server news_app_workers_content news_app_workers_image news_app_workers_transcribe news_app_workers_onboarding news_app_workers_chat
```

If the watchdog is enabled on the host, append `news_app_queue_watchdog`. If YouTube PO token support is enabled, also append `news_app_bgutil_provider`.

## 7) Post-deploy image-queue recovery

After the image queue rollout, move old pending image tasks out of `content`:

```bash
cd /opt/news_app
.venv/bin/python scripts/queue_control.py move-queue \
  --from-queue content \
  --to-queue image \
  --task-type generate_image \
  --status pending \
  --yes
```

Then verify:
- `news_app_workers_image` is draining image tasks
- content workers are no longer blocked behind image backlog
- pending daily digest tasks on `content` begin completing

## 8) Nginx reverse proxy

Create `/etc/nginx/sites-available/news_app`:

```nginx
server {
    listen 80;
    server_name news.example.com;

    client_max_body_size 25m;

    location /static/images/ {
        alias /data/images/;
        expires 30d;
        access_log off;
        add_header Cache-Control "public, max-age=2592000";
    }

    location /static/ {
        alias /opt/news_app/static/;
        expires 30d;
        access_log off;
        add_header Cache-Control "public, max-age=2592000";
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_connect_timeout 60s;
        proxy_send_timeout    120s;
        proxy_read_timeout    120s;
    }

    access_log /var/log/nginx/news_app.access.log;
    error_log  /var/log/nginx/news_app.error.log;
}
```

Enable and reload:

```bash
sudo ln -sf /etc/nginx/sites-available/news_app /etc/nginx/sites-enabled/news_app
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

Optional HTTPS:

```bash
sudo snap install core
sudo snap refresh core
sudo snap install --classic certbot
sudo ln -s /snap/bin/certbot /usr/bin/certbot
sudo certbot --nginx -d news.example.com
sudo certbot renew --dry-run
```
