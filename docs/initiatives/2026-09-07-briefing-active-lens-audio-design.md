# Briefing audio for the active lens

Status: Implemented and validated locally on September 7; uncommitted and undeployed.

This supersedes the program scope, request identity, and source selection in
[Briefing Audio Story Chapters](2026-09-01-briefing-audio-story-chapters-design.md).
The user corrected the intended behavior on September 7: Play should use the active
lens. Existing script adaptation, chapter generation, and background playback remain.

## Behavior

- Play captures the selected lens key when tapped and builds audio from that lens's
  complete eligible unread source set, including segments beyond the loaded page.
- Sources from other lenses never enter that manifest. There is no automatic
  continuation into another lens after the final chapter.
- News chapters curate highlights within the selected lens. Article and podcast
  chapters retain one titled source per chapter within the selected lens.
- Changing the visible lens does not replace an existing playback queue. Pressing
  Play in the newly selected lens explicitly switches to that lens's audio session.
- Player and lock-screen collection labels use the originating lens title, so an
  ongoing queue remains identifiable after navigation.
- Retrying failed audio keeps the original edition and chapter IDs through
  `POST /api/briefing/narrations/{episode_group_id}/retry`. Only failed chapters
  are reset, and their saved sources and generated script checkpoints survive.
- Once the final chapter finishes, the next Play waits for pending completion
  read marks, then requests a fresh unread edition. Pause/resume and explicitly
  selecting a chapter retain the existing edition. A newer playback intent wins
  if it arrives while a fresh-edition request is waiting for read marks.
- A lens with no eligible sources shows an empty state and starts no generation.
- Completing a News chapter marks its planned source window read, including sources
  omitted from the spoken script. Those source keys must all belong to the captured
  lens. Shared canonical read state continues to apply normally.

For example, Play in AI & Society includes only that lens's unread sources. It may
combine related AI stories and omit minor details. Business sources are not added,
and playback ends when the AI & Society queue ends.

## Request and planning

Keep the plural create route and the existing group-status route. Introduce an
explicit adapted-lens request, `{"scope":"lens","lens_key":"<selected key>"}`.
The server validates user ownership and active status and derives the content tier
from the lens. Scope `lens` requires a lens key; existing tier scopes continue to
reject a lens key. New app Play actions send only the adapted-lens request.

The released lens-key-only request retains its existing preauthored narration
behavior. Existing tier scopes remain a compatibility path for installed clients;
the API owns that path until minimum supported versions and route telemetry show
it can be removed. Existing episode groups remain playable.

Planning loads active/degraded segments for the captured lens in canonical order,
revalidates unread eligibility, and snapshots only eligible sources. News retains
whole-segment chapter windows with the existing approximate five-minute input
budget. Its script remains a grounded 500–700-word adaptation of each window.
Article and podcast chapters keep their existing 180–320-word adaptation.

The manifest carries the actual lens key and title. Session caches use that key,
and immutable group identity includes the lens key, ordered chapter inputs, and a
new adaptation version. A cached all-News group cannot satisfy a lens request.
Shared domain metadata resolves preauthored, document, or News script policy;
the repository resolves chapter layout and snapshot identity once per request.
The worker selects News versus document prompts from server-derived lens tier;
the new request must not fall through to legacy verbatim narration.

## Acceptance checks

- With two populated News lenses, Play in either includes only that lens's sources,
  including eligible sources beyond the first page.
- Read, retired, inaccessible, and other-lens sources are excluded. An empty lens
  never falls back to another lens or a tier-wide program.
- Switching lenses during preparation cannot redirect the original request or let
  an older playback intent replace a newer one.
- Merely browsing another lens preserves playback; pressing its Play switches
  queues. Automatic advance stops at the originating lens's final chapter.
- Repeating an unchanged lens request reuses its completed audio. Different lenses
  and old tier-wide groups remain distinct.
- Chapter completion marks only the captured source keys. Generation, pause, and
  chapter skip do not mark sources read.
- Contract compatibility, adapted prompt selection, chapter titles, background
  playback, and lock-screen controls remain covered by focused tests.

## Implementation evidence

The client now sends scope `lens` with the selected lens key, retains per-lens
playback sessions, and identifies the originating lens in player metadata. The
repository plans eligible sources from that lens and persists its tier for script
generation. Version 5 groups remain distinct from existing tier-wide and legacy
preauthored audio. Empty lenses return a specific user-facing explanation.

Validation passed: 89 iOS Simulator tests, 15 focused Rust tests including five
isolated PostgreSQL tests, warning-denied Clippy for affected packages, formatting,
public contract drift, iOS wire boundaries, and whitespace checks. Evidence lives
under `test-results/active-lens-audio/`. The repository-wide architecture guard still
reports the unrelated pre-existing `scraper_configs.rs` size violation. Live provider
audio, physical-device playback, and deployment were not exercised in this change.
