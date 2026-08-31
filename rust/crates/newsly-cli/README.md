# `newsbuddy` Rust CLI

`newsbuddy` is the authenticated user CLI for the Newsly API. It manages
content, News, sources, search, onboarding, jobs, and a local Markdown library.
It is an HTTP client and never connects directly to PostgreSQL. Operational
health, ownership, usage, and repair commands belong to the separate
`newsly-admin` binary.

The Rust implementation preserves the existing binary name, commands, config
files and environment aliases, JSON/text output contract, QR login, and library
manifest format.

## Install from source

From the repository root:

```bash
cargo install --locked --path rust/crates/newsly-cli
newsbuddy version
```

For development without installation:

```bash
cargo run --manifest-path rust/Cargo.toml \
  -p newsly-cli --bin newsbuddy -- version
```

The Homebrew formula is maintained in the external `willemave/newsbuddy` tap.
Updating that formula to package the Rust binary is separate from this source
cutover. Until the tap publishes the Rust build, install from source.

## Configuration and login

The default config file is:

```text
~/.config/newsbuddy/config.json
```

It contains `server_url`, `api_key`, and `library_root`. Resolution precedence
is command-line flag, environment variable, config file, then the default
library path. The default library path is
`~/.local/share/newsbuddy/library`. Config files and downloaded library files
are written with owner-only permissions.

Set the server and link the CLI to the iOS app:

```bash
newsbuddy config set server https://news.example.com
newsbuddy auth login
newsbuddy config show
```

`auth login` starts an unauthenticated link session, prints a QR code and
approval URL to stderr, waits for app approval, and stores the returned API key.
The following overrides remain supported:

- config path: `--config`, `NEWSBUDDY_CONFIG`, `NEWSBUDDY_CONFIG_PATH`,
  `NEWSLY_AGENT_CONFIG`, or `NEWSLY_AGENT_CONFIG_PATH`;
- server: `--server`, `NEWSBUDDY_SERVER`, or `NEWSLY_AGENT_SERVER`;
- API key: `--api-key`, `NEWSBUDDY_API_KEY`, or `NEWSLY_AGENT_API_KEY`;
- output: `--output json|text`, with `--json` as the JSON shortcut;
- HTTP deadline: `--timeout`, which defaults to `30s`.

The legacy `NEWSLY_AGENT_*` names are compatibility aliases, not separate
configuration authorities.

## Commands

```bash
# Content and submission status
newsbuddy content list --read-filter unread --limit 25
newsbuddy content get 42
newsbuddy content submit https://example.com/article --wait
newsbuddy content summarize https://example.com/article --wait
newsbuddy content submissions list --limit 25

# Short-form News
newsbuddy news list --read-filter unread
newsbuddy news get 4821
newsbuddy news convert 4821
newsbuddy news mark-read 4821 4822

# Sources and provider-backed search
newsbuddy sources list --type atom
newsbuddy sources add https://example.com/feed.xml --feed-type atom
newsbuddy search "transformer architectures" --limit 10 --include-podcasts

# Onboarding and jobs
newsbuddy onboarding start --brief "Rust and database systems" --wait
newsbuddy onboarding status 17
newsbuddy onboarding complete 17 --accept-all
newsbuddy jobs get 1201
newsbuddy jobs wait 1201

# Local Markdown library
newsbuddy config set library-root ~/newsbuddy-library
newsbuddy library sync --include-source

# Shell integration
newsbuddy completion zsh
newsbuddy version
```

Supported source types are `atom`, `substack`, and `podcast_rss`. Submit and
onboarding wait operations accept `--wait-interval` and `--wait-timeout`.
`content summarize` uses the same submission route as `content submit` and also
saves the result to Knowledge and marks it read.

## Output contract

Pretty JSON is the default. Successful operations use a stable envelope:

```json
{
  "command": "content.submit",
  "ok": true,
  "data": {
    "content_id": 42,
    "task_id": 1201
  }
}
```

Waited operations may add a `job` field. Failures use `ok: false`, the config
path, and the canonical API error fields when the server supplied them:

```json
{
  "command": "sources.add",
  "ok": false,
  "error": {
    "message": "already subscribed",
    "status_code": 409,
    "code": "already_exists",
    "details": {},
    "retryable": false,
    "request_id": "request-id"
  },
  "config_path": "/Users/example/.config/newsbuddy/config.json"
}
```

`--output text` keeps the same command, status, data, job, and error content in
a terminal-oriented layout. Operational and API failures exit with status 1.

## Library sync safety

`library sync` treats the remote manifest as desired state while deleting only
files tracked by the previous local manifest. It verifies SHA-256 before
publication, repairs missing or corrupt files, writes files atomically, rejects
paths outside the root and writes through symlinks, and refuses to prune every
tracked file from an empty remote manifest unless `--allow-prune-all` is set.
The canonical local manifest remains `.newsbuddy-manifest.json`; the retired
`.newsly-agent-manifest.json` name is read only for migration.

## Development

From the repository root:

```bash
cargo fmt --manifest-path rust/Cargo.toml --all -- --check
cargo clippy --manifest-path rust/Cargo.toml \
  -p newsly-cli --all-targets -- -D warnings
cargo test --manifest-path rust/Cargo.toml -p newsly-cli
```

The CLI imports outbound request and error contracts from `newsly-contracts`.
Ordinary successful response bodies remain `serde_json::Value` at the transport
boundary so a newly added server enum value does not break an older CLI.
