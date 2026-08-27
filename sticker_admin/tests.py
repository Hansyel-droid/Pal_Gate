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
from applications.models import RegistrationWindow, StickerApplication
from appointments.models import Appointment, AppointmentSlot
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
            plate_number='QUE-001', official_receipt=make_doc(), vehicle_registration=make_doc(), drivers_license=make_doc(),
            status='approved', **common
        )
        # Must NOT appear — only approved applications belong at this counter.
        self.not_approved = StickerApplication.objects.create(
            full_name='Scheduled Person', id_number='2020-1001',
            plate_number='QUE-002', official_receipt=make_doc(), vehicle_registration=make_doc(), drivers_license=make_doc(),
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
            official_receipt=make_doc(), vehicle_registration=make_doc(),
            drivers_license=make_doc(),
            status='issued',
            rfid_uid='CLAIMED-UID-123',
            sticker_id='PalawanSU-AAAA0001',
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
            official_receipt=make_doc(), vehicle_registration=make_doc(),
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
        self.assertTrue(self.pending.sticker_id.startswith('PalawanSU-'))

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
            official_receipt=make_doc(), vehicle_registration=make_doc(),
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
class ApproveEndpointContractTests(TestCase):
    """
    The Approve button is now guarded by a confirmation dialog, which is
    entirely client-side. These pin the endpoint's side of that line: it
    still takes a bare POST with nothing but a CSRF token, and no part of
    the confirmation — no token, no acknowledgement field — is required
    server-side. A dialog that has to be paid for in the view would be a
    second place for approval rules to live.

    ApprovalRejectionEmailTests.test_approve_sends_email_and_logs_audit
    already covers the notification and audit side effects.
    """

    def setUp(self):
        self.admin = User.objects.create_user(
            username='contract_admin', password='pw-1234567', role='admin'
        )
        self.applicant = User.objects.create_user(
            username='contract_applicant', password='pw-1234567',
            role='applicant', email='contract@example.com',
        )
        self.application = StickerApplication.objects.create(
            applicant=self.applicant, full_name='Contract Tester',
            college_department='CCIS', id_number='2020-0030',
            classification='student', plate_number='CON-001',
            vehicle_type='four_wheels', vehicle_color='red', is_owner=True,
            official_receipt=make_doc(), vehicle_registration=make_doc(), drivers_license=make_doc(), status='scheduled',
        )
        self.url = reverse('approve_application', args=[self.application.pk])

    def test_bare_post_still_approves(self):
        """No confirmation field is invented as a server-side requirement."""
        self.client.force_login(self.admin)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.url)

        self.application.refresh_from_db()
        self.assertEqual(self.application.status, 'approved')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            AuditLog.objects.filter(action='app_approved').exists()
        )

    def test_get_does_not_approve(self):
        """The dialog is not the thing keeping a GET from mutating state."""
        self.client.force_login(self.admin)
        response = self.client.get(self.url)

        self.application.refresh_from_db()
        self.assertEqual(self.application.status, 'scheduled')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(AuditLog.objects.filter(action='app_approved').exists())

    def test_non_admin_cannot_approve(self):
        self.client.force_login(self.applicant)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.url)

        self.application.refresh_from_db()
        self.assertEqual(self.application.status, 'scheduled')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/dashboard/', response.url)
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(AuditLog.objects.filter(action='app_approved').exists())

    def test_approving_a_non_scheduled_application_is_refused(self):
        self.application.status = 'rejected'
        self.application.save()
        self.client.force_login(self.admin)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.url)

        self.application.refresh_from_db()
        self.assertEqual(self.application.status, 'rejected')
        self.assertEqual(len(mail.outbox), 0)

    def test_detail_page_wires_approve_to_the_confirmation_dialog(self):
        """
        The guard is markup, so it can be deleted by an unrelated template
        edit without any Python test noticing. This is the one assertion
        that the wiring is still present.
        """
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse('application_detail', args=[self.application.pk])
        )
        self.assertContains(response, 'data-confirm="approveConfirm"')
        self.assertContains(response, 'id="approveConfirm"')
        # The point of the dialog: it names the record being approved.
        self.assertContains(response, 'Contract Tester')
        self.assertContains(response, 'CON-001')


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class DestructiveActionContractTests(TestCase):
    """
    The four destructive admin actions that are now fronted by a confirmation
    dialog: delete-all-empty, delete-selected, per-row delete, and closing the
    registration window. The dialog is entirely client-side, so these pin the
    endpoints' side of that line — each still acts on a bare POST carrying
    only its `action` field, and none of them grew a server-side
    acknowledgement that the dialog would have to satisfy.

    No test covered any of these before, so this is new ground rather than a
    guard on existing coverage.
    """

    def setUp(self):
        self.admin = User.objects.create_user(
            username='destructive_admin', password='pw-1234567', role='admin'
        )
        self.applicant = User.objects.create_user(
            username='destructive_applicant', password='pw-1234567',
            role='applicant',
        )
        today = timezone.localdate()
        # Two free dates and one with a booking on it.
        self.free_a = AppointmentSlot.objects.create(date=today + timedelta(days=1))
        self.free_b = AppointmentSlot.objects.create(date=today + timedelta(days=2))
        self.booked = AppointmentSlot.objects.create(date=today + timedelta(days=3))

        application = StickerApplication.objects.create(
            applicant=self.applicant, full_name='Booked Person',
            college_department='CCIS', id_number='2020-7000',
            classification='student', plate_number='BKD-001',
            vehicle_type='four_wheels', vehicle_color='black', is_owner=True,
            official_receipt=make_doc(), vehicle_registration=make_doc(), drivers_license=make_doc(), status='scheduled',
        )
        Appointment.objects.create(
            application=application, slot=self.booked, time='09:00'
        )

        self.url = reverse('appointment_dates')
        self.client.force_login(self.admin)

    # ── Per-row delete ───────────────────────────────────────────────

    def test_row_delete_removes_only_that_date(self):
        response = self.client.post(
            self.url, data={'action': 'delete', 'slot_id': self.free_a.pk}
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AppointmentSlot.objects.filter(pk=self.free_a.pk).exists())
        self.assertTrue(AppointmentSlot.objects.filter(pk=self.free_b.pk).exists())
        self.assertTrue(
            AuditLog.objects.filter(action='slot_deleted').exists()
        )

    def test_row_delete_refuses_a_date_with_bookings(self):
        self.client.post(
            self.url, data={'action': 'delete', 'slot_id': self.booked.pk}
        )
        self.assertTrue(AppointmentSlot.objects.filter(pk=self.booked.pk).exists())

    def test_row_delete_ignores_a_get(self):
        self.client.get(self.url, data={'action': 'delete', 'slot_id': self.free_a.pk})
        self.assertTrue(AppointmentSlot.objects.filter(pk=self.free_a.pk).exists())

    # ── Delete selected ──────────────────────────────────────────────

    def test_delete_selected_removes_exactly_the_posted_ids(self):
        self.client.post(self.url, data={
            'action': 'delete_selected',
            'selected_slots': [self.free_a.pk],
        })
        self.assertFalse(AppointmentSlot.objects.filter(pk=self.free_a.pk).exists())
        self.assertTrue(AppointmentSlot.objects.filter(pk=self.free_b.pk).exists())

    def test_delete_selected_skips_dates_with_bookings(self):
        self.client.post(self.url, data={
            'action': 'delete_selected',
            'selected_slots': [self.free_a.pk, self.booked.pk],
        })
        self.assertFalse(AppointmentSlot.objects.filter(pk=self.free_a.pk).exists())
        self.assertTrue(AppointmentSlot.objects.filter(pk=self.booked.pk).exists())

    def test_delete_selected_with_nothing_selected_deletes_nothing(self):
        self.client.post(self.url, data={'action': 'delete_selected'})
        self.assertEqual(AppointmentSlot.objects.count(), 3)

    # ── Delete all empty ─────────────────────────────────────────────

    def test_delete_all_empty_keeps_dates_that_have_bookings(self):
        self.client.post(self.url, data={'action': 'delete_all_empty'})
        remaining = list(AppointmentSlot.objects.values_list('pk', flat=True))
        self.assertEqual(remaining, [self.booked.pk])

    def test_delete_all_empty_ignores_a_get(self):
        self.client.get(self.url, data={'action': 'delete_all_empty'})
        self.assertEqual(AppointmentSlot.objects.count(), 3)

    def test_empty_count_offered_to_the_template_matches_what_is_deleted(self):
        """
        The dialog names this number, so it has to be the number the action
        actually removes — not the total, and not the count of active dates.
        """
        response = self.client.get(self.url)
        promised = response.context['empty']

        self.client.post(self.url, data={'action': 'delete_all_empty'})
        actually_deleted = 3 - AppointmentSlot.objects.count()
        self.assertEqual(promised, 2)
        self.assertEqual(promised, actually_deleted)

    # ── Permissions ──────────────────────────────────────────────────

    def test_non_admin_cannot_delete_dates(self):
        self.client.force_login(self.applicant)
        response = self.client.post(
            self.url, data={'action': 'delete', 'slot_id': self.free_a.pk}
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/dashboard/', response.url)
        self.assertTrue(AppointmentSlot.objects.filter(pk=self.free_a.pk).exists())

    # ── Markup wiring ────────────────────────────────────────────────

    def test_date_page_wires_all_three_deletes_to_dialogs(self):
        """
        The guard is markup, so an unrelated template edit could drop it
        without any Python test noticing. This is the one assertion it is
        still attached — and that no plain confirm() came back on the three
        delete controls.
        """
        response = self.client.get(self.url)
        self.assertContains(response, 'data-confirm="deleteEmptyConfirm"')
        self.assertContains(response, 'data-confirm="deleteSelectedConfirm"')
        self.assertContains(response, 'data-confirm="deleteDateConfirm"')
        self.assertContains(response, 'id="deleteEmptyConfirm"')
        self.assertContains(response, 'id="deleteSelectedConfirm"')
        self.assertContains(response, 'id="deleteDateConfirm"')
        # The per-row dialog is shared, so each row must hand it its own date.
        self.assertContains(response, 'data-confirm-field-date=')

        # The three delete controls specifically no longer fall back to the
        # browser's popup. Asserted by their old text rather than by
        # "no onclick confirm anywhere on the page", because "Deactivate all
        # dates" still uses one deliberately — it was out of scope for this
        # change, and a blanket assertion here would quietly claim otherwise.
        self.assertNotContains(response, "confirm('Delete every date")
        self.assertNotContains(response, "confirm('Delete the selected dates?')")
        self.assertNotContains(response, f"confirm('Delete {self.free_a.date}?')")

    def test_deactivate_all_is_deliberately_still_unguarded(self):
        """
        Not a wish — a record of scope. "Deactivate all dates" is the one
        confirm() on this page that was not converted. If someone converts
        it later, this test failing is the prompt to delete it.
        """
        response = self.client.get(self.url)
        self.assertContains(response, "confirm('Deactivate all dates?")
        self.assertNotContains(response, 'data-confirm="deactivateAllConfirm"')


class RegistrationWindowContractTests(TestCase):
    """
    Closing the registration window is now fronted by a confirmation dialog.
    The endpoint behind it is unchanged: a bare POST with action=close still
    closes the window and writes the audit entry.
    """

    def setUp(self):
        self.admin = User.objects.create_user(
            username='window_admin', password='pw-1234567', role='admin'
        )
        today = timezone.localdate()
        self.window = RegistrationWindow.objects.create(
            start_date=today - timedelta(days=1),
            end_date=today + timedelta(days=14),
            is_active=True,
        )
        self.url = reverse('registration_window')
        self.client.force_login(self.admin)

    def test_bare_post_still_closes_the_window(self):
        response = self.client.post(self.url, data={'action': 'close'})
        self.window.refresh_from_db()
        self.assertFalse(self.window.is_active)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(AuditLog.objects.filter(action='window_closed').exists())

    def test_get_does_not_close_the_window(self):
        self.client.get(self.url, data={'action': 'close'})
        self.window.refresh_from_db()
        self.assertTrue(self.window.is_active)
        self.assertFalse(AuditLog.objects.filter(action='window_closed').exists())

    def test_non_admin_cannot_close_the_window(self):
        applicant = User.objects.create_user(
            username='window_applicant', password='pw-1234567', role='applicant'
        )
        self.client.force_login(applicant)
        response = self.client.post(self.url, data={'action': 'close'})
        self.window.refresh_from_db()
        self.assertTrue(self.window.is_active)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/dashboard/', response.url)

    def test_open_window_page_wires_close_to_the_dialog(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'data-confirm="closeWindowConfirm"')
        self.assertContains(response, 'id="closeWindowConfirm"')
        self.assertNotContains(response, 'onclick="return confirm(')

    def test_no_dialog_is_rendered_when_no_window_is_open(self):
        """The button it guards isn't on the page either."""
        self.window.is_active = False
        self.window.save()
        response = self.client.get(self.url)
        self.assertNotContains(response, 'id="closeWindowConfirm"')


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
            official_receipt=make_doc(), vehicle_registration=make_doc(), drivers_license=make_doc(), status='approved',
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
