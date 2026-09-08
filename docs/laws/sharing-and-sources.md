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

S13. Source refresh status distinguishes a successful check with no new items from a fetch, parsing, or persistence failure. Scheduled refresh accepts only unseen entries ahead of the first known item and never walks backward through feed history on repeated polls. An unchanged feed creates no further memberships or paid work. Intake is bounded even when the known boundary is missing; entries beyond the admitted head require explicit catch-up. Explicit catch-up scans past already known and rejected entries while preserving existing read, saved, and archive state.

S14. Feed status shows article and podcast sources with their last successful item completion and distinct running and queued item counts. A feed check is not an item completion. Successful processing totals and feed history include the user's read and archived items and count each canonical item once. History is scoped to the user's sources and memberships; ambiguous attribution must not assign an item to an arbitrary feed. The source list contains statistics only; individual items appear after opening a feed. Publication dates remain distinct from processing dates, and unavailable measurements are not fabricated.
