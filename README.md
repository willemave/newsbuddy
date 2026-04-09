<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/hero.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/assets/hero.svg">
    <img alt="Newsly" src="docs/assets/hero.svg" width="100%">
  </picture>
</p>

<p align="center">
  <strong>Stop drowning in tabs. Start understanding what matters.</strong>
</p>

<p align="center">
  <a href="#getting-started"><img src="https://img.shields.io/badge/python-3.13+-3776ab?style=flat-square&logo=python&logoColor=white" alt="Python 3.13+"></a>
  <a href="#getting-started"><img src="https://img.shields.io/badge/FastAPI-0.115+-009688?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI"></a>
  <a href="#cli"><img src="https://img.shields.io/badge/Go_CLI-1.23+-00add8?style=flat-square&logo=go&logoColor=white" alt="Go CLI"></a>
  <a href="#ios-app"><img src="https://img.shields.io/badge/SwiftUI-iOS_17+-007aff?style=flat-square&logo=swift&logoColor=white" alt="SwiftUI"></a>
  <a href="https://github.com/willemave/news_app/actions"><img src="https://img.shields.io/github/actions/workflow/status/willemave/news_app/docker-racknerd-deploy.yml?branch=main&style=flat-square&label=deploy" alt="Deploy"></a>
  <a href="docs/architecture.md"><img src="https://img.shields.io/badge/docs-architecture-8b5cf6?style=flat-square" alt="Docs"></a>
</p>

---

Newsly is an AI-powered knowledge companion that keeps you informed without the noise. It pulls in content from RSS feeds, podcasts, Hacker News, Reddit, Techmeme, X bookmarks, and any URL you throw at it — then summarizes everything with LLMs so you get focused, non-sensationalized reading on the things you actually care about.

> **Hosted version coming soon to the App Store.** Or [self-host it yourself](#getting-started) with your own API keys.

<br>

## Highlights

<table>
<tr>
<td width="50%">

### Focused Reading, Zero Noise
Stay informed on the go with content that respects your attention. Newsly delivers non-sensationalized, AI-summarized reading across the topics you care about and what's happening in the world — so you read what matters, when you want to.

</td>
<td width="50%">

### Your Council of Experts
Chat with a council of AI experts grounded in your entire knowledge base. Dig deeper into any article, corroborate claims across sources, and explore new angles — all in one conversation. Think of it as a research team that's read everything you have.

</td>
</tr>
<tr>
<td width="50%">

### RSS Feeds & Long-Form Content
First-class support for RSS and Atom feeds, podcasts, and long-form articles. Subscribe to your favorite blogs, newsletters, and shows — Newsly monitors them continuously and processes new content as it arrives.

</td>
<td width="50%">

### Fast Tech News
Curated, quick-hit summaries from Hacker News, Techmeme, Reddit, and more. Get the signal from the noisiest corners of the internet in seconds, not hours of scrolling.

</td>
</tr>
<tr>
<td width="50%">

### Discover New Knowledge
Newsly surfaces related content and new sources based on what you've read and what's trending across the open web — expanding your knowledge graph without you having to hunt for it.

</td>
<td width="50%">

### Sources You Already Use
Built-in integrations for **X bookmarks & follows**, **Hacker News**, **Techmeme**, **Reddit**, **Substack**, and any RSS/Atom feed. Connect the sources you already follow and let Newsly do the rest.

</td>
</tr>
<tr>
<td width="50%">

### CLI-Powered Content Management
A dedicated CLI lets your AI *claws* manage and curate your library from the terminal. Add feeds, submit one-off articles, manage sources, and organize by topic — the system classifies, processes, and enriches everything automatically so your knowledge base stays fresh.

</td>
<td width="50%">

### Local Markdown Sync
Export and sync your saved knowledge as Markdown files locally. Keep a searchable, offline archive of everything you've read for research, note-taking, or integration with your existing tools.

</td>
</tr>
</table>

<br>

## CLI

The `newsly-agent` CLI (Go, Cobra-based) gives you full control over your knowledge base from the terminal — perfect for scripting, automation, or letting your AI *claws* manage content on your behalf.

```bash
# Authenticate
newsly-agent auth login

# Subscribe to an RSS feed
newsly-agent sources add "https://simonwillison.net/atom/everything/" --feed-type rss --display-name "Simon Willison"

# Submit a one-off article and wait for processing
newsly-agent content submit "https://example.com/great-post" --wait

# Browse your unread content
newsly-agent content list --read-filter unread --limit 20

# Get today's fast news digests
newsly-agent news list --read-filter unread

# Convert a news item into a full article
newsly-agent news convert 4821

# Search across your sources
newsly-agent search "transformer architectures" --limit 10

# Sync your knowledge base to local Markdown
newsly-agent library sync --dir ~/newsly-library --include-source

# List all your feed subscriptions
newsly-agent sources list
```

For full architecture details, see **[docs/architecture.md](docs/architecture.md)**.

<br>

## Getting Started

### Prerequisites

- **Homebrew** for the native PostgreSQL local-dev path
- **uv** for Python environment management
- **Docker** and **Docker Compose** only if you want the containerized runtime

### Native PostgreSQL Quick Start

This is the default local-development path. For day-to-day local work, run the app and workers as normal host services against a local PostgreSQL instance.

```bash
# Clone
git clone https://github.com/willemave/news_app.git
cd news_app

# Install/start PostgreSQL, create the local app DB/user, and update .env
./scripts/setup_local_postgres.sh

# Install Python dependencies
uv sync && . .venv/bin/activate

# Start the full local stack
./scripts/start_services.sh all --env-file .env
```

The setup script installs Homebrew PostgreSQL if needed, starts the service, creates the `newsly` database + role, and rewrites `DATABASE_URL` in `.env` to point at `127.0.0.1:5432`.

### Docker Quick Start

Use this for staging or production-style container runs. Docker is not required for normal local development.

```bash
# Clone
git clone https://github.com/willemave/news_app.git
cd news_app

# Configure environment
cp .env.docker.example .env.docker.local
# Edit .env.docker.local with your secrets

# Start the single-container stack (FastAPI + embedded Postgres)
docker compose --env-file .env.docker.local up --build -d

# View logs
docker compose logs -f newsly
```

The container exposes:

- API: `http://127.0.0.1:8000`
- PostgreSQL: `127.0.0.1:5432`

Set `NEWSLY_RUNTIME_MODE=server` in `.env.docker.local` to skip workers, the queue watchdog, and the scheduler while keeping the API server and embedded Postgres.

### SQLite To PostgreSQL Migration

```bash
# Copy your existing SQLite file into the mounted local data directory
mkdir -p docker/local-data
cp /path/to/news_app.db docker/local-data/news_app.db

# Start the stack first so embedded Postgres is ready
docker compose --env-file .env.docker.local up --build -d

# Restore or seed PostgreSQL directly before first start.
# Legacy SQLite migration tooling has been removed.
```

### Local Start Scripts

For native local development, use the unified launcher with `.env`:

```bash
# Run the local long-running stack
./scripts/start_services.sh all --env-file .env

# Run just the API server
./scripts/start_services.sh server --env-file .env --port 8000 --reload

# Run just the workers
./scripts/start_services.sh workers --env-file .env --content-workers 2 --media-workers 1

# Run migrations explicitly
./scripts/start_services.sh migrate --env-file .env
```

The legacy entrypoints still work and now delegate to `start_services.sh`:

```bash
./scripts/start_server.sh --env-file .env
./scripts/start_workers.sh --env-file .env
./scripts/start_scrapers.sh --env-file .env --show-stats
./scripts/start_queue_watchdog.sh --env-file .env
```

### Environment Variables

| Variable | Required | Description |
|----------|:--------:|-------------|
| `DATABASE_URL` | Yes | SQLAlchemy connection URL. Local dev uses native PostgreSQL on `127.0.0.1:5432`; Docker uses `.env.docker.local`. |
| `PORT` | | API port inside/outside the container (default `8000`) |
| `JWT_SECRET_KEY` | Yes | Token signing key — generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `ADMIN_PASSWORD` | Yes | Admin panel access |
| `ANTHROPIC_API_KEY` | | Summarization, chat agents |
| `OPENAI_API_KEY` | | Summarization, deep research |
| `GOOGLE_API_KEY` | | Image generation (Gemini) |
| `EXA_API_KEY` | | Web search in chat |

Docker-only env templates remain in `.env.docker.example` and `.env.docker.local`.

<br>

## iOS App

```bash
open client/newsly/newsly.xcodeproj
```

Build and run on a simulator or device. The app connects to `http://127.0.0.1:8000` by default. Features include:

- **Apple Sign In** authentication
- **Share extension** — save content from any app
- **Integrated chat** — converse with your knowledge base
- **Markdown sync** — export knowledge locally

<br>

## Development

```bash
# Run tests
pytest tests/ -v

# Lint & format
ruff check .
ruff format .

# Create a new migration
alembic revision -m "describe your change"

# Apply migrations
alembic upgrade head
```

### Project Structure

```
app/
├── routers/           # API endpoints
├── commands/          # Write operations (CQRS)
├── queries/           # Read operations (CQRS)
├── repositories/      # Data access layer
├── services/          # Business logic & integrations
├── pipeline/          # Task queue workers
├── processing_strategies/  # Content extraction
├── scraping/          # Feed & site scrapers
├── models/            # SQLAlchemy ORM models
└── core/              # Settings, DB, auth
client/
└── newsly/            # SwiftUI iOS app + Share Extension
```

### Deployment

Production deploys are handled via GitHub Actions with Docker image build + RackNerd container rollout through [`.github/workflows/docker-racknerd-deploy.yml`](.github/workflows/docker-racknerd-deploy.yml). The server runs the repo `docker-compose.yml` and a single `newsly` container.

<br>

## Tech Stack

| Layer | Technologies |
|-------|-------------|
| **Backend** | Python 3.13, FastAPI, SQLAlchemy 2, Pydantic v2 |
| **AI/ML** | pydantic-ai, Anthropic Claude, OpenAI, Google Gemini |
| **CLI** | Go, Cobra, `newsly-agent` binary |
| **iOS** | SwiftUI, Apple Sign In, Share Extension |
| **Frontend** | Jinja2 templates, Tailwind CSS v4 |
| **Infrastructure** | SQLite (Postgres-ready), Alembic, uv, GitHub Actions |

<br>

## Documentation

| Resource | Description |
|----------|-------------|
| **[Architecture](docs/architecture.md)** | System design, database schema, API reference, worker pipeline |
| **[CLAUDE.md](CLAUDE.md)** | Development conventions, coding rules, project guidelines |
| **[docs/library/](docs/library/)** | Operational, deployment, and integration guides |

<br>

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feat/amazing-feature`)
3. Make your changes and add tests
4. Run `ruff check . && ruff format . && pytest tests/ -v`
5. Commit and push
6. Open a Pull Request

<br>

---

<p align="center">
  Built with FastAPI, SwiftUI, and a council of LLMs<br>
  <sub>Made by <a href="https://github.com/willemave">@willemave</a></sub>
</p>
