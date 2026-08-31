# Content and Reading Laws

C1. Every incoming URL is validated, canonicalized, and deduplicated before product state is created. Unsupported or unsafe schemes create nothing.

C2. Repeating a submission reuses existing content and active work unless new user input requires additional processing.

C3. A user may read an item only through their visible inbox, saved library, owned source stream, or an explicitly shared global stream.

C4. Short-form news and long-form content keep distinct identities even when they describe the same story.

C5. A readable summary makes long-form content usable. Missing optional artwork or enrichment cannot hide it.

C6. Fast Reads use canonical titles and publication time, suppress duplicates, and respect user visibility. Converting one to an article preserves attribution and saves the article to Knowledge.

C7. Read state and Knowledge state are per-user, independent, and idempotent unless an explicit action changes both.

C8. Saved items remain readable after they leave the inbox.

C9. Recently Read reflects the user's own read events.

C10. Search returns only user-visible material and remains useful when an optional external source fails.

C11. Discussions, links, images, and other enrichment may improve an item without changing its canonical identity.

C12. Processing state remains truthful, and every displayed item retains enough provenance to trace its origin.

C13. Lifecycle cancellation is not a content failure. A backgrounded read resumes or revalidates when its still-visible route becomes active; readable content remains visible while automatic revalidation is in flight or fails transiently, and a result from a suspended or replaced request generation never publishes.

C14. Submission status has one canonical discriminated result whose kind scopes its fields to content, feed subscription, Learning Deck, or no action. During the installed-client compatibility window, every legacy top-level mirror must agree with that result; mirrors may be removed only after operation and client-version telemetry satisfies the declared retirement window.

C15. An agent-initiated feed subscription—Share Add Feed, a feed-valued Add to Briefing action, or Chat—is applied only after the host has fetched and parsed the exact RSS or Atom URL outside the finalization transaction. Its applied result identifies the active subscription config and the validated feed format; a created or reactivated subscription and its initial backfill become durable atomically, while an invalid feed fails without an applied action. The host derives podcast treatment from parsed audio entries and Substack treatment from the effective host rather than trusting a model label.

C16. Content submission and feed subscription are separate commands. Content submission never mutates a scraper config; legacy requests that set `subscribe_to_feed` are rejected before persistence and callers must use the canonical scraper-subscription or Share Add Feed boundary.
