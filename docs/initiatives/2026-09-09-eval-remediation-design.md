# Eval remediation design

Status: implemented locally; hosted verification pending

Scope: repair the September 9 eval blockers and address reproducible chat,
summarization, Briefing, and News-relation failures. This design does not
authorize production-source disclosure, OpenRouter spend, commit, push, or
deployment.

## Oracle verification and synthesis

Fable reviewed the plan with repository access before implementation. Direct
inspection accepted the production-aligned ordering but changed the embedding
work materially:

- The original MiniLM result was not production-comparable. Curated title-only
  cases fabricated summary and provenance views, while the production policy
  renormalizes weights when those views are absent.
- The reported macro precision gave every no-prediction case a perfect score,
  obscuring missed positive groups. Reports now separate positive-case recall,
  negative-case pass rate, and precision among cases that predicted a pair.
- Repeated `wsj.com` and `Subscribe to read` titles were deterministic invalid
  inputs, not embedding successes. The Rust policy now rejects low-information
  titles before semantic scoring.
- Threshold defaults had drifted across Python entrypoints. Rust now owns and
  emits the default 0.85/0.75 policy; Python uses it unless both values are
  explicitly overridden.
- Frozen feed fixtures used distinct synthetic URLs as singleton gold labels
  for byte-identical reposts. Within one user/window, identical normalized full
  titles are now reviewed as one event; case boundaries retain user isolation.

Fable also identified the Briefing schema mismatch and endorsed the earlier
scoped Sol chat repair. A read-only production check confirmed the active image
still has `briefing_segments.prompt_version VARCHAR(16)`, but did not confirm a
current retry storm: recent sweep tasks were completing without appends. The
migration therefore fixes a proven latent append/publication blocker without
claiming a broader live incident.

Implemented local evidence:

- MiniLM curated titles: 20/101 exact cases, positive recall 0.356, negative
  safety 2/2. This corrected baseline is diagnostic only and reinforces that
  production thresholds must not be tuned from MiniLM.
- MiniLM frozen relations: 8/8 cases after the reviewed repost policy; four
  cases contain positive pairs and four are negative safety cases.
- PostgreSQL publication persists `briefing-v7-commonmark` through the real
  production repository path after migration.
- Focused chat ownership/pagination, Sol default, search-input, summary-prompt,
  Rust compile, and Python package gates pass.

## Decision

Use production-aligned acceptance criteria.

- Sol candidate with Sol judge is authoritative for chat.
- Luna candidate with Sol judge is authoritative for summarization and Briefing.
- The configured hosted Qwen embedding route is authoritative for production
  News relations.
- Local MiniLM remains a useful diagnostic baseline, but its 23/101 result is
  not a reason to tune production matching policy by itself.
- Deterministic contract, persistence, privacy, and destination checks remain
  hard requirements regardless of model.

This keeps infrastructure failures, model-quality failures, and experimental
model comparisons separate.

## Evidence from the rerun

The rerun used revision `b2320a09e0c6198aac5d23ebc0e0de77115efc4b`
from a dirty checkout. Concurrent warm-news, Briefing, feed-status, contract,
and client changes overlap several packages and must be preserved.

- Chat: 5/12 passed with Sol/Sol. Failures involved missing individual links,
  incomplete saved/unread retrieval, unsupported extrapolation, unresolved
  source ambiguity, and recommendations not grounded in available evidence.
- Raw summarization: 3/5 passed with Luna/Sol. One result over-emphasized a
  malicious reader comment; one lost speaker-level attribution.
- Briefing: publication failed before evaluation because
  `briefing-v7-commonmark` does not fit the existing `VARCHAR(16)`
  `briefing_segments.prompt_version` column.
- Frozen feed relations: 4/8 non-empty cases passed. The configured
  `exact_duplicates` slice was empty and incorrectly appeared as 0/0.
- Curated title clustering with MiniLM: 23/101 passed, with high macro
  precision (0.972) and low recall (0.426). Production Qwen was not run.
- The Python eval package passed 34 tests with five opt-in skips; Ruff and MyPy
  passed, and the Rust eval binaries built.

## Options considered

### 1. Production-aligned staged remediation — recommended

Fix deterministic blockers, port the existing reviewed chat retrieval work,
establish a hosted-Qwen clustering baseline, and change product behavior only
for failures reproduced on the production route. This produces comparable
evidence and avoids tuning for MiniLM.

### 2. Make every current suite green

Treat MiniLM and every stochastic judge result as a release gate. This gives a
simple headline but risks model-specific thresholds, prompt overfitting, and
rubric weakening. Reject this approach unless MiniLM becomes a supported
production route.

### 3. Repair infrastructure and stop

Fix the Briefing schema and harness integrity, then rerun without addressing
quality failures. This is a useful first milestone but does not satisfy the
chat or relation-quality objective.

## Workstream 1: make eval execution trustworthy

Perform this work first so later results are interpretable.

1. Work in a clean isolated checkout at the selected base revision. Do not
   modify or absorb the current checkout's unrelated feed-status work.
2. Add a forward SQLx migration that expands
   `briefing_segments.prompt_version` to `TEXT`. Do not edit the historical
   baseline and do not shorten `briefing-v7-commonmark` to fit an obsolete
   storage limit.
3. Add PostgreSQL integration coverage that persists a real composed segment
   with the current prompt version through the production publication path.
   Cover both a fresh database and an upgrade from the current schema.
4. Assert that observed Briefing usage remains recorded independently of
   publication success and that an infrastructure failure cannot silently be
   reported as a quality failure.
5. Make every configured dataset fail closed when it resolves to zero cases.
   Restore the empty `exact_duplicates.jsonl` slice from a reviewed source or
   remove it from the default matrix until a valid fixture exists; never report
   0/0 as a successful evaluation.
6. Correct the documented chat command to point at the suite wrapper in
   `python/evals/datasets/chat/`, not the scenario-only contract file.
7. Add one canonical eval-matrix command or manifest that lists each maintained
   suite, route, case count, maximum top-level candidate/judge turns, privacy
   class, and output directory before execution. It must require explicit flags
   for paid hosted embeddings and complete production-source bodies.
8. Preserve partial reports on interruption. When the same deterministic
   infrastructure error recurs at one stage, stop the remaining cases in that
   stage before making more paid calls and report them as blocked.

Exit gate:

- Fresh and upgrade migrations pass.
- The current Briefing prompt version publishes successfully.
- Empty default datasets are rejected.
- The canonical matrix enumerates all suites and authorization requirements
  before any paid call.

## Workstream 2: land the scoped Sol chat repair

There is already an independently reviewed, locally verified implementation in
the earlier eval worktree. Port it file by file onto the clean base rather than
copying that dirty worktree wholesale.

1. Centralize `openai:gpt-5.6-sol` as the default for newly created and
   missing-model chat sessions. Preserve every explicit existing session model
   and leave summarization/Briefing defaults unchanged.
2. Replace the separate bounded content/feed searches with one chat-specific
   accessible-content query that:
   - applies user access, owning-source identity, unread, and saved filters
     before ordering and limiting;
   - supports deterministic offset pagination;
   - returns page count, matching count, accessible-scope count, `has_more`,
     and the next offset from the same database snapshot;
   - permits saved-content listing without a lexical query;
   - returns no unrelated fallback items for an empty text match;
   - resolves an exact followed source, reports unknown sources, and surfaces
     ambiguous followed-source names without guessing;
   - preserves individual article/episode titles and URLs.
3. Preserve `search_agent_knowledge` for its non-chat caller. Consolidate only
   the chat retrieval surface and keep task-tool lifecycle semantics intact.
4. Port the source identity fixture corrections (`feed_url`, owning show, saved
   and unread state) so the integration tests exercise the real relationship,
   not title/body mentions.
5. Update chat guidance around evidence rather than named tool calls:
   - count only displayed items and disclose bounded scope;
   - include individual destinations for requested items;
   - ask about or distinguish ambiguous sources;
   - keep factual recaps close to source language and label inference;
   - ground recommendations in returned search/discovery evidence;
   - distinguish feed-validation failure from search failure;
   - avoid claims about other library state when the evidence only establishes
     an empty result.
6. Add focused tests mapped to each rerun failure:
   - `unread_episodes`: all four records and links, correct count;
   - `saved_ai`: both saved AI episodes without requiring the literal token in
     each title;
   - `comparison`: both source records, links, and bounded paraphrase;
   - `ambiguous_show`: explicit ambiguity between both followed shows;
   - `follow_up`: no strengthened mechanism or causal claim;
   - `recommend_podcasts`: recommendations limited to available evidence and
     validation state;
   - `unread_news_empty`: no unsupported Knowledge preference claim;
   - private/other-user content remains inaccessible.
7. Keep the five-criterion judge and deterministic link/privacy checks intact.
   Change a rubric only when a saved known-good/known-bad calibration pair
   proves the rubric itself is wrong, and report that separately.

Exit gate:

- Focused Rust repository/tool tests and Python black-box integration tests
  pass.
- Each previously failing case passes three fresh Sol/Sol repetitions.
- A fresh full 12-case run has no graded failures. A provider policy rejection
  is preserved as an ungraded execution outcome, not converted into a pass.
- No subscription, read, or saved state changes occur during read-only cases.

## Workstream 3: rerun and repair summarization and Briefing quality

Do not change prompts until the schema repair yields publishable output.

1. Rerun the five raw synthetic summaries and all thirteen non-private
   synthetic/public Briefing cases with Luna candidate and Sol judge.
2. Rerun the two production-derived Briefing cases only if their inputs satisfy
   the approved disclosure class. The complete four-body production-source
   summary set remains behind explicit user approval.
3. Classify failures before editing:
   - input/projection or publication contract;
   - grounding/attribution;
   - completeness;
   - concision/repetition;
   - citation/destination correctness;
   - judge error.
4. Address the two known raw-summary failures with the smallest prompt or
   validator change:
   - treat injected/advertising reader text as evidence to reject, not content
     to amplify across takeaways and watch items;
   - retain named speaker ownership when a source's argument depends on one
     speaker proposing a claim and another rejecting it.
5. For Briefing-only quality failures, preserve the newspaper-density law and
   source reachability. Do not trade away qualifications, citations, or event
   separation merely to hit a word target.
6. Add focused fixtures for each accepted behavior change before another paid
   rerun.

Exit gate:

- Zero publication, contract, or judge-format errors.
- Each formerly failing deterministic expectation passes focused coverage.
- Each formerly failing model case passes three fresh repetitions, followed by
  one complete suite run.
- Reports retain the unabridged API response and exact judge input.

## Workstream 4: recalibrate News relation evaluation on the production route

Do not adjust thresholds from the MiniLM result alone.

1. Obtain explicit approval and credentials for the hosted Qwen/OpenRouter
   embedding comparison. Pin the provider route, disable fallbacks, require
   supported parameters, request ZDR, deny provider data collection, and state
   the exact case/vector count before running.
2. Run MiniLM and production Qwen against the same Rust-prepared canonical
   strings and the same 101 curated cases. Keep their reports separate.
3. Repair and run the frozen feed slices, including a real exact-duplicate
   fixture. Treat user scoping, exact identity, and low-information placeholder
   titles as deterministic policy cases rather than embedding-quality cases.
4. Triage production-Qwen failures by trace:
   - exact identity and user visibility;
   - low-information placeholders such as host-only/paywall titles;
   - missed positive same-event relations;
   - false merges between adjacent but distinct events/products;
   - candidate-retrieval limits versus similarity scoring.
5. Prefer deterministic guards for deterministic identities and invalid titles.
   Use embeddings for semantic relation, not to override explicit ownership or
   identity constraints.
6. If thresholds need recalibration, sweep the complete positive and negative
   corpus. Accept a threshold only when it improves full-corpus recall without
   weakening critical negative cases or macro precision below the approved
   baseline. Never tune against one named incident.
7. Add any newly confirmed production failure as a minimized curated case with
   provenance before changing policy.

Exit gate:

- Every default frozen slice contains at least one valid case.
- User isolation, exact-identity behavior, and placeholder-title guards pass
  deterministically.
- All critical curated regressions pass on production Qwen.
- The final report shows Qwen and MiniLM separately, including routing,
  dimensions, thresholds, precision, recall, F1, latency, and known/unknown
  cost.

## Final validation and rollout

After all four workstreams stabilize:

1. Run Rust formatting, warning-denied Clippy, focused PostgreSQL tests, the
   complete locked workspace tests, SQLx prepare/check, public contract drift,
   and the isolated Python eval Ruff/MyPy/pytest gates.
2. Run the canonical eval matrix with fresh output directories. Record the
   exact revision, dirty state, binary hashes, model routes, case counts,
   provider-call bounds, privacy approvals, and every execution error.
3. Compare against the September 9 artifacts by criterion and case, not only
   aggregate pass count. Call out rubric changes separately from candidate or
   product changes.
4. Request a focused independent review of the persistence migration, chat
   ownership/pagination SQL, and relation-policy changes.
5. Commit only if explicitly requested. Push, deployment, production proof,
   and any production regeneration remain separate explicit actions.

## Recommended implementation slices

1. Briefing schema and eval fail-closed integrity.
2. Scoped port of the reviewed Sol chat retrieval/default changes.
3. Raw-summary grounding and attribution fixes after a clean rerun.
4. Production-Qwen relation baseline and only then relation-policy changes.
5. Full matrix rerun and independent review.

Each slice must leave its affected path functional and independently
verifiable. Do not combine the unrelated dirty feed-status work or the broader
uncommitted changes from the earlier eval worktree.

## Open approvals

- Sending the four complete production-derived article/podcast bodies to
  OpenAI and Codex/Sol.
- Using the paid OpenRouter Qwen embedding route.
- Any commit, push, deployment, or production regeneration.
