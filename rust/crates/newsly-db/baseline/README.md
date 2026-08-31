# Alembic-head SQLx baseline

This directory is the immutable adoption evidence for SQLx baseline version `20260830000000`.
It was generated from a newly created PostgreSQL database after the complete Alembic graph had
reached its single head, `20260829_02`.

- `manifest.json` pins the source head, migration version, hashes, and inventory counts.
- `catalog-inventory.json` is the exact normalized catalog expected before adoption.
- `data-invariants.json` records the bounded data invariants required by the frozen history.
- `role-policy.json` checks ownership and grants without embedding environment-specific role names.
- the SQL used to collect each snapshot lives beside the Rust verifier in `src/*.sql`.

The inventory covers extensions and versions, application schemas, relations, columns and
defaults, sequence ownership, constraints and validation state, index definitions and readiness,
user-defined types/routines/triggers, row-level policies, and explicit grants. It intentionally
excludes SQLx's own history table, physical OIDs, planner statistics, storage paths, and literal
role names.

Do not edit these files or the baseline migration after an adoption has run. A later schema change
must be a new SQLx migration. The retired Alembic source and one-time baseline generator are kept
in repository history; they must not be restored as active schema authority. If a historical
database cannot satisfy this immutable evidence, stop and investigate instead of regenerating the
baseline around it.
