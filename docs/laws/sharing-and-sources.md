# Sharing and Sources Laws

S1. The Share Extension exposes four outcomes named Add to Briefing, Add to Knowledge, Create Deck, and Chat.

S2. Share acceptance records durable queued work, while final visibility follows completion.

S3. Every shared URL is canonicalized before its outcome runs, and retrying the same outcome cannot create duplicate content or work.

S4. Add to Briefing resolves a continuing publication to a supported feed and an individual article or episode to direct content. Directly verified feeds and items resolve through host actions before model-backed discovery. A valid item may replace failed feed discovery, while an arbitrary homepage cannot.

S5. A subscription controls future fetched items. The shared page requires its own ingestion outcome.

S6. Add to Knowledge saves the submitted item and marks that item read.

S7. Create Deck and Chat use the shared item as source material without creating ordinary unread content. Deck instructions survive queueing and retry without being rewritten.

S8. Generated actions must match the chosen mode, and approval-required actions wait for explicit approval.

S9. Source subscriptions and aggregator selections belong to one user and control future visibility without rewriting history.

S10. Global aggregator items appear only for users who selected that aggregator, and user-scoped sources never enter the global pool.

S11. X connections belong to one user, sync incrementally, preserve provenance, and retain bounded-page continuations across retries, and advance their checkpoint only after the complete range through the previous checkpoint has been ingested.

S12. Disconnecting or invalidating an integration removes usable credentials and reports when reauthorization is required.

S13. Source refresh status distinguishes a successful check with no new items from a fetch, parsing, or persistence failure. Scheduled refresh accepts only unseen entries ahead of the first known item and never walks backward through feed history. Explicit catch-up scans past already known and rejected entries while preserving existing read and archive state.
