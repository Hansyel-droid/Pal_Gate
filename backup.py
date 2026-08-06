"""
Daily backup — database AND uploaded documents.

Run via Windows Task Scheduler or cron; see "Daily Maintenance" in
DEPLOYMENT.md for the exact commands.

Two things get backed up, because restoring either one alone is useless:

  1. db.sqlite3      — every application, account, gate log, audit entry.
  2. media/          — the actual uploaded OR/CR, licences and CORs that
                       those database rows point at.

Restoring only the database would leave every document link pointing at a
file that no longer exists.

WHERE BACKUPS GO
----------------
By default, ./backups next to this script. That protects against someone
deleting a record by mistake, but NOT against the disk or machine dying —
the backups are sitting on the same disk as the thing they're backing up.

Set BACKUP_DIR in .env (or as an environment variable) to somewhere that
lives off this machine — a mounted network share, an OneDrive/Google Drive
synced folder, a second physical disk:

    BACKUP_DIR=D:/palsu_gate_backups
    BACKUP_DIR=/mnt/backups/palsu_gate

WHY NOT A PLAIN FILE COPY
-------------------------
Copying a live SQLite file byte-for-byte can catch it mid-write and
produce a backup that looks fine — right name, right size — but won't
open. sqlite3's own backup API takes a consistent snapshot while the site
keeps serving, and this script verifies the result before keeping it.
"""

import os
import shutil
import sqlite3
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'db.sqlite3')
MEDIA_DIR = os.path.join(BASE_DIR, 'media')

# Read BACKUP_DIR from the environment, falling back to .env, then to a
# local ./backups folder. Kept dependency-free so this still runs if it's
# ever invoked outside the virtualenv.
def _backup_root():
    value = os.environ.get('BACKUP_DIR')
    if not value:
        env_file = os.path.join(BASE_DIR, '.env')
        if os.path.exists(env_file):
            with open(env_file, encoding='utf-8') as handle:
                for line in handle:
                    line = line.strip()
                    if line.startswith('BACKUP_DIR=') and not line.startswith('#'):
                        value = line.split('=', 1)[1].strip()
                        break
    return value or os.path.join(BASE_DIR, 'backups')


BACKUP_ROOT = _backup_root()
DB_BACKUP_DIR = os.path.join(BACKUP_ROOT, 'database')
MEDIA_MIRROR_DIR = os.path.join(BACKUP_ROOT, 'media')
KEEP = 30
PREFIX = 'db_backup_'

failures = []


def backup_database():
    """Consistent, verified snapshot of db.sqlite3."""
    if not os.path.exists(DB_PATH):
        failures.append(f'No database found at {DB_PATH}')
        return

    os.makedirs(DB_BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    target = os.path.join(DB_BACKUP_DIR, f'{PREFIX}{timestamp}.sqlite3')

    source = sqlite3.connect(DB_PATH)
    try:
        dest = sqlite3.connect(target)
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()

    # An unverified backup is a guess. Confirm it opens and is intact
    # before it counts towards retention.
    check = sqlite3.connect(target)
    try:
        result = check.execute('PRAGMA integrity_check;').fetchone()[0]
    finally:
        check.close()

    if result != 'ok':
        os.remove(target)
        failures.append(f'Database backup failed integrity check: {result}')
        return

    size_mb = os.path.getsize(target) / (1024 * 1024)
    print(f'  database : {os.path.basename(target)} ({size_mb:.1f} MB) — verified')

    # Retention. Only ever touches files this script created, so nothing
    # else in the folder can be removed by accident.
    snapshots = sorted(
        f for f in os.listdir(DB_BACKUP_DIR)
        if f.startswith(PREFIX) and f.endswith('.sqlite3')
    )
    removed = 0
    while len(snapshots) > KEEP:
        os.remove(os.path.join(DB_BACKUP_DIR, snapshots.pop(0)))
        removed += 1
    if removed:
        print(f'             removed {removed} snapshot(s) beyond the last {KEEP}')


def backup_media():
    """
    Mirror uploaded documents.

    Uploads are write-once — each gets a random UUID filename and is never
    modified afterwards — so copying only what's missing is enough, and
    keeps this cheap to run daily even as the folder grows. Files are
    never deleted from the mirror: if a document disappears from the live
    folder (bad deletion, disk fault, ransomware), the backup copy is
    exactly what you want to still have.
    """
    if not os.path.isdir(MEDIA_DIR):
        print('  documents: no media/ folder yet — nothing to copy')
        return

    copied = 0
    skipped = 0
    errors = 0
    total_bytes = 0

    for root, _dirs, files in os.walk(MEDIA_DIR):
        relative = os.path.relpath(root, MEDIA_DIR)
        target_dir = (
            MEDIA_MIRROR_DIR if relative == '.'
            else os.path.join(MEDIA_MIRROR_DIR, relative)
        )
        os.makedirs(target_dir, exist_ok=True)

        for name in files:
            source_path = os.path.join(root, name)
            target_path = os.path.join(target_dir, name)
            try:
                source_size = os.path.getsize(source_path)
                # Re-copy if missing or size differs (a partial copy from
                # an interrupted run would fail this check and be redone).
                if (os.path.exists(target_path)
                        and os.path.getsize(target_path) == source_size):
                    skipped += 1
                    continue
                shutil.copy2(source_path, target_path)
                copied += 1
                total_bytes += source_size
            except OSError as exc:
                errors += 1
                failures.append(f'Could not copy {source_path}: {exc}')

    size_mb = total_bytes / (1024 * 1024)
    print(f'  documents: {copied} new ({size_mb:.1f} MB), '
          f'{skipped} already backed up'
          + (f', {errors} FAILED' if errors else ''))


print(f'Backup started {datetime.now():%Y-%m-%d %H:%M:%S}')
print(f'Destination: {BACKUP_ROOT}')

if os.path.abspath(BACKUP_ROOT).startswith(os.path.abspath(BASE_DIR)):
    print('  NOTE: backups are on the same disk as the live data. Set '
          'BACKUP_DIR in .env to somewhere off this machine to protect '
          'against disk or hardware failure.')

backup_database()
backup_media()

if failures:
    print(f'\nBACKUP FINISHED WITH {len(failures)} PROBLEM(S):', file=sys.stderr)
    for problem in failures:
        print(f'  - {problem}', file=sys.stderr)
    # Non-zero exit so Task Scheduler / cron reports this as a failed run
    # instead of silently "succeeding" every night.
    sys.exit(1)

print('\nBackup completed successfully.')
