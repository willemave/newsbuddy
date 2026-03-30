# Newsly

**Your AI-powered knowledge companion.** Newsly pulls in content from across the web, summarizes it with LLMs, generates visual thumbnails, and lets you have real-time voice conversations about anything you've saved — or anything on the internet.

Stop drowning in tabs. Start understanding what matters.


## Why Newsly

- **Smart ingestion** — Drop in any URL or let scrapers pull from Hacker News, Reddit, Substack, Techmeme, podcasts, and Atom feeds. Newsly figures out what it is and does the rest.
- **AI summaries that actually help** — Narrative summaries with key insights, supporting quotes, and takeaways. Not bullet-point slop.
- **Talk to your knowledge** — Deep-dive chat agents with web search dig deeper into any article, corroborate claims, or explore new topics.
- **Live Voice** — Have a real-time spoken conversation with your knowledge base. Ask questions, get answers, interrupt naturally. Backed by ElevenLabs STT/TTS and Claude.
- **Deep Research** — Kick off comprehensive async research sessions that run for minutes, not seconds, with web search and code analysis.
- **Native iOS app** — SwiftUI client with Apple Sign In, share extension, and integrated chat and voice.

## Getting Started

### Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) package manager
- Node.js (for Tailwind CSS build)

### Install & Run

```bash
# Clone and install
git clone https://github.com/willemave/news_app.git
cd news_app
uv sync && source .venv/bin/activate

# Configure
cp .env.example .env
# Edit .env with your API keys (see below)

# Database
alembic upgrade head

# Build CSS
npx @tailwindcss/cli -i ./static/css/styles.css -o ./static/css/app.css

# Start everything
scripts/start_server.sh     # API on :8000
scripts/start_workers.sh    # Background processing
scripts/start_scrapers.sh   # Content scrapers
```

### Environment

Copy `.env.example` and fill in:

| Variable | Required | What it does |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | `sqlite:///./news_app.db` for dev |
| `JWT_SECRET_KEY` | Yes | Auth token signing |
| `ADMIN_PASSWORD` | Yes | Admin panel access |
| `OPENAI_API_KEY` | - | Summarization, deep research |
| `ANTHROPIC_API_KEY` | - | Summarization, voice agent |
| `GOOGLE_API_KEY` | - | Image generation |
| `EXA_API_KEY` | - | Web search in chat |
| `ELEVENLABS_API_KEY` | - | Live voice (STT + TTS) |

### iOS App

```bash
open client/newsly/newsly.xcodeproj
```

Build and run on a simulator or device. The app connects to your local server at `http://127.0.0.1:8000` by default.

### Development

```bash
pytest tests/ -v    # Run tests
ruff check .            # Lint
ruff format .           # Format
alembic revision -m "description"  # New migration
```

### Production Deploys

- Production app deploys are handled by GitHub Actions via [`.github/workflows/bare-metal-deploy.yml`](.github/workflows/bare-metal-deploy.yml).
- Do not use `scripts/deploy/push_app.sh` for production deploys.
- `scripts/deploy/push_envs.sh` is env-sync only when you need to refresh remote secrets outside the normal deploy workflow.

## Built With

Python 3.13 / FastAPI / SQLAlchemy 2 / pydantic-ai / SwiftUI / ElevenLabs / Tailwind CSS v4

## Docs

- [`docs/architecture.md`](docs/architecture.md) — Full system architecture, database schema, and API reference
- [`CLAUDE.md`](CLAUDE.md) — Development conventions and coding rules
