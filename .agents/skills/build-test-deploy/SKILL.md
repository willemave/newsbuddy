---
name: build-test-deploy
description: Validate a clean Newsly commit locally, push that exact SHA to main, wait for the production image build and blue-green deploy, and prove live health. Use for Newsly build-test-deploy, full release, or production deployment requests; Apple distribution is separate.
---

# Build, test, deploy

Treat local validation, the pushed Git SHA, the GitHub image/deploy workflow,
and live production as separate evidence layers. Never describe one as proof of
another.

## Local release authority

Preserve unrelated worktree changes and commit only work authorized by the
user. Reconcile with current `origin/main`, then run the canonical gate from a
clean commit:

```bash
scripts/release_gate.sh --env-file /absolute/path/to/local.env
```

For deck, chat, Share Extension, provider, sandbox, worker, or explicitly full
end-to-end releases, include the live production-shaped matrix:

```bash
scripts/release_gate.sh \
  --env-file /absolute/path/to/local-staging.env \
  --with-live-smoke \
  --allow-live-provider-costs
```

That live phase builds each Docker image once, runs all scenarios against the
same disposable stack, and tears it down. Do not substitute in-process tests,
remote staging, or repeated per-scenario image builds.

The gate must pass on the exact clean commit that will be pushed. If a fix
changes the commit, rerun the affected validation and the complete gate before
release. Do not bypass repository hooks.

## Push and deploy

Immediately before pushing, confirm the tested SHA still contains current
`origin/main` and the worktree remains clean. Push only that SHA to `main`.

GitHub's `Docker Deploy` workflow is intentionally not a second source-level
quality gate. It must:

- build immutable Rust and extractor images tagged with `github.sha`;
- smoke-test those published images;
- refuse a stale SHA when `main` has advanced;
- deploy through the existing blue-green migration and rollback path.

Wait for the workflow associated with the pushed SHA. If a newer push makes it
stale, do not force the old release through; reconcile, retest, and push the new
exact SHA only when authorized.

## Production proof

After GitHub reports success, prove the live revision and health with the Rust
operator, beginning with:

```bash
newsly-admin --output json health snapshot
```

Corroborate the active exact-SHA image, container health, public readiness, and
migration state. Report local tested SHA, pushed `main` SHA, workflow result,
and live revision separately.

Xcode Cloud provides an independent clean iOS build on each push to `main`, but
Apple distribution, TestFlight, and App Store Connect are outside this workflow
unless the user explicitly requests them.
