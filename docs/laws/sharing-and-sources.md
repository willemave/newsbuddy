# Sharing and Sources Laws

S1. The Share Extension exposes four outcomes: Add to Briefing, Add to Knowledge, Create Deck, and Chat.

S2. Share acceptance means durable asynchronous work was queued; it does not promise that the final item is already visible.

S3. Every shared URL is canonicalized before the requested outcome is applied.

S4. Add to Briefing resolves a continuing publication to a supported feed and an individual article or episode to direct content.

S5. Feed discovery may fall back to a valid individual item, but it never ingests an arbitrary homepage merely to avoid a clear unsupported result.

S6. Subscribing to a feed affects future fetched items and does not imply that the currently shared page was ingested.

S7. Add to Knowledge saves the item and marks that submitted item read so it does not also enter Briefing as unread.

S8. Create Deck and Chat treat the shared item as source material for their requested outcome, not as an ordinary unread submission.

S9. Retrying the same share outcome is idempotent and cannot create duplicate content, subscriptions, decks, chats, or jobs.

S10. A generated action must match the user's chosen mode; approval-required actions wait for explicit approval before changing product state.

S11. Source subscriptions and aggregator selections are per-user and control future visibility without rewriting historical content.

S12. Global aggregator items appear only for users who selected that aggregator; user-scoped sources such as Reddit never leak into the global pool.

S13. X connections are per-user, sync bookmarks incrementally, retain checkpoint and provenance data, and never consume a success checkpoint on failure.

S14. Disconnecting or invalidating an integration removes usable credentials and reports reauthorization when required.
