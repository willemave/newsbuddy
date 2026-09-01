# iOS Onboarding

Newsly onboarding creates a durable, server-owned source configuration without
blocking the UI on initial ingestion. The iOS app offers voice personalization
or an explicit non-personalized path, then lets the user choose suggested
sources and manual aggregators before completion.

## User flow

1. Apple authentication creates the user only when needed.
2. The user chooses **Personalize with voice** or **Skip personalization**.
3. Voice personalization submits the final transcript and starts a durable
   discovery run.
4. The app polls that run until persisted suggestions or a terminal failure are
   available.
5. The user confirms server-owned feed, podcast, and subreddit suggestions and
   may choose supported aggregators.
6. Completion persists the selections and queues initial ingestion and Briefing
   work in one transaction.
7. The one-time tutorial is shown after onboarding until its completion flag is
   persisted.

The non-personalized path sends no discovery run or suggestion IDs and continues
to the manual choices.

## Durable discovery

`POST /api/onboarding/audio-discover` accepts a transcript and optional locale.
The server builds the discovery plan outside a database transaction, then
creates the run and enqueues `onboarding_discover` atomically. Its response
contains:

- `run_id`
- `run_status`
- optional `topic_summary`
- `inferred_topics`
- per-lane status

`GET /api/onboarding/discovery-status?run_id=<id>` returns the persisted run
state, lanes, suggestions, and safe error message. The lookup is scoped to the
authenticated user; clients cannot inspect or complete another user's run.

The iOS app retains the run ID while polling. Reaching the client polling limit
does not cancel durable work: the user can continue waiting, retry
personalization, or take the explicit non-personalized path.

A failed audio discovery surfaces its durable failure state.
`/api/onboarding/fast-discover` returns empty suggestion lists when its
provider step fails.

## Completion contract

`POST /api/onboarding/complete` accepts:

- `discovery_run_id`, or `null` for the non-personalized path;
- `selected_suggestion_ids`, which must belong to that completed run;
- supported manual aggregator selections;
- an optional X username.

The client sends only IDs. The server resolves persisted proposals, validates
selected feed URLs, rejects unfinished, foreign, stale, or cross-run selections,
and normalizes manual configuration.

Selection persistence and task creation share one transaction. Depending on the
resulting configuration, completion queues feed backfill, source scraping, feed
discovery, generated images, and an append-mode Briefing refresh. The response
is the canonical `OnboardingCompleteResponse`:

```json
{
  "status": "queued",
  "task_id": null,
  "inbox_count_estimate": 100,
  "configured_source_count": 0,
  "longform_status": "loading",
  "has_completed_onboarding": true,
  "has_completed_new_user_tutorial": false
}
```

`task_id` is nullable because the selected configuration may not require one
primary setup task. The queued Briefing refresh is independent of that field.
Clients should use the response fields rather than infer completion from local
selection state.

## Tutorial completion

`POST /api/onboarding/tutorial-complete` persists the authenticated user's
one-time tutorial flag and returns:

```json
{"has_completed_new_user_tutorial": true}
```

## Sources of truth

- Public request and response shapes: the Rust Utoipa OpenAPI document and
  `newsly-contracts`.
- Durable run, suggestion, selection, and task state: PostgreSQL through
  `newsly-db` and the queue kernel.
- iOS orchestration: `OnboardingViewModel` and `OnboardingService`.
- Behavioral reliability rules: `docs/laws/processing-and-reliability.md`.
