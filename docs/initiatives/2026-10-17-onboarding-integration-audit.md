# Onboarding and aggregator integration audit — 2026-10-17

The initial read-only audit fetched `origin`, inspected all local branches/worktrees,
and listed every GitHub PR, including merged/closed PRs. Local and remote main both
pointed to `48a21d7e5cce7475570a991ef36c54695502a06b`.

## Already integrated at the audit baseline

| Branch | Tip | Commits absent from main |
| --- | --- | --- |
| `onboarding-discovery-and-loaders` | `e4711f48` | 0 |
| `origin/onboarding-discovery-and-loaders` | `e4711f48` | 0 |
| `codex/onboarding-and-ops-fixes` | `0a309646` | 0 |
| `claude/news-category-performance-205bb1` | `776317d2` | 0 |
| `willem/nightly-news-categories` | `13754a57` | 0 |
| `feature/optimize-card-stack-performance` | `ca649b2d` | 0 |

Relevant existing main commits include warm onboarding `8174de23`, source-grounded
onboarding discovery `e4711f48`, pinned category strip `d652b3cc`, category long press
`a95e4122`, nightly category maintenance `0fe99134`, and progressive onboarding
`a23ed53c` / mixed publication correction `48a21d7e`.

GitHub had no outstanding onboarding/category/aggregator PR. The only open PR was
[#10](https://github.com/willemave/newsbuddy/pull/10), a separate Knowledge/Learning
design document. It is outside this integration. Older merged PRs do not contain the
missing aggregator feature.

## Remaining branch and preserved work

`willem/aggregator-freshness` ended at
`cba333048718b37a6d9e4ff134de9c69727027f5`, four commits absent from main:

- `a3664d3387da36831eb4c8c2feebb068027105ef`: freshness and demand implementation.
- `592b543510a4feb9e6ca90ea067cf453c2aff664`: release preparation and module splits.
- `b3fa89b07a9a12bf2252814dca9e3a64050966fc`: explicit imports after those splits.
- `cba333048718b37a6d9e4ff134de9c69727027f5`: historically blocked release-gate log.

Its original worktree remains untouched:
`/Users/willem/.codex/worktrees/629c8ddc-28e7-4ec8-8f41-506f10578e9b/news_app`.
Six uncommitted cleanup files there are preserved: `docs/log.md`, API onboarding and
scraper normalization, DB Brutalist demand, provider scraping, and worker scraping.
Their canonical-list cleanup is superseded by the current nine-source catalog;
applicable bound catalog query, selector/URL reuse, and empty-count-query skip are
included in the adaptation.

## Intentional adaptation to current main

HTTP writes and chat aggregator subscription commands share one transactional audience hook.
The adaptation preserves all nine typed source keys, the single
`aggregator_config_admits` visibility owner, current metadata/public contracts,
provider identity normalization, and observed usage persistence. It adds forward
migrations rather than the obsolete older `newsly_brutalist_visible` migration.
Current silent topic canonicalization remains; the old extra rejection path is not
restored. Old first-edition global-scrape plumbing is obsolete after warm attachment.

News sources use 15/60-minute cadence; daily paper sources retain 60/120 minutes.
Demand-triggered Brutalist work queues once per serialized audience transition;
an older in-flight snapshot cannot absorb a new or re-added topic. Shared transaction locks protect audience transitions,
denied fallback publication, and the rolling section budget. Partial demanded-topic
failures remain failures, and existing metadata still updates when the new-item cap
is full. Paid fallback uses the current typed policy and live canonical demand.

A body-backed story cannot adopt a metadata-only sibling summary/representative.
This can add one fresh model summary per affected cluster, including outside demand
replay. Newly visible skipped stories have bounded recent re-enrichment; the paid
fallback and summary calls remain separately observed.

The four old commits are semantically accounted for by the adaptation, current
module organization and updated validation notes. The historical unsuccessful
release-gate status is not a new release claim. After the validated adaptation,
an explicit ancestry merge records the original tip; the original dirty worktree
is neither reset nor committed.

## Verification boundaries

The integration is local. No push or production deployment is performed. Remote
main remains at the fetched baseline unless separately changed by its owner.
Repository code, test results, successful source checks and ready historical items
are distinct from evidence of fresh production intake. In particular, this feature
does not change SciURLs parsing or prove that source has new items.

Final local ancestry checks use `git merge-base --is-ancestor <tip> HEAD` for every
branch in the table and the original aggregator tip. Validation and review evidence
is recorded in the matching `docs/log.md` entries and the integration handoff.

The final affected Rust library suites passed 619 tests (API 84, DB 199, providers
137, worker 199), with one existing provider test ignored. All-target Clippy denied
warnings, and formatting, diff and module guardrails passed. Read-only Claude Opus
5.5 high reviews covered the design, adaptation, paid fallback races, and first-run
progression. Bounded addenda approved shared chat subscriptions and set-based discussion selection; polling-only counters/clocks preserve checkpoints without hiding title, URL, topic, body or discussion changes. Re-fetches retain the initial discovery timestamp, and summary publication preserves refreshed current-row counters. Local runtime measurements after merge remain a separate verification.
