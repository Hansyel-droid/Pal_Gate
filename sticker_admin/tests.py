import tempfile
from datetime import timedelta

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from applications.models import StickerApplication
from appointments.models import AppointmentSlot
from gate.models import AuditLog, PendingRFID


def make_doc(name='doc.pdf'):
    return SimpleUploadedFile(name, b'%PDF-1.4 test document content', content_type='application/pdf')


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class StickerStationQueueTests(TestCase):
    """
    sticker_admin.views.sticker_station lists everyone approved and waiting
    by default — staff at the counter shouldn't have to already know a name
    to see who's in the queue. Search narrows that same list rather than
    being the only way to see anything.
    """

    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin1', password='pw-1234567', role='admin'
        )
        applicant = User.objects.create_user(
            username='queued', password='pw-1234567', role='applicant'
        )
        common = dict(
            applicant=applicant, college_department='CCIS',
            classification='student', vehicle_type='four_wheels',
            vehicle_color='black', is_owner=True,
        )
        self.approved = StickerApplication.objects.create(
            full_name='Approved Person', id_number='2020-1000',
            plate_number='QUE-001', or_cr=make_doc(), drivers_license=make_doc(),
            status='approved', **common
        )
        # Must NOT appear — only approved applications belong at this counter.
        self.not_approved = StickerApplication.objects.create(
            full_name='Scheduled Person', id_number='2020-1001',
            plate_number='QUE-002', or_cr=make_doc(), drivers_license=make_doc(),
            status='scheduled', **common
        )
        self.client.force_login(self.admin)

    def test_approved_applicants_are_listed_without_searching(self):
        response = self.client.get(reverse('sticker_station'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Approved Person')
        self.assertEqual(response.context['total_waiting'], 1)

    def test_non_approved_applications_are_not_listed(self):
        response = self.client.get(reverse('sticker_station'))
        self.assertNotContains(response, 'Scheduled Person')

    def test_search_narrows_the_same_list(self):
        response = self.client.get(reverse('sticker_station'), {'q': 'QUE-001'})
        self.assertContains(response, 'Approved Person')

        miss = self.client.get(reverse('sticker_station'), {'q': 'nobody-by-that-name'})
        self.assertNotContains(miss, 'Approved Person')
        self.assertContains(miss, 'No approved applicants match')

    def test_issued_application_leaves_the_queue(self):
        self.approved.status = 'issued'
        self.approved.save()
        response = self.client.get(reverse('sticker_station'))
        self.assertNotContains(response, 'Approved Person')
        self.assertEqual(response.context['total_waiting'], 0)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class RfidDoubleIssuanceTests(TestCase):
    """
    sticker_admin.views.issue_sticker: an RFID tag must never be bound to
    two applications. The view guards this two ways — an explicit
    pre-check and a select_for_update() lock inside the same transaction
    (see the comment in issue_sticker) — this test exercises the
    user-visible outcome of that guard: the second issuance attempt for an
    already-claimed tag must be rejected and must not change anything.
    """

    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin1', password='pw-1234567', role='admin'
        )
        applicant1 = User.objects.create_user(
            username='applicant1', password='pw-1234567', role='applicant'
        )
        applicant2 = User.objects.create_user(
            username='applicant2', password='pw-1234567', role='applicant'
        )
        self.already_issued = StickerApplication.objects.create(
            applicant=applicant1,
            full_name='Already Issued',
            college_department='CCIS',
            id_number='2020-0010',
            classification='student',
            plate_number='ISS-001',
            vehicle_type='four_wheels',
            vehicle_color='black',
            is_owner=True,
            or_cr=make_doc(),
            drivers_license=make_doc(),
            status='issued',
            rfid_uid='CLAIMED-UID-123',
            sticker_id='PalSU-AAAA0001',
        )
        self.pending = StickerApplication.objects.create(
            applicant=applicant2,
            full_name='Pending Applicant',
            college_department='CCIS',
            id_number='2020-0011',
            classification='student',
            plate_number='ISS-002',
            vehicle_type='four_wheels',
            vehicle_color='silver',
            is_owner=True,
            or_cr=make_doc(),
            drivers_license=make_doc(),
            status='approved',
        )

    def test_issuing_already_claimed_rfid_is_rejected(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('issue_sticker', args=[self.pending.pk]),
            data={'rfid_uid': 'CLAIMED-UID-123'},
        )
        # Rejected -> bounced back to the issue form, not the detail page.
        self.assertRedirects(
            response, reverse('issue_sticker', args=[self.pending.pk])
        )
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, 'approved')
        self.assertFalse(self.pending.rfid_uid)

    def test_issuing_a_free_rfid_succeeds(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('issue_sticker', args=[self.pending.pk]),
            data={'rfid_uid': 'FREE-UID-456'},
        )
        self.assertRedirects(
            response, reverse('application_detail', args=[self.pending.pk])
        )
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, 'issued')
        self.assertEqual(self.pending.rfid_uid, 'FREE-UID-456')
        self.assertTrue(self.pending.sticker_id.startswith('PalSU-'))

    def test_issuing_retires_the_pending_scan(self):
        # The registration scanner's row for this tag has to stop being
        # offered once the tag is actually bound to an application.
        # Nothing used to mark it claimed, so the issuing station kept
        # auto-filling a UID that was already spoken for.
        scan = PendingRFID.objects.create(uid='FREE-UID-456', claimed=False)
        self.client.force_login(self.admin)

        self.client.post(
            reverse('issue_sticker', args=[self.pending.pk]),
            data={'rfid_uid': 'FREE-UID-456'},
        )

        scan.refresh_from_db()
        self.assertTrue(scan.claimed)
        self.assertIsNone(PendingRFID.latest_offerable())

    def test_rejected_issuance_leaves_the_pending_scan_alone(self):
        # The claim happens inside issue_sticker's transaction, so an
        # issuance that bounces must not burn the scan — staff need to be
        # able to retry with the same tag on their next attempt.
        scan = PendingRFID.objects.create(uid='CLAIMED-UID-123', claimed=False)
        self.client.force_login(self.admin)

        self.client.post(
            reverse('issue_sticker', args=[self.pending.pk]),
            data={'rfid_uid': 'CLAIMED-UID-123'},
        )

        scan.refresh_from_db()
        self.assertFalse(scan.claimed)

    def test_only_admin_can_issue_stickers(self):
        applicant = User.objects.create_user(
            username='notadmin', password='pw-1234567', role='applicant'
        )
        self.client.force_login(applicant)
        response = self.client.get(
            reverse('issue_sticker', args=[self.pending.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/dashboard/', response.url)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ApprovalRejectionEmailTests(TestCase):
    """
    approve_application / reject_application notify the applicant and
    write an audit entry — this covers both side effects together since
    they're meant to happen atomically with the status change.
    """

    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin1', password='pw-1234567', role='admin'
        )
        self.applicant = User.objects.create_user(
            username='applicant1', password='pw-1234567', role='applicant',
            email='applicant1@example.com',
        )
        self.application = StickerApplication.objects.create(
            applicant=self.applicant,
            full_name='Approval Tester',
            college_department='CCIS',
            id_number='2020-0020',
            classification='student',
            plate_number='APR-001',
            vehicle_type='four_wheels',
            vehicle_color='green',
            is_owner=True,
            or_cr=make_doc(),
            drivers_license=make_doc(),
            status='scheduled',
        )

    def test_approve_sends_email_and_logs_audit(self):
        self.client.force_login(self.admin)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse('approve_application', args=[self.application.pk])
            )
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, 'approved')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.applicant.email, mail.outbox[0].to)
        self.assertTrue(
            AuditLog.objects.filter(action='app_approved').exists()
        )

    def test_reject_requires_a_reason(self):
        self.client.force_login(self.admin)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse('reject_application', args=[self.application.pk]),
                data={'rejection_reason': ''},
            )
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, 'scheduled')  # unchanged
        self.assertEqual(len(mail.outbox), 0)

    def test_reject_with_reason_sends_email_and_logs_audit(self):
        self.client.force_login(self.admin)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse('reject_application', args=[self.application.pk]),
                data={'rejection_reason': 'Blurry OR/CR scan.'},
            )
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, 'rejected')
        self.assertEqual(self.application.rejection_reason, 'Blurry OR/CR scan.')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Blurry OR/CR scan.', mail.outbox[0].body)
        self.assertTrue(
            AuditLog.objects.filter(action='app_rejected').exists()
        )


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ApplicationDetailQueryShapeTests(TestCase):
    """
    application_detail renders slot.slots_remaining for every open date in
    its reassignment dropdown. Without the booked-count annotation that's
    one COUNT per date, so opening a term's worth of dates makes the page
    linearly more expensive to view.
    """

    def setUp(self):
        self.admin = User.objects.create_user(
            username='detail_admin', password='pw-1234567', role='admin'
        )
        applicant = User.objects.create_user(
            username='detail_applicant', password='pw-1234567', role='applicant'
        )
        self.application = StickerApplication.objects.create(
            applicant=applicant, full_name='Detail Person',
            college_department='CCIS', id_number='2020-9000',
            classification='student', plate_number='DET-001',
            vehicle_type='four_wheels', vehicle_color='blue', is_owner=True,
            or_cr=make_doc(), drivers_license=make_doc(), status='approved',
        )
        self.client.force_login(self.admin)
        self.url = reverse('application_detail', args=[self.application.pk])

    def open_dates(self, count):
        AppointmentSlot.objects.all().delete()
        today = timezone.localdate()
        for i in range(count):
            AppointmentSlot.objects.create(date=today + timedelta(days=i + 1))

    def count_queries(self, dates):
        self.open_dates(dates)
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        return len(ctx.captured_queries)

    def test_query_count_does_not_grow_with_open_dates(self):
        few = self.count_queries(5)
        many = self.count_queries(60)
        self.assertEqual(
            few, many,
            f'{few} queries at 5 open dates but {many} at 60 — the slot '
            f'list is still counting bookings one row at a time',
        )
