from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone

from applications.models import StickerApplication, STICKER_VALIDITY_DAYS
from gate.audit import log_action

VALIDITY_DAYS = STICKER_VALIDITY_DAYS


class Command(BaseCommand):
    help = 'Expire issued stickers older than one academic year (365 days)'

    def handle(self, *args, **kwargs):
        cutoff = timezone.now() - timedelta(days=VALIDITY_DAYS)

        # issued_at is when the sticker was actually handed out — measuring
        # from submitted_at would expire stickers based on when the
        # application was filed, which can be weeks before pickup.
        # select_related because the audit log below reads
        # application.applicant.username for every row.
        expiring = StickerApplication.objects.filter(
            status='issued',
            issued_at__lt=cutoff
        ).select_related('applicant')

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
                f'Issued on {application.issued_at:%Y-%m-%d}.',
                target_user=application.applicant.username,
                extra_data={
                    'application_id': application.pk,
                    'sticker_id': application.sticker_id,
                    'rfid_uid': application.rfid_uid,
                }
            )
            count += 1

        self.stdout.write(f'Expired {count} sticker(s).')
