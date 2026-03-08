# Database Backup Setup Guide

## Server Setup Steps

### 1. Create required directories
```bash
# Create backup directory
sudo mkdir -p /data/backups
sudo chown willem:willem /data/backups
sudo chmod 755 /data/backups

# Ensure log directory exists
sudo mkdir -p /var/log/newsly
sudo chown willem:willem /var/log/newsly
sudo chmod 755 /var/log/newsly
```

### 2. Verify script is on server
```bash
# Check script exists and is executable
ls -la /opt/newsly/scripts/backup_database.sh

# If not executable, fix it
chmod +x /opt/newsly/scripts/backup_database.sh
```

### 3. Verify sqlite3 is installed
```bash
# Check if sqlite3 is available
which sqlite3

# If not installed (Debian/Ubuntu):
sudo apt-get install sqlite3

# If not installed (RHEL/CentOS):
sudo yum install sqlite
```

### 4. Test the backup script manually
```bash
# Run as the user who will own the cron job
/opt/newsly/scripts/backup_database.sh

# Check if backup was created
ls -lh /data/backups/

# Check the log output for errors
cat /var/log/newsly/backup.log
```

### 5. Install the cron job
```bash
# Edit crontab for your user
crontab -e

# Add this line (runs at 2 AM daily):
0 2 * * * /opt/newsly/scripts/backup_database.sh >> /var/log/newsly/backup.log 2>&1

# Save and exit (in vi: press ESC, type :wq, press ENTER)
```

### 6. Verify cron job is installed
```bash
# List current cron jobs
crontab -l

# Check if cron service is running
sudo systemctl status cron     # Debian/Ubuntu
sudo systemctl status crond    # RHEL/CentOS
```

### 7. Monitor the backup (optional)
```bash
# Watch the log file in real-time (after cron runs)
tail -f /var/log/newsly/backup.log

# Check backup files
ls -lht /data/backups/ | head -10
```

## Cron Schedule Options

If you want a different schedule:

```bash
# Every day at 3:30 AM
30 3 * * * /opt/newsly/scripts/backup_database.sh >> /var/log/newsly/backup.log 2>&1

# Every day at midnight
0 0 * * * /opt/newsly/scripts/backup_database.sh >> /var/log/newsly/backup.log 2>&1

# Every 6 hours
0 */6 * * * /opt/newsly/scripts/backup_database.sh >> /var/log/newsly/backup.log 2>&1

# Every Sunday at 1 AM
0 1 * * 0 /opt/newsly/scripts/backup_database.sh >> /var/log/newsly/backup.log 2>&1
```

## Troubleshooting

### Cron job not running?
```bash
# Check cron service is running
sudo systemctl status cron

# Check system logs for cron errors
sudo grep CRON /var/log/syslog | tail -20

# Make sure user has cron permissions
ls -la /etc/cron.allow /etc/cron.deny
```

### Permissions errors?
```bash
# Check ownership of directories
ls -ld /data/backups /var/log/newsly

# Fix if needed
sudo chown -R willem:willem /data/backups /var/log/newsly
```

### Script not found?
```bash
# Verify full path
which bash
# Use absolute paths in crontab

# Example with explicit bash path:
0 2 * * * /bin/bash /opt/newsly/scripts/backup_database.sh >> /var/log/newsly/backup.log 2>&1
```

## Quick One-Liner Setup

Copy and paste on server:
```bash
sudo mkdir -p /data/backups /var/log/newsly && \
sudo chown willem:willem /data/backups /var/log/newsly && \
chmod +x /opt/newsly/scripts/backup_database.sh && \
/opt/newsly/scripts/backup_database.sh && \
echo "0 2 * * * /opt/newsly/scripts/backup_database.sh >> /var/log/newsly/backup.log 2>&1" | crontab -
```

Then verify:
```bash
crontab -l && ls -lh /data/backups/
```
