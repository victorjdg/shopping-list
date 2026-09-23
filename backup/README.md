# SQLite backups

`backup.py` makes a consistent daily copy of `shopping_list.sqlite` via
`sqlite3.Connection.backup()` (safe even while the file is in use by the running container —
unlike a plain `cp`, which could capture a mid-write, inconsistent state) and prunes copies
older than 14 days.

Runs via cron, not inside a container — it's a standalone script with no new dependencies
(`sqlite3` is part of the Python standard library).

## Deploying

```bash
# Copy the script to wherever the app runs
scp backup.py your-server:/path/to/backup/

# Crontab — once a day at 03:00, off-peak
crontab -e
# Add:
0 3 * * * /usr/bin/python3 /path/to/backup/backup.py /path/to/shopping_list.sqlite /path/to/backups >> /path/to/backup/backup.log 2>&1
```

Usage: `python3 backup.py <source_db> <backup_dir> [retention_days]` (default retention: 14
days).

## Restoring a backup

```bash
# Stop the container so it doesn't write while restoring
podman stop shopping-list

# Copy the chosen backup over the real file (check the date in the filename first)
cp /path/to/backups/shopping_list-YYYY-MM-DD.sqlite /path/to/shopping_list.sqlite

podman start shopping-list
```

**Note on disk placement**: for real protection against a disk failure (not just accidental
deletion/corruption of the live file), the backup destination should be a *different physical
disk* than the source database — a same-disk backup only protects against the latter.
