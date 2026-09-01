# Release pipeline acceleration

## Goal

Reduce normal Rust-era production releases from roughly one hour to a warm-path target of 15–25
minutes without weakening exact-SHA testing, immutable image identity, migration safety, blue/green
rollback, or live production verification.

## Observed bottleneck

The reusable quality workflow built both production images with plain `docker build`, discarded
them, and then the deployment workflow rebuilt and published the same images on a fresh runner.
The Rust image copied the entire workspace before a release build, so ordinary source changes
invalidated the dependency compilation layer. The extractor placed revision metadata before its
Python and Chromium installation layers, coupling those expensive layers to every release SHA.

## Design

1. The quality workflow remains the authority for Rust, SQLx, contracts, the two isolated Python
   packages, and native iOS tests. It no longer performs disposable production-image builds.
2. After quality attests the exact SHA, the deployment workflow builds and publishes the Rust and
   extractor images exactly once, then deploys only those SHA-tagged images.
3. The Rust Dockerfile uses a pinned `cargo-chef` planner/cook layer so dependency compilation is
   keyed by Cargo manifests and the lockfile rather than application source.
4. The extractor keeps dependency and Chromium installation ahead of source and revision metadata,
   so a source-only or SHA-only change reuses the expensive environment layers.
5. Catalog adoption remains fail-closed. The verifier accepts either the complete catalog produced
   by the frozen baseline SQL or the complete catalog produced by the legacy Alembic history. The
   latter is derived from the former by two pinned, exact partial-index substitutions and has its
   own manifest hash; the live catalog is never normalized.
6. Before deployment stops writers, the exact image runs a read-only adoption preflight. After the
   maintenance barrier and before the first write, deployment creates and verifies a PostgreSQL
   custom-format backup under `/data`, records its path, and reuses that immutable recovery point
   on a retry. Adoption rechecks the fingerprint while holding its advisory lock. A partially
   applied post-baseline history fails the automated preflight for operator inspection.

## Validation

- Run the focused fingerprint unit tests and verify the complete production inventory equals the
  manifest-pinned legacy-Alembic snapshot and differs from the frozen baseline only at the two
  audited renderings.
- Exercise the read-only preflight, backup creation/verification, first adoption, and interrupted
  exact-history retry paths against a disposable PostgreSQL 15 database.
- Build both Dockerfiles from a cold cache, then rebuild with a different SHA and verify dependency
  layers are cached.
- Run Actionlint, architecture/contracts, the full Rust workspace, isolated Python packages, and
  native iOS release gates.
- Push only the tested SHA, wait for the SHA-matched workflow, and verify the active image, SQLx
  adoption marker, containers, public health, and Rust operator snapshot.

## Non-goals

This change does not run image builds speculatively before tests, change migration authority,
weaken the production catalog fingerprint, or alter Apple distribution.
