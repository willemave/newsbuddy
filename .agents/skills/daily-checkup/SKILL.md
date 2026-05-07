---
name: daily-checkup
description: "Run a daily production checkup for Newsly using the admin CLI: inspect recent logs, recent exceptions, LLM usage, and cost stats, then produce a concise set of suggested fixes."
---

# Daily Checkup

Use this skill when the user asks for a daily checkup, production sweep, morning review, or a quick operational summary of the deployed Newsly system.

## Goal
- Inspect recent remote production signals with the `admin` CLI.
- Include a short LLM usage and cost readout for the same window.
- Summarize what looks healthy, noisy, or broken.
- End with concrete suggested fixes, ordered by severity.

## Fast Rules
- Use the `admin` CLI, not ad hoc SSH, unless the CLI cannot answer the question.
- Default window: last 24 hours.
- Prefer concise findings over raw output dumps.
- Do not apply fixes unless the user explicitly asks.
- If one command fails, report that clearly and continue with the others when possible.

## Time Window
Compute a UTC `since` timestamp for the last 24 hours:

```bash
SINCE="$(uv run python - <<'PY'
from datetime import UTC, datetime, timedelta
print((datetime.now(UTC) - timedelta(hours=24)).strftime('%Y-%m-%dT%H:%M:%SZ'))
PY
)"
```

## Workflow

### 1) Recent logs
Start with the live container stream:

```bash
uv run admin logs tail --limit 200
```

Look for:
- repeated failures or restart loops
- queue backlogs or stuck worker noise
- auth, DB, websocket, or provider errors
- sudden spikes in one subsystem

### 2) Recent exceptions
Pull structured exceptions for the same window:

```bash
uv run admin logs exceptions --since "$SINCE" --limit 200
```

If one noisy component dominates the top of the list, de-bias before concluding the sweep:

```bash
uv run admin logs exceptions --since "$SINCE" --limit 200
uv run admin logs search --source errors --query "OperationalError" --since "$SINCE" --limit 50
uv run admin logs search --source errors --query "recovery mode" --since "$SINCE" --limit 50
```

If needed, narrow further:

```bash
uv run admin logs search --source errors --query timeout --since "$SINCE" --limit 50
uv run admin logs search --source errors --query ElevenLabs --since "$SINCE" --limit 50
```

Focus on:
- dominant `component/operation` pairs
- repeated identical failures
- new exception types
- errors tied to one feature or external dependency
- whether one recurring error pattern is hiding older but higher-severity incidents earlier in the window

### 3) Recent LLM usage and cost stats
Inspect usage totals for the same window and include the stats in the final answer:

```bash
uv run admin usage summary --since "$SINCE" --group-by feature
uv run admin usage summary --since "$SINCE" --group-by model
uv run admin usage summary --since "$SINCE" --group-by provider
```

If a feature, provider, or model dominates cost or tokens, add an operation or source breakdown:

```bash
uv run admin usage summary --since "$SINCE" --group-by operation
uv run admin usage summary --since "$SINCE" --group-by source
```

Use these stats to report:
- total calls, tokens, requests/resources, and estimated cost
- top cost driver by feature, model, and provider
- whether one feature/provider/model is dominating spend or token volume
- whether usage dropped unexpectedly to zero for active features
- whether retries or provider errors appear to be amplifying usage

Use this to spot:
- unusual cost spikes
- one feature dominating calls or tokens
- model/provider drift
- usage dropping unexpectedly to zero

## Output Shape
Respond in 4 short sections:

### Health
- what looks normal
- anything worth watching

### LLM Usage
- report the window totals: calls, tokens, requests/resources, and estimated cost
- name the top feature, model, and provider by cost or tokens
- call out cost spikes, zero-usage surprises, model/provider drift, or retry amplification
- if usage checks fail, say which command failed and keep the rest of the checkup moving

### Findings
- highest-severity issue first
- include the signal that proves it
- mention missing or failed checks explicitly

### Suggested Fixes
- give 1-5 concrete next actions
- tie each action to an observed issue
- prefer specific CLI follow-ups such as:

```bash
uv run admin health snapshot
uv run admin db query --sql 'select id, task_type, status, content_id from processing_tasks order by id desc limit 20'
uv run admin fix requeue-stale --hours 4
uv run admin fix reset-content --hours 24
```

## Suggested Fix Heuristics

### Logs show repeated worker backlog or stale queue noise
Suggested fixes:
- inspect task state with `admin db query`
- preview `uv run admin fix requeue-stale --hours 4`
- if approved, apply the requeue fix

### Exceptions cluster around one feature
Suggested fixes:
- isolate the failing feature with `admin logs search`
- inspect recent related DB rows
- disable or reduce the failing path only if the user asks

### LLM usage spikes sharply
Suggested fixes:
- compare `feature`, `model`, `provider`, `operation`, and `source` summaries
- identify the feature driving cost
- inspect recent exceptions to see whether retries amplified usage

### LLM usage drops unexpectedly
Suggested fixes:
- check recent logs for provider/auth failures
- check exceptions for a shared upstream dependency
- verify the feature still emits normal task flow

### Provider-specific errors dominate
Suggested fixes:
- confirm whether the issue is isolated to one provider
- recommend fallback or retry policy review
- inspect credential/config state only if the user asks for remediation

## Watchouts
- `admin logs tail` defaults to Docker and is the best first check.
- Global CLI flags must come before the subcommand, for example:

```bash
uv run admin --output json health snapshot
```

- Keep the final write-up short. The point is triage, not a raw transcript.
