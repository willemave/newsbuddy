# Content and Reading Laws

C1. Every incoming URL is validated, canonicalized, and deduplicated before new product state is created.

C2. Repeating a submission reuses existing content and active work unless new user input requires additional processing.

C3. Unsupported or unsafe URL schemes fail without creating content, subscriptions, or jobs.

C4. A user may read an item only when it is in that user's visible inbox, saved library, owned source stream, or explicitly shared global stream.

C5. Short-form news and long-form content keep distinct identities even when they refer to the same story.

C6. A readable summary is the long-form usability boundary; missing optional artwork must not hide otherwise readable content.

C7. Fast Reads prefer canonical titles and publication time, suppress duplicate members, and never expose another user's scoped items.

C8. Converting a visible Fast Read to an article reuses available source text, preserves attribution, and saves the resulting article to Knowledge.

C9. Read state and Knowledge state are per-user, independent, and idempotent unless an explicit composite action changes both.

C10. Saving an item keeps its detail and body accessible after it leaves the inbox.

C11. Recently Read reflects actual per-user read events, not global content activity.

C12. Search returns only user-visible material and degrades gracefully when an optional external search source fails.

C13. Discussions, related links, images, and other enrichment may improve an item but never redefine its canonical identity.

C14. Processing and failure states are truthful: incomplete work is never presented as completed content.

C15. Every displayed item retains enough canonical URL, platform, author, and source metadata to trace it back to its origin.
