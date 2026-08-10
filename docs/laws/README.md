# Newsly Laws

These documents are the canonical statement of durable product behavior. They say what must remain true, not where code lives or how a feature is implemented.

Use the documentation in this order:

1. `docs/laws/` for product behavior and invariants.
2. `docs/architecture.md` for system boundaries and runtime design.
3. `docs/coding-guidelines*.md` for implementation conventions.
4. `docs/library/` for operational and integration detail.
5. `docs/initiatives/` and `docs/agent-plans/` as historical context, never as current authority.

A behavior change must update its law and tests in the same change. A bug fix should restore the law, not quietly rewrite it. If code and a law disagree, call out the mismatch and decide which one is wrong.

The app has three primary surfaces: Briefing for reading, Knowledge for saved material, and Learning for chats, decks, and narrations. Search, recently read, submissions, processing, sources, and settings are supporting surfaces.

- [Accounts and onboarding](accounts-and-onboarding.md)
- [Content and reading](content-and-reading.md)
- [Briefing](briefing.md)
- [Knowledge and Learning](knowledge-and-learning.md)
- [Chat](chat.md)
- [Sharing and sources](sharing-and-sources.md)
- [Audio and voice](audio-and-voice.md)
- [Processing and reliability](processing-and-reliability.md)
