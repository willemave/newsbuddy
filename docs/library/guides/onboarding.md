# Agentic First Onboarding (iOS)

## Overview
Newsly onboarding is a **three-phase flow**:
1) **Create User** (Apple login) — create user only if not already in DB.
2) **Onboarding / Personalization** — build profile, recommend sources, capture selections.
3) **New User Tutorial** — one-time walkthrough modal; completion stored in DB.

Goal: quickly select the right set of sources for the feed and content inbox, then enqueue crawlers **asynchronously after completion** (do not block UI).

---

## Goals
- Personalized feed + inbox in the first session.
- Discovery completes in **≤45s** (ideal ≤30s).
- Clear interstitials between steps.
- User can skip personalization and accept defaults.

## Non‑Goals
- Web onboarding.
- Blocking on crawler setup before showing inbox.

---

## UX Flow

### Screen 0 — “What is Newsly?”
Single explainer screen post‑login.
- CTA: Continue

### Screen 1 — Personalize or Defaults
- “Build a personalized feed in ~30–45 seconds or start with defaults.”
- Options: **Personalize my feed** / **Use defaults**

### Screen 2 — Voice discovery (personalized path)
- Speech-first capture with live transcript.
- The server creates a durable discovery run and owns its inferred profile and search lanes.
- Interstitial: “Building your profile…”

### Screen 3 — Recommended Sources (pods/substacks)
- Polls the durable discovery run and presents persisted suggestions with stable IDs.
- User selects from the server-owned proposals.
- CTA: Continue

### Screen 4 — Optional Subreddits
- Suggestions + optional search
- CTA: Finish
- Interstitial: “Curating your inbox…”

### Completion State
- Show “100+ unread news articles” immediately (already populated).
- Long‑form content shows “Loading…”
- Trigger async crawler setup in background.

### Tutorial Modal (one‑time)
- Show after first home load post‑onboarding if not completed.
- Explains:
  - Read articles
  - Share an LLM summary
  - Chat / dig deeper
  - Join discussion (Reddit / HN)

---

## System Design

### Three Phases (Backend)
1) **Create User**: via `/auth/apple` (only if user doesn’t exist).
2) **Onboarding**: durable audio discovery + run-owned selections, then async crawler setup.
3) **Tutorial**: persisted completion flag so it only shows once.

### Fast vs Async Discovery
- **fast_discover (sync)**
  - Uses Exa search + LLM to identify the right feeds quickly.
  - Tight limits (≤10–15s): fewer queries, fewer results, smaller prompts.
  - Output used to populate Screen 3 immediately.
  - If timeout/failure → fallback to defaults.

- **feed discovery (async)**
  - Runs as a separate post-completion workflow when no active discovery task exists.
  - Expands coverage without replaying the completed onboarding discovery run.

### Source Inputs
- Exa AI search results based on name + handle.
- Existing curated config lists (Substack, Podcast, Atom, Reddit) merged in.

---

## Data Model

### User
- `has_completed_new_user_tutorial: bool` (default false)

### Onboarding Selections
- `discovery_run_id` identifies the authenticated user's completed personalized run.
- `selected_suggestion_ids` confirms persisted feed, podcast, and subreddit proposals.
- Aggregator choices remain explicit manual product choices.
- The server resolves URLs, titles, subreddit names, and inferred profile context; clients do not echo them.

---

## API (Proposed)

### 1) Build Profile (Sync)
`POST /api/onboarding/profile`

Request:
```json
{
  "first_name": "Ada",
  "interest_topics": ["AI policy", "climate tech"]
}
```

Response:
```json
{
  "profile_summary": "AI researcher and writer focused on ML systems...",
  "inferred_topics": ["machine learning", "AI policy"],
  "candidate_sources": []
}
```

### 2) Fast Discovery (Sync)
`POST /api/onboarding/fast-discover`

Request:
```json
{
  "profile_summary": "...",
  "inferred_topics": ["..."]
}
```

Response:
```json
{
  "recommended_pods": [ ... ],
  "recommended_substacks": [ ... ],
  "recommended_subreddits": [ ... ]
}
```

### 3) Complete Onboarding (Async crawler setup)
`POST /api/onboarding/complete`

Request:
```json
{
  "discovery_run_id": 412,
  "selected_suggestion_ids": [9102, 9104, 9110],
  "selected_aggregators": [
    {"key": "hackernews", "title": "Hacker News", "topics": []}
  ],
  "twitter_username": null
}
```

The non-personalized path sends `discovery_run_id: null` and an empty
`selected_suggestion_ids` list. Completion rejects unfinished, foreign, stale,
duplicate, or cross-run suggestion IDs.

Response:
```json
{
  "status": "queued",
  "feed_id": null,
  "inbox_count_estimate": 100,
  "longform_status": "loading",
  "has_completed_new_user_tutorial": false
}
```

### 4) Tutorial Completion
`POST /api/onboarding/tutorial-complete`

Response:
```json
{ "has_completed_new_user_tutorial": true }
```

---

## Crawler Setup
- Triggered **after onboarding completion** (async job).
- Creates user-specific scraper configs from selections.
- Enqueues scraper run(s) without blocking UI.

---

## Performance & Fallbacks
- Total discovery target ≤45s.
- If Exa or LLM step >20s → prompt to use defaults.
- If discovery fails → return curated defaults immediately.

---

## Acceptance Criteria
- New user created only when not present.
- Profile built from name + handle using Exa + LLM.
- fast_discover returns within 15s or falls back to defaults.
- Completion persists selections and triggers async crawler setup.
- Inbox shows 100+ unread immediately; long‑form shows loading.
- Tutorial modal shows once per user (DB flag).
