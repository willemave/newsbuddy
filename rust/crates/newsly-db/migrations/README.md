# Embedded SQLx migrations

`20260830000000_alembic_20260829_02_baseline.sql` is the complete schema produced by migrating a
fresh PostgreSQL database through the frozen Alembic head. Fresh databases execute it normally.
Existing databases must use the fingerprint-gated `newsly-db baseline` command and must never
execute the baseline schema SQL over existing relations.

Later changes use SQLx timestamped reversible pairs unless a reviewed roll-forward-only migration
is required. Every migration is embedded into the exact-image `newsly-db` binary. Never edit an
applied migration; add a new roll-forward repair.
