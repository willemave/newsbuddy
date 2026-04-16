# Fast News Onboarding Selection Plan

**Opened:** 2026-04-16  
**Status:** Proposed  
**Scope:** backend onboarding + news visibility + iOS onboarding/discovery personalization  
**Primary goal:** let users choose shared global fast-news feeds during onboarding without introducing per-user scraping

---

## Summary

Hacker News and Techmeme should remain globally ingested sources. Crawl, clustering, summarization, and discussion fetch should still happen once per source for all users. The new work is only a per-user visibility layer: which globally ingested fast-news platforms a user wants to see.

The existing seam is still the right one:

- `NewsItem.platform` already distinguishes `hackernews` and `techmeme`
- `UserScraperConfig` already stores per-user source selections
- news visibility is already centralized enough to gate these items at query time

The main revisions from the earlier draft are:

- use the real feed URLs for preference records
- treat fast-news selection as shared-source visibility, not a new per-user scraping workflow
- preserve backward compatibility for older clients with a tri-state request field
- update every news visibility query path, not only `build_visible_news_item_filter()`
- avoid accidentally turning fast-news into a generic user-managed scraper type in the public `/api/scrapers` surface unless explicitly desired later

---

## Goals

- Let a user opt in or out of Hacker News and Techmeme during onboarding.
- Keep Hacker News and Techmeme as shared global scrapers.
- Persist user preference using durable per-user records keyed by the real feed URLs.
- Make adding another fast-news source later a config-and-filter change, not a scraper-architecture change.
- Keep old clients and pre-existing users on current behavior until they explicitly save fast-news preferences.

## Non-Goals

- No per-user Hacker News or Techmeme scraping.
- No change to the existing Hacker News or Techmeme ingestion pipeline in this pass.
- No general redesign of `UserScraperConfig`.
- No broad scraper-management UI expansion in settings unless separately planned.

---

## Product Behavior

### New users

- During onboarding, the app shows a `FAST NEWS` section above newsletters/podcasts/reddit.
- Available fast-news feeds are preselected by default.
- The completion payload sends the selected fast-news keys.
- Backend stores per-user preference rows for the selected feeds.
- News feed and news search only show global fast-news items for the selected platforms.

### Existing users

- Existing users have no fast-news preference rows initially.
- Absence of fast-news preference rows means legacy behavior: show all global non-Reddit fast news.
- A migration may backfill explicit rows for current active users, but correctness does not depend on it if the fallback remains in place.

### Explicit opt-out

- If a client sends an explicit empty fast-news selection, the user sees no fast-news items.
- This must be distinguishable from “older client did not send the field”.

---

## Canonical Source Identity

Fast-news preference rows should use stable source identifiers based on the real feed URLs:

- Hacker News: canonical feed URL `https://news.ycombinator.com/rss`
- Techmeme: canonical feed URL `https://www.techmeme.com/feed.xml`

These URLs should be stored in `UserScraperConfig.feed_url` and copied into `config.feed_url`.

Why this matters:

- it keeps uniqueness on `(user_id, scraper_type, feed_url)` correct
- it makes migration inserts idempotent
- it avoids synthetic placeholder URLs
- it leaves room for future fast-news sources that already have real feed endpoints

---

## Data Model Strategy

Use `UserScraperConfig` as the persistence layer for fast-news preferences, but do not treat fast-news as a normal feed-subscription feature in public APIs by default.

### Recommended approach

- Add internal constants for fast-news source types, for example:
  - `FAST_NEWS_SCRAPER_TYPES = {"hackernews", "techmeme"}`
- Add an internal registry loader that returns:
  - `key`
  - `scraper_type`
  - `display_name`
  - `description`
  - `icon`
  - `feed_url`
  - `enabled`
- Add internal creation/upsert helpers for fast-news preference rows.
- Keep public subscribe/create endpoints scoped to the current user-managed feed types unless product explicitly wants fast-news to appear there too.

### Why not just widen the public scraper models

Blindly adding `hackernews` and `techmeme` to public `ALLOWED_SCRAPER_TYPES` and the request literals would leak these internal preference rows into:

- `/api/scrapers`
- subscribe-to-feed flows
- scraper stats assumptions built around normal feed content

That is unnecessary for onboarding selection and increases blast radius.

---

## API Contract

### New endpoint

`GET /api/onboarding/fast-news-feeds`

Response shape:

- `feeds: [FastNewsFeedOption]`
- each option includes:
  - `key`
  - `display_name`
  - `description`
  - `icon`
  - `enabled`

### Onboarding completion request

Add this field to `OnboardingCompleteRequest`:

- `selected_fast_news_feeds: list[str] | None = None`

Semantics:

- `None`: client did not participate in fast-news selection; preserve legacy/default-all behavior
- non-empty list: explicit selected set
- empty list: explicit opt-out of all fast-news feeds

This is the key backward-compatibility requirement. `[]` cannot be the default.

### Agent and secondary onboarding flows

Any code path that constructs `OnboardingCompleteRequest` must either:

- omit the field and rely on legacy/default behavior, or
- explicitly populate it

That includes:

- first-run iOS onboarding
- discovery personalization sheet
- agent onboarding completion command

---

## Backend Design

### 1. Fast-news registry

Add a small internal registry loader in `app/services/onboarding.py` or a dedicated helper module.

Recommended shape:

- source metadata lives in `config/fastnews.yml`
- the registry is the single source of truth for:
  - key
  - scraper type
  - display name
  - description
  - icon
  - enabled state
  - real feed URL

Example registry payload:

```yaml
feeds:
  - key: hackernews
    scraper_type: hackernews
    display_name: Hacker News
    description: Top stories from the tech community
    icon: flame
    enabled: true
    feed_url: https://news.ycombinator.com/rss
  - key: techmeme
    scraper_type: techmeme
    display_name: Techmeme
    description: The day in tech in one fast stream
    icon: newspaper
    enabled: true
    feed_url: https://www.techmeme.com/feed.xml
```

Implementation note:

- `config/fastnews.yml` should be the authoritative product registry for onboarding-visible fast-news feeds
- fast-news scraper implementations should be updated to read their canonical feed settings from this registry
- once scraper loaders have migrated, the old per-source fast-news config files should be deleted rather than left as stale duplicates

### 2. Onboarding completion persistence

Add a helper such as `_create_fast_news_configs(db, user_id, selected_keys)` that:

- resolves keys to enabled registry entries
- inserts or reactivates `UserScraperConfig` rows
- deactivates previously selected fast-news rows when the client sends an explicit set

Behavior by request state:

- `selected_fast_news_feeds is None`
  - do nothing
  - user stays on fallback behavior unless migrated/backfilled
- explicit list present
  - make DB rows match the selected set exactly for fast-news types

This exact-set sync is cleaner than “insert selected rows only”, because it supports deselection after resume/retry and keeps the model honest.

### 3. Visibility filter

Keep `build_visible_news_item_filter()` as the primary gate for list/detail/count paths in `app/services/news_feed.py`, but change the rule:

- if the user has explicit fast-news preference rows:
  - allow global non-Reddit items only when:
    - they are not fast-news platforms, or
    - their `platform` is in the selected fast-news set
- if the user has no fast-news preference rows:
  - preserve current behavior for global non-Reddit items

Still keep user-scoped news visible through the existing user clause.

### 4. Other visibility query paths

Update any separate news visibility logic to share the same rule. At minimum:

- `app/repositories/search_repository.py`

Do not ship the feature with list/detail/count updated but search still showing hidden fast-news items.

### 5. Public scraper APIs

For this pass, keep fast-news configs out of the generic user-managed scraper UX unless explicitly required. Options:

- do not add fast-news types to public create/subscribe request literals
- or filter fast-news rows out of `/api/scrapers` by default

The first option is cleaner for this feature.

---

## iOS Design

### 1. Models and service

Add:

- `FastNewsFeedOption`
- `FastNewsFeedsResponse`
- `selectedFastNewsFeeds: [String]?` on `OnboardingCompleteRequest`

The client should preserve the `nil` versus `[]` distinction.

### 2. First-run onboarding

Update:

- `OnboardingService`
- `APIEndpoints`
- `OnboardingViewModel`
- `OnboardingFlowView`
- `OnboardingProgressSnapshot`

Behavior:

- fetch fast-news options before or when entering suggestions
- preselect all enabled fast-news options
- persist selection in the progress snapshot
- include the explicit selected set in completion

### 3. Discovery personalization sheet

Also update:

- `DiscoveryPersonalizeViewModel`
- `DiscoveryPersonalizeSheet`

Reason:

- it uses the same completion payload
- without parity, the app would have two onboarding-like paths with different fast-news behavior

If product wants the sheet to stay simpler, make that an explicit decision and keep `selected_fast_news_feeds` as `nil` there.

---

## Migration and Rollout

### Optional but recommended migration

Add an Alembic migration that inserts active fast-news preference rows for active existing users using the real feed URLs:

- `(user_id, scraper_type='hackernews', feed_url='https://news.ycombinator.com/rss')`
- `(user_id, scraper_type='techmeme', feed_url='https://www.techmeme.com/feed.xml')`

Use `INSERT ... ON CONFLICT DO NOTHING` against `user_scraper_configs`.

### Rollout safety

The filter change is safe before or after the migration if and only if:

- “no fast-news rows” still means legacy visibility

That fallback should remain until we intentionally decide every user must have explicit fast-news preferences.

---

## Implementation Order

1. Add the internal fast-news registry and request/response models.
2. Update fast-news scraper config loading to use `config/fastnews.yml`.
3. Remove old per-source fast-news config files after loader migration.
4. Add backend onboarding helpers to sync fast-news preference rows.
5. Add the `GET /api/onboarding/fast-news-feeds` endpoint.
6. Update news visibility in `news_feed.py`.
7. Update duplicate visibility logic in `search_repository.py`.
8. Add iOS model/service/view-model/view changes.
9. Add discovery personalization parity or explicitly leave it legacy.
10. Add migration/backfill.
11. Add tests and run verification.

---

## Verification

### Backend

- `ruff check app/models/api/common.py app/services/onboarding.py app/services/news_feed.py app/repositories/search_repository.py app/routers/api/onboarding.py app/scraping/techmeme_unified.py`
- `pytest tests/routers/test_onboarding.py tests/routers/test_api_news.py tests/routers/api/test_content_stats.py -v`
- add focused tests for:
  - explicit selected fast-news set
  - explicit empty fast-news set
  - omitted fast-news field
  - news search honoring the same visibility rules
  - scraper config loading from `config/fastnews.yml`

### Migration

- `alembic upgrade head`
- verify rows in `user_scraper_configs`
- verify re-running the migration is idempotent

### iOS

- build the app in simulator
- complete first-run onboarding with:
  - both feeds selected
  - only Hacker News selected
  - neither selected
- resume onboarding from saved progress and confirm selection persistence
- run discovery personalization and confirm the intended fast-news behavior there

### Functional checks

- old client or omitted field: both fast-news feeds remain visible
- explicit `["hackernews"]`: only Hacker News global items visible
- explicit `[]`: no Hacker News or Techmeme items visible
- user-scoped news still remains visible
- news search results match feed visibility

---

## Open Decisions

1. Should fast-news preferences appear later in settings or the generic scraper-management UI?
2. Should discovery personalization expose fast-news selection now, or intentionally stay legacy/default-all for a simpler sheet?

---

## Recommended Decision Set

- Use real feed URLs for both sources.
- Keep global ingestion unchanged.
- Use `selected_fast_news_feeds: list[str] | None`.
- Sync fast-news preference rows as an exact selected set when the field is present.
- Update all news visibility paths, including search.
- Keep fast-news out of generic public scraper-management APIs in this pass.
