# Migrating Session-Based Data to User-Based Tracking

This guide explains how to recover your favorites and read status after migrating from session-based to user-based authentication.

## Background

The migration from session_id to user_id tracking (Alembic migration `f77c586ac45b`) deleted all existing favorites, read status, and unlikes data. If you have a database backup from before the migration, you can use the `migrate_session_data.py` script to restore your data.

## Prerequisites

1. **Database backup** from before the user-based tracking migration
   - Must be a SQLite database file (`.db`)
   - Must contain tables with old `session_id` schema

2. **Your session ID** from the old system
   - This is the cookie or session identifier that was used to track your activity
   - Check your browser's developer tools or old session data

3. **Your user ID** in the new system
   - You can find this by running `python scripts/migrate_session_data.py --list-users`

## Quick Start

### Step 1: List Available Users

Find your user ID in the current database:

```bash
source .venv/bin/activate
python scripts/migrate_session_data.py --list-users
```

Output:
```
📋 Available users:

   ID:   5 | willem.ave@gmail.com | Willem Ave | ✅ ACTIVE
```

### Step 2: Find Your Session ID (Optional)

If you don't know your session ID, list all session IDs in your backup database:

```bash
python scripts/migrate_session_data.py --backup-db /path/to/backup.db --list-sessions
```

Output:
```
📋 Session IDs in backup database:

   abc123xyz...                                       |   42 records
   def456uvw...                                       |   12 records

   Total: 2 unique session IDs
```

The session ID with the most records is likely yours.

### Step 3: Test Migration (Dry Run)

First, run a dry-run to see what would be migrated:

```bash
python scripts/migrate_session_data.py \
  --backup-db /path/to/backup.db \
  --session-id "your-session-id-here" \
  --user-id 5 \
  --dry-run
```

Output:
```
================================================================================
🔄 MIGRATION CONFIGURATION
================================================================================
Backup database:  /path/to/backup.db
Source session:   your-session-id-here
Target user ID:   5
Mode:             DRY RUN (no changes)
================================================================================

✅ Target user verified: willem.ave@gmail.com (Willem Ave)

🔍 Searching for favorites with session_id='your-session-id-here'...
   Found 25 favorite records

🔍 Searching for read status with session_id='your-session-id-here'...
   Found 150 read status records

🔍 Searching for unlikes with session_id='your-session-id-here'...
   Found 3 unlike records

⚠️  Dry run - no changes made

================================================================================
📊 MIGRATION SUMMARY
================================================================================
Favorites:      25 found |   25 migrated |    0 skipped
Read Status:   150 found |  150 migrated |    0 skipped
Unlikes:         3 found |    3 migrated |    0 skipped
================================================================================

💡 DRY RUN: Would migrate 178 records (skip 0 duplicates)
   Run without --dry-run to apply changes
```

### Step 4: Run Actual Migration

If the dry run looks correct, run the migration for real:

```bash
python scripts/migrate_session_data.py \
  --backup-db /path/to/backup.db \
  --session-id "your-session-id-here" \
  --user-id 5
```

Output:
```
================================================================================
🔄 MIGRATION CONFIGURATION
================================================================================
Backup database:  /path/to/backup.db
Source session:   your-session-id-here
Target user ID:   5
Mode:             LIVE (will modify database)
================================================================================

✅ Target user verified: willem.ave@gmail.com (Willem Ave)

🔍 Searching for favorites with session_id='your-session-id-here'...
   Found 25 favorite records

🔍 Searching for read status with session_id='your-session-id-here'...
   Found 150 read status records

🔍 Searching for unlikes with session_id='your-session-id-here'...
   Found 3 unlike records

✅ Migration committed to database

================================================================================
📊 MIGRATION SUMMARY
================================================================================
Favorites:      25 found |   25 migrated |    0 skipped
Read Status:   150 found |  150 migrated |    0 skipped
Unlikes:         3 found |    3 migrated |    0 skipped
================================================================================

✅ COMPLETE: Migrated 178 records (skipped 0 duplicates)
```

## Using Environment Variable for Session ID

You can set the session ID as an environment variable to avoid typing it repeatedly:

```bash
export SESSION_ID="your-session-id-here"
python scripts/migrate_session_data.py --backup-db backup.db --user-id 5 --dry-run
```

## Troubleshooting

### "Backup database not found"

Make sure the path to your backup database is correct:
```bash
ls -l /path/to/backup.db
```

### "User ID X not found"

Run `--list-users` to see available user IDs:
```bash
python scripts/migrate_session_data.py --list-users
```

### "No session data found in backup database"

Your backup might be from after the migration, or the tables might not exist. Check the backup:
```bash
sqlite3 /path/to/backup.db ".tables"
```

You should see tables like `content_favorites`, `content_read_status`, etc.

### Duplicate Records Skipped

The script automatically skips records that already exist for the target user_id. This is safe and prevents duplicates.

## Notes

- The script preserves timestamps from the original records
- Existing data for the target user is never deleted or modified
- The migration is idempotent - running it multiple times is safe
- Only unique (user_id, content_id) pairs are created due to database constraints

## Advanced Usage

### Migrating Multiple Session IDs

If you used multiple devices/browsers, you might have multiple session IDs. Migrate each one separately:

```bash
# Session 1
python scripts/migrate_session_data.py --backup-db backup.db --session-id "session1" --user-id 5

# Session 2
python scripts/migrate_session_data.py --backup-db backup.db --session-id "session2" --user-id 5
```

The script will skip duplicates automatically.

### Batch Migration Script

Create a bash script to migrate multiple sessions:

```bash
#!/bin/bash
BACKUP_DB="/path/to/backup.db"
USER_ID=5

for SESSION_ID in "session1" "session2" "session3"; do
    echo "Migrating session: $SESSION_ID"
    python scripts/migrate_session_data.py \
        --backup-db "$BACKUP_DB" \
        --session-id "$SESSION_ID" \
        --user-id "$USER_ID"
done
```
