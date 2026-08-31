---
name: daily-checkup
description: Run a read-only Newsly production sweep using the Rust operator, container logs, queue failures, and model/vendor usage. Use for daily checkups, morning reviews, or quick deployed-system health reports; do not use it to mutate production.
---

# Daily Checkup

Inspect the last 24 hours by default and return a concise, evidence-backed
health report. This skill is read-only. A request to fix, commit, push, or
deploy is a separate authorization and workflow.

## Runtime boundary

Use the Rust `newsly-admin` binary inside a healthy production application
container so it inherits the deployed database environment. Use the existing
remote/container access mechanism; do not recreate or invoke the retired Python
`admin` package.

For a local checkout, the equivalent prefix is:

```bash
cargo run --manifest-path rust/Cargo.toml --locked -p newsly-admin --
```

Production logs come from the container runtime, not `newsly-admin`. Read the
bounded unified application stream and include the extractor only when an
extraction symptom makes it relevant. Never dump environment variables,
credentials, task payloads, or unrestricted database rows.

## Evidence to collect

Run the Rust operator in JSON mode where possible:

```bash
newsly-admin --output json health snapshot
newsly-admin --output json health queue --window-hours 24 --top-errors-limit 20
newsly-admin --output json tasks failures --window-hours 24 --limit 50
newsly-admin --output json usage summary --window-hours 24 --group-by feature
newsly-admin --output json usage summary --window-hours 24 --group-by model
newsly-admin --output json usage summary --window-hours 24 --group-by provider
```

Add operation or source usage groupings only when they help explain a spike.
Use `newsly-admin db query` only for a narrow follow-up that the health/task
commands cannot answer; it enforces a bounded read-only transaction. If one
signal fails, identify the missing evidence and continue with the others.

In container logs, look for restart loops, repeated worker failures, lease or
queue churn, database/auth failures, and provider-specific clusters. Correlate
log claims with the queue/task snapshots rather than inferring health from logs
alone.

## Report

Return four short sections:

1. **Health** — what is healthy and what needs attention.
2. **Usage** — calls/tokens/resources/cost and the leading feature, model, and
   provider, including suspicious zero usage or retry amplification.
3. **Findings** — highest severity first, with the exact bounded signal.
4. **Suggested fixes** — one to five scoped next actions tied to findings.

Do not apply a suggested fix during a checkup. If remediation is later
authorized, first re-read the decisive signal, make the narrowest in-scope
change, and rerun the smallest check that proves the outcome.
