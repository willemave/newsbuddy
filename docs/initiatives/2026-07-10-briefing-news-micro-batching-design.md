# Briefing News Micro-Batching Design

## Goal

Keep incremental Briefing news fresh without degrading into mostly single-source segments or
eight-link event dumps. News segments should read as compact, stable roundups with multiple inline
source links whenever traffic permits.

## Decisions

- Prefer three sources per news segment.
- Allow balanced news segments from two through four sources.
- Publish one remaining source when it has waited 25 minutes.
- Measure the deadline from the oldest pending source in each lens. New arrivals never move that
  deadline later.
- Split larger ready backlogs evenly instead of leaving singleton tails: `5 -> 3 + 2`,
  `6 -> 3 + 3`, `7 -> 4 + 3`, and `8 -> 4 + 4`.
- Require generated news output to contain exactly one passage and one paragraph, with every source
  linked exactly once.
- Retry output that violates the news contract. After the final attempt, use the compact
  deterministic news layout instead of appending coverage-repair prose.
- Keep the existing hourly sweep as the general backstop, but pull its pending task forward to the
  earliest pending-source deadline when necessary.

## Queue Flow

The first ready source still schedules the normal debounced append refresh. At refresh time, lens
assignment determines which news sources can share a segment. Three related unassigned sources can
create a lens; a smaller unassigned group moves to Briefs when its oldest source reaches the same
25-minute deadline.

For each news lens:

1. Compose immediately when at least three sources are pending.
2. Otherwise compose when the oldest pending source is at least 25 minutes old.
3. Retain younger one- or two-source groups and calculate their exact deadline.
4. Move the user's pending sweep task earlier when that deadline precedes the hourly sweep.

Moving tasks earlier uses an earliest-deadline-wins update. Later enqueue attempts cannot reset the
wait. A processing sweep releases its own dedupe key before scheduling its successor, as it does
today.

## Hand-Computed Cases

### Target reached before the deadline

```text
t=00 pending[lens] = [A], deadline = 25
t=08 pending[lens] = [A, B], deadline = 25
t=12 pending[lens] = [A, B, C], deadline = 25
t=15 append refresh -> compose [A, B, C]
```

### Low-volume lens reaches the deadline

```text
t=00 pending[lens] = [A], deadline = 25
t=15 append refresh sees count 1 and age 15
     retain A; pull sweep forward to t=25
t=25 sweep sees age 25
     compose [A]
```

### Balanced backlog

```text
pending[lens] = [A, B, C, D, E, F, G]
window sizes = [4, 3]
```

No source is stranded in a one-source tail when the lens already has enough ready material.

## Validation

- Unit-test balanced news window sizes and unchanged long-form windowing.
- Unit-test younger low-volume pending rows staying pending.
- Unit-test deadline-aged rows composing and the sweep task moving to the exact remaining wait.
- Unit-test enqueue dedupe behavior so later deadlines cannot postpone earlier work.
- Unit-test the news layout contract, retries, and deterministic final fallback.
- Run focused Briefing service tests and Ruff on touched Python files.
