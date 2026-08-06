import logging
import os
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.core.files.storage import default_storage

logger = logging.getLogger('django')


class Command(BaseCommand):
    help = 'Delete temp upload files older than 24 hours'

    def handle(self, *args, **kwargs):
        temp_dir = 'temp_uploads'
        cutoff = timezone.now() - timedelta(hours=24)
        deleted = 0
        errors = 0

        try:
            # listdir() returns (directories, files) — the per-applicant
            # folders are the FIRST element. Reading the second one handed
            # back the (always empty) list of loose files sitting directly
            # in temp_uploads/, so this command silently deleted nothing.
            folders, _ = default_storage.listdir(temp_dir)
        except Exception:
            logger.exception('cleanup_temp_files: could not list %s', temp_dir)
            self.stdout.write(f'Deleted {deleted} temp file(s), 0 error(s) (no temp_uploads directory).')
            return

        for folder in folders:
            folder_path = f'{temp_dir}/{folder}'
            try:
                _, files = default_storage.listdir(folder_path)
                for filename in files:
                    file_path = f'{folder_path}/{filename}'
                    modified = default_storage.get_modified_time(file_path)
                    if modified < cutoff:
                        default_storage.delete(file_path)
                        deleted += 1
            except Exception:
                errors += 1
                logger.exception('cleanup_temp_files: failed to clean up %s', folder_path)
                self.stderr.write(f'Error cleaning up {folder_path} — see logs/errors.log')

        self.stdout.write(f'Deleted {deleted} temp file(s), {errors} error(s).')
