# Accounts and Onboarding Laws

A1. Private product data is always scoped to the authenticated user; matching numeric IDs never grant cross-user access.

A2. Apple sign-in is the production identity boundary; debug users are development-only.

A3. Refresh tokens rotate once, and a consumed token cannot mint another session.

A4. Account deletion requires fresh authorization and deactivates the account only after durable cleanup work has been accepted.

A5. API keys, provider tokens, and user-managed model keys are revocable secrets: they are encrypted at rest, never returned in full, and never written to logs.

A6. Device caches and snapshots are keyed by user and are invalidated on sign-out or account change.

A7. Onboarding suggestions are proposals; no source becomes active until the user confirms it.

A8. Completing onboarding validates and persists the full selection atomically, then queues the initial source work.

A9. A failed onboarding dependency must not leave half-applied source selections or pretend completion.

A10. One unavailable source does not erase successful onboarding progress or block the remaining sources.

A11. Onboarding finishes in the real Briefing experience, where first-run progress and usable categories may appear incrementally.

A12. Server-owned product preferences travel with the account; purely visual preferences may remain device-local.
