# Share Sheet Action Alignment — Implementation Plan

**Date:** 2026-07-12  
**Status:** Proposed  
**Scope:** iOS Share Extension, Share Action API contracts/workflows, focused tests and documentation

## Outcome

Replace the current collection of overlapping Share Sheet choices with four user-facing actions:

1. **Add to Briefing**
   - If the shared URL represents a source, publication, show, channel, or feed, discover and subscribe to its feed as a new Briefing source.
   - If the shared URL represents an individual article or episode, ingest that content directly so it becomes eligible for the user's Briefing.
2. **Add to Knowledge**
   - Process the individual item, save it to Knowledge, and mark it read so it does not enter Briefing or Long Reads.
3. **Create Deck**
   - Preserve the existing Learning Deck workflow.
4. **Chat**
   - Preserve the existing prompt-and-chat handoff, including saving the processed item to Knowledge.

The Share Sheet should describe user outcomes, while the backend owns URL classification, canonicalization, feed discovery, and the choice between source subscription and direct content ingestion.

## Current Behavior

`client/newsly/ShareExtension/ShareViewController.swift` currently exposes:

- Add content
- Create learning deck
- Add links
- Add feed
- Chat
- A context-dependent Bookmark only toggle

These map to separate Share Action modes and prompt packs:

- `add_content` -> submit one content URL
- `add_links` -> crawl and submit selected links
- `add_feed` -> discover and subscribe to a feed
- `bookmark_only` -> submit, save to Knowledge, and mark read
- `presentation` -> create or rerun a Learning Deck
- `chat` -> submit, save to Knowledge, mark read, and enqueue chat

Direct content already reaches Briefing through the normal pipeline when it finishes as an unread, non-skipped article or podcast with an inbox status. Feed subscription instead creates a continuing source whose future eligible items can enter Briefing.

## Recommended Design

### User-facing action model

Replace `LinkHandlingMode` with four cases:

| Share Sheet label | Description | Backend mode |
|---|---|---|
| Add to Briefing | Add this item, or subscribe to its source, for future Briefings. | `add_to_briefing` |
| Add to Knowledge | Save this item to Knowledge without adding it to Briefing. | `bookmark_only` |
| Create Deck | Turn this source into a Learning Deck. | `presentation` |
| Chat | Save this item and start a chat after it is processed. | `chat` |

Use action-specific submit-button titles: **Add to Briefing**, **Add to Knowledge**, **Create deck**, and **Start chat**.

Remove the Bookmark only toggle. Its behavior becomes the explicit **Add to Knowledge** action.

Remove **Add links** and **Add feed** from the visible Share Sheet. Keep their backend modes during the compatibility window so older app builds and persisted tasks continue to work.

### Add-to-Briefing contract

Add `ADD_TO_BRIEFING = "add_to_briefing"` to the shared `LlmTaskMode` contract and include it in the Share Action API's supported modes.

Model one typed host action rather than exposing feed/content branching to the extension:

```text
mode: add_to_briefing
host action: add_to_briefing
resolved target:
  kind: feed | content
  url: canonical feed or content URL
  title/platform/content_type: optional hints
  rationale: classification and resolution summary
```

The host applicator dispatches through existing services:

- `kind=feed` -> existing content submission with `subscribe_to_feed=true`
- `kind=content` -> existing ordinary content submission

This keeps persistence, deduplication, queueing, retries, and feed resolution in their existing layers. It also gives the action one stable idempotency key based on the resolved target.

### Resolution policy

The `share_action.add_to_briefing` prompt and typed result validation should enforce this order:

1. Canonicalize redirects and tracking URLs.
2. Identify whether the shared URL is an individual item or a continuing source.
3. For a continuing source, discover and validate the best RSS, Atom, podcast, newsletter, channel, or other supported feed.
4. For an individual article or episode, return the canonical item URL as direct content.
5. If feed discovery fails but the submitted page is valid individual content, fall back to direct content.
6. Do not silently ingest an arbitrary homepage as an article merely to avoid a failure. Return a clear unsupported/no-action result when neither a feed nor Briefing-eligible content can be resolved.

The API remains asynchronous and should continue returning the queued Share Action task. The action result should retain enough structured information to report whether Newsly subscribed to a source or added one item, even if the extension initially continues closing after a successful enqueue.

## Implementation Phases

### Phase 1: Add the composite backend workflow

Files:

- `app/models/contracts.py`
- `app/models/api/share_actions.py`
- `app/services/share_action_workflows.py`
- `app/services/share_actions.py`
- `app/prompts/llm_tasks/share_action.add_to_briefing.md` (new)
- generated API/Swift contract artifacts, if the enum is exported there

Work:

1. Add the `add_to_briefing` mode without removing existing modes.
2. Add a discriminated, Pydantic-validated input model for `feed` versus `content` targets.
3. Register one workflow spec and one host applicator for the new mode.
4. Reuse `_submit_content(...)` for both branches, setting `subscribe_to_feed` only for feed targets.
5. Make unsupported/no-action results complete safely without creating content or subscriptions.
6. Add the new prompt pack with the resolution policy above.
7. Preserve existing `add_content`, `add_feed`, `add_links`, `bookmark_only`, `presentation`, and `chat` handling for old clients and already-queued tasks.

### Phase 2: Align the Share Extension UI

Files:

- `client/newsly/ShareExtension/ShareViewController.swift`
- `client/newsly/newsly/Shared/ShareExtensionStyle.swift` only if the reduced layout needs shared styling changes

Work:

1. Replace the five current option cases with the four agreed actions.
2. Make **Add to Briefing** the default.
3. Map the modes to `add_to_briefing`, `bookmark_only`, `presentation`, and `chat`.
4. Remove the Bookmark only toggle and all related visibility/state logic.
5. Keep the chat prompt visible and required only for Chat.
6. Update the heading from link-handling language to outcome language, for example: **What would you like to do with this?**
7. Update submit-button titles per action.
8. Check Dynamic Type, keyboard behavior, VoiceOver order/labels, and compact Share Sheet height after removing one row and the toggle.

### Phase 3: Clarify completion and failure messaging

The extension currently treats `202 Accepted` as success and closes before the resolver finishes. Preserve that fast handoff initially, but align terminology:

- enqueue success: **Adding to Briefing…**, **Saving to Knowledge…**, **Creating deck…**, or **Starting chat…**
- synchronous validation failure: actionable error without closing the extension
- asynchronous resolution failure: visible through the existing task/status surface and logs

Do not claim **Subscribed** or **Added to Briefing** in the extension before the queued task has actually resolved and applied the action.

A later enhancement could poll the Share Action task long enough to display **Source subscribed** versus **Item added**, but that is not required for this refactor.

### Phase 4: Tests and verification

Backend tests:

- `tests/routers/test_api_share_actions.py`
  - accepts `add_to_briefing`
  - rejects invalid modes/target shapes
  - preserves authentication and task ownership behavior
- `tests/services/test_share_actions.py`
  - source URL result applies the feed-subscription branch
  - individual item result applies direct content ingestion
  - feed-discovery fallback applies direct content only for a valid item
  - duplicate feed/content targets remain idempotent
  - no-action/unsupported resolution creates neither a subscription nor inbox content
  - legacy modes still execute
- `tests/services/test_share_action_agent.py`
  - new prompt pack is selected and its result schema is validated

Briefing integration tests:

- confirm direct article/podcast ingestion creates or preserves inbox state
- confirm completed unread direct content triggers the existing Briefing event path
- confirm Add to Knowledge saves and marks read without becoming a Briefing candidate
- confirm subscribing to a feed does not incorrectly add the source page as an article

iOS verification:

- build the Share Extension target
- inspect the four-row layout in an iPhone Simulator at default and accessibility text sizes
- verify each action sends the intended mode
- verify Chat keyboard focus, required-message gating, and submit-button behavior
- verify unauthenticated and network-error presentation
- update or add a stable Share Extension screenshot/AXe assertion if the current test harness can launch the extension deterministically

Suggested commands:

```bash
ruff check app/models/contracts.py app/models/api/share_actions.py app/services/share_action_workflows.py app/services/share_actions.py tests/routers/test_api_share_actions.py tests/services/test_share_actions.py tests/services/test_share_action_agent.py
pytest tests/routers/test_api_share_actions.py tests/services/test_share_actions.py tests/services/test_share_action_agent.py tests/services/briefing/test_events.py -v
client/newsly/scripts/regenerate_api_contracts.sh
```

Run the repository's normal Xcode build/test path for the Share Extension after the backend contract artifacts are regenerated.

## Gotchas and Guardrails

### “Added to Briefing” is asynchronous

Direct content must first be analyzed and summarized. It only enters Briefing after it is completed, unread, non-skipped, classified as an article or podcast, and visible in the user's inbox. The UI must not imply immediate appearance.

### Feed subscription and current-item ingestion are different

Subscribing to a feed affects future fetched items. It does not necessarily add the currently shared page. The resolver must choose one explicit target, and tests must prevent a source homepage from also becoming a bogus content item.

### Briefing does not accept every content type

The current event path admits articles and podcasts. Generic videos, unsupported social posts, homepages, and skipped content may not appear. The new prompt should prefer a supported canonical equivalent where one exists and otherwise return a clear unsupported result. Expanding Briefing eligibility is a separate product change.

### Add to Knowledge means “save and mark read”

The existing `bookmark_only` workflow deliberately saves to Knowledge and marks the item read, keeping it out of Briefing and Long Reads. The new label and description should make that consequence clear.

### Old clients and queued tasks must survive rollout

Do not delete legacy backend modes in the same release. Deploy the backend contract first or in the same compatible release as the extension. Remove dead modes only after the minimum supported app version no longer sends them and no persisted tasks depend on them.

### Existing user-facing features are being removed from this surface

- **Add links** loses multi-link extraction from the Share Sheet.
- The explicit **Add feed** control is replaced by automatic resolution, so users lose the ability to force feed discovery from an ambiguous individual page.
- The Bookmark-only toggle is replaced by the clearer Add to Knowledge action.

If forcing feed discovery remains valuable, expose it later as an advanced override rather than restoring another primary row.

## Rollout Order

1. Land the compatible backend mode, prompt, applicator, and tests.
2. Regenerate shared contracts and verify the existing iOS app still builds.
3. Land the four-action Share Extension UI.
4. Exercise all four actions against local services with workers running.
5. Deploy backend support before distributing the new extension build.
6. Monitor Share Action failures by mode and resolved target kind.
7. Consider removing legacy modes only in a later cleanup release.

## Definition of Done

- The Share Sheet shows exactly four actions: Add to Briefing, Add to Knowledge, Create Deck, and Chat.
- Add to Briefing resolves a source to a feed subscription or an individual item to direct content ingestion.
- Direct content reaches the existing Briefing pipeline without a parallel persistence path.
- Add to Knowledge saves and marks read without entering Briefing.
- Create Deck and Chat retain their current behavior.
- Old app versions and already-queued Share Actions continue to work.
- Focused backend tests, generated-contract checks, and Share Extension build/UI verification pass.

