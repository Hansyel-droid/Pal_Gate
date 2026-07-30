from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone

from applications.models import StickerApplication
from gate.audit import log_action

# A sticker is valid for one academic year
VALIDITY_DAYS = 365


class Command(BaseCommand):
    help = 'Expire issued stickers older than one academic year (365 days)'

    def handle(self, *args, **kwargs):
        cutoff = timezone.now() - timedelta(days=VALIDITY_DAYS)

        # submitted_at is null for records that were never submitted,
        # and those are excluded automatically by this filter.
        expiring = StickerApplication.objects.filter(
            status='issued',
            submitted_at__lt=cutoff
        )

        count = 0
        for application in expiring:
            application.status = 'expired'
            application.save()
            log_action(
                None,
                'app_expired',
                f'Sticker expired for {application.full_name} '
                f'(Plate: {application.plate_number}, '
                f'Sticker ID: {application.sticker_id}). '
                f'Issued on {application.submitted_at:%Y-%m-%d}.',
                target_user=application.applicant.username,
                extra_data={
                    'application_id': application.pk,
                    'sticker_id': application.sticker_id,
                    'rfid_uid': application.rfid_uid,
                }
            )
            count += 1

        self.stdout.write(f'Expired {count} sticker(s).')
