# Newsly Laws

These documents define the product behavior that must survive refactors. They focus on outcomes and invariants while architecture and coding documents own implementation details.

Keep each area between 10 and 20 laws. Consolidate overlapping rules before adding another one. Details that depend on the current implementation belong in architecture or coding documentation.

A behavior change updates its law and tests in the same change. A bug fix restores the law unless the intended behavior has changed. When code and a law disagree, call out the mismatch and resolve it.

Read the laws first, then use `docs/architecture.md` for system design, the coding guides for implementation conventions, `docs/library/` for operations, and initiatives or plans for history.

- [Accounts and onboarding](accounts-and-onboarding.md)
- [Content and reading](content-and-reading.md)
- [Briefing](briefing.md)
- [Knowledge and Learning](knowledge-and-learning.md)
- [Chat](chat.md)
- [Sharing and sources](sharing-and-sources.md)
- [Audio and voice](audio-and-voice.md)
- [Processing and reliability](processing-and-reliability.md)
