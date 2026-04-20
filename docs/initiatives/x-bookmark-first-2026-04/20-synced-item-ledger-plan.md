# X Synced-Item Ledger Plan

**Opened:** 2026-04-19  
**Status:** Complete  
**Scope:** X bookmark sync persistence, DB schema, architecture docs, targeted tests  
**Primary goal:** add a per-user synced-item ledger so X bookmark sync history is tracked independently from `contents`

---

## Problem

Today the X bookmark pipeline tracks sync progress in `user_integration_sync_state`, but the actual synced items are only inferable from `contents` rows plus `submitted_via="x_bookmarks"`.

That is not a reliable per-user ledger because:

- `contents` is global, not per-user
- content rows may be reused across duplicate submissions
- bookmark sync needs a durable audit trail even when content reuse or later URL resolution changes the content row shape

---

## Contract

Newsly should persist two distinct kinds of X bookmark state:

1. sync cursor/state in `user_integration_sync_state`
2. synced-item history in a dedicated per-connection ledger table

The new ledger should support:

- one row per `(connection_id, channel, external_item_id)`
- durable linkage to the created or reused `content_id` when available
- a stable external item URL for audit/debugging
- timestamps for first sync and most recent observation

---

## Plan

### 1. Add a dedicated synced-item ledger table

- [x] Add `user_integration_synced_items` to the ORM schema.
- [x] Add a PostgreSQL migration for the new table and indexes.
- [x] Use a unique constraint on `(connection_id, channel, external_item_id)`.

### 2. Record bookmark sync results into the ledger

- [x] Record one ledger row for each bookmark accepted during sync.
- [x] Persist `content_id` and canonical tweet URL alongside the external item id.
- [x] Update existing ledger rows on reuse instead of inserting duplicates.
- [x] Reuse the ledger during sync so rescans can refresh already-synced bookmarks without re-submitting content.

### 3. Keep existing cursor logic intact

- [x] Continue to use `user_integration_sync_state` for bookmark cursor/cooldown state.
- [x] Do not change bookmark content creation or tweet snapshot persistence behavior in this pass.

### 4. Verify and document

- [x] Add targeted tests proving the ledger is written for created content rows.
- [x] Add targeted tests proving the ledger is written when sync reuses an existing content row.
- [x] Update architecture docs to mention the new storage split.
- [x] Run targeted `pytest` and `ruff check` on touched files.

---

## Implementation notes

- The ledger is additive and does not replace `contents` as the canonical content store.
- The ledger is per connection and per external item, so it remains authoritative even when `contents` is reused.

## Outcome

- Bookmark sync progress remains in `user_integration_sync_state`.
- Synced bookmark history now lives in `user_integration_synced_items`.
- `contents` no longer has to act as the only source of truth for “what did this user already sync from X?”.
