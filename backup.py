import shutil
import os
from datetime import datetime

# Run this script daily via Task Scheduler (Windows) or cron (Linux)
backup_dir = 'backups'
os.makedirs(backup_dir, exist_ok=True)

timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
backup_path = os.path.join(backup_dir, f'db_backup_{timestamp}.sqlite3')

shutil.copy2('db.sqlite3', backup_path)
print(f'Backup saved: {backup_path}')

# Keep only last 30 backups
backups = sorted(os.listdir(backup_dir))
while len(backups) > 30:
    os.remove(os.path.join(backup_dir, backups.pop(0)))