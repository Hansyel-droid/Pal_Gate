import tempfile
from datetime import timedelta

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from appointments.models import AppointmentSlot, Appointment
from gate.models import AuditLog
from .models import RegistrationWindow, StickerApplication
from .notifications import notify_approved


def make_doc(name='doc.pdf'):
    return SimpleUploadedFile(name, b'%PDF-1.4 test document content', content_type='application/pdf')


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ServeDocumentOwnershipTests(TestCase):
    """
    applications.views.serve_document is the only thing standing between a
    stranger and someone else's driver's license / OR/CR. This is the
    single most sensitive access-control check in the app.
    """

    def setUp(self):
        self.owner = User.objects.create_user(
            username='owner', password='pw-1234567', role='applicant'
        )
        self.other_applicant = User.objects.create_user(
            username='intruder', password='pw-1234567', role='applicant'
        )
        self.admin = User.objects.create_user(
            username='admin1', password='pw-1234567', role='admin'
        )
        self.security = User.objects.create_user(
            username='security1', password='pw-1234567', role='security'
        )
        self.application = StickerApplication.objects.create(
            applicant=self.owner,
            full_name='Owner Person',
            college_department='CCIS',
            id_number='2020-0001',
            classification='student',
            plate_number='OWN-001',
            vehicle_type='four_wheels',
            vehicle_color='blue',
            is_owner=True,
            or_cr=make_doc('or_cr.pdf'),
            drivers_license=make_doc('license.pdf'),
            cor=make_doc('cor.pdf'),
            status='draft',
        )
        self.url = reverse(
            'serve_document', args=[self.application.pk, 'or_cr']
        )

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_owner_can_view_own_document(self):
        self.client.force_login(self.owner)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_other_applicant_forbidden(self):
        self.client.force_login(self.other_applicant)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_security_role_forbidden(self):
        # Security officers review gate activity, not applicant documents —
        # only admins and the owning applicant may see these.
        self.client.force_login(self.security)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_admin_can_view_any_document(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_unknown_field_name_404s(self):
        self.client.force_login(self.owner)
        url = reverse('serve_document', args=[self.application.pk, 'rfid_uid'])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class PlateUniquenessConstraintTests(TestCase):
    """
    unique_active_plate_number (applications.models.StickerApplication.Meta)
    must block two live applications for the same plate, but must NOT block
    reuse of a plate that a rejected/expired application let go of.
    """

    def setUp(self):
        self.applicant = User.objects.create_user(
            username='plateowner', password='pw-1234567', role='applicant'
        )

    def _make(self, plate, status):
        return StickerApplication.objects.create(
            applicant=self.applicant,
            full_name='Plate Tester',
            college_department='CCIS',
            id_number='2020-0002',
            classification='faculty',
            plate_number=plate,
            vehicle_type='four_wheels',
            vehicle_color='red',
            is_owner=True,
            or_cr=make_doc(),
            drivers_license=make_doc(),
            status=status,
        )

    def test_duplicate_active_plate_is_rejected(self):
        self._make('DUP-001', 'approved')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._make('DUP-001', 'draft')

    def test_plate_reusable_after_rejection(self):
        first = self._make('DUP-002', 'scheduled')
        first.status = 'rejected'
        first.save()
        # Should not raise — a rejected application no longer holds the plate.
        second = self._make('DUP-002', 'draft')
        self.assertEqual(second.plate_number, 'DUP-002')

    def test_plate_reusable_after_expiry(self):
        first = self._make('DUP-003', 'issued')
        first.status = 'expired'
        first.save()
        second = self._make('DUP-003', 'draft')
        self.assertEqual(second.plate_number, 'DUP-003')


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class RenewalFlowTests(TestCase):
    """
    applications.views.apply_renew + the untouched apply_step3/apply_step4
    it hands off to. Covers the whole "lighter renewal" path end-to-end:
    pre-filled session, reused documents, applicant-chosen appointment, and
    an audit trail that records this as a renewal.
    """

    def setUp(self):
        self.applicant = User.objects.create_user(
            username='renewer', password='pw-1234567', role='applicant',
            email='renewer@example.com',
        )
        today = timezone.localdate()
        self.window = RegistrationWindow.objects.create(
            start_date=today, end_date=today + timedelta(days=5), is_active=True
        )
        self.slot = AppointmentSlot.objects.create(
            date=self.window.end_date + timedelta(days=1),
            is_active=True,
            capacity=20,
        )
        self.expired_application = StickerApplication.objects.create(
            applicant=self.applicant,
            full_name='Renewer Person',
            college_department='CCIS',
            id_number='2020-0003',
            classification='faculty',
            plate_number='REN-001',
            vehicle_type='four_wheels',
            vehicle_color='white',
            is_owner=True,
            or_cr=make_doc('old_or_cr.pdf'),
            drivers_license=make_doc('old_license.pdf'),
            status='expired',
            issued_at=timezone.now() - timedelta(days=400),
        )

    def test_non_expired_application_cannot_be_renewed(self):
        self.expired_application.status = 'issued'
        self.expired_application.save()
        self.client.force_login(self.applicant)
        response = self.client.get(
            reverse('apply_renew', args=[self.expired_application.pk]), follow=True
        )
        self.assertRedirects(response, reverse('my_applications'))
        self.assertEqual(StickerApplication.objects.count(), 1)

    def test_cannot_renew_someone_elses_application(self):
        stranger = User.objects.create_user(
            username='stranger', password='pw-1234567', role='applicant'
        )
        self.client.force_login(stranger)
        response = self.client.get(
            reverse('apply_renew', args=[self.expired_application.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_renew_prefills_session_and_reaches_step3(self):
        self.client.force_login(self.applicant)
        response = self.client.get(
            reverse('apply_renew', args=[self.expired_application.pk])
        )
        self.assertRedirects(response, reverse('apply_step3'))

        session = self.client.session
        self.assertEqual(session['app_step1']['full_name'], 'Renewer Person')
        self.assertEqual(session['app_step2']['plate_number'], 'REN-001')
        self.assertIsNotNone(session['app_temp_files']['or_cr'])
        self.assertIsNone(session['app_temp_files']['cor'])
        self.assertEqual(session['app_renewal_of'], self.expired_application.pk)

    def test_renewal_submission_creates_new_application_and_notifies(self):
        self.client.force_login(self.applicant)
        self.client.get(reverse('apply_renew', args=[self.expired_application.pk]))

        # Renewal still needs a fresh appointment — choose one before review.
        choose_response = self.client.post(reverse('apply_step3'), data={
            'slot_id': self.slot.pk,
            'time': '08:00',
        })
        self.assertRedirects(choose_response, reverse('apply_step4'))

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('apply_step4'), data={})

        self.assertRedirects(response, reverse('my_applications'))

        # A brand-new row, not a mutation of the expired one.
        self.assertEqual(
            StickerApplication.objects.filter(plate_number='REN-001').count(), 2
        )
        new_application = StickerApplication.objects.filter(
            plate_number='REN-001'
        ).exclude(pk=self.expired_application.pk).get()
        self.assertEqual(new_application.status, 'scheduled')
        self.assertTrue(new_application.or_cr.name)

        self.expired_application.refresh_from_db()
        self.assertEqual(self.expired_application.status, 'expired')

        audit_entry = AuditLog.objects.filter(action='app_submitted').latest('timestamp')
        self.assertEqual(
            audit_entry.extra_data.get('renewed_from'), self.expired_application.pk
        )

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('appointment', mail.outbox[0].subject.lower())
        self.assertIn(self.applicant.email, mail.outbox[0].to)

    def test_choosing_a_full_time_bounces_back_to_the_picker(self):
        # Fill 8:00 AM to capacity (20) with other applicants' appointments.
        for i in range(20):
            filler_applicant = User.objects.create_user(
                username=f'filler{i}', password='pw-1234567', role='applicant'
            )
            filler_app = StickerApplication.objects.create(
                applicant=filler_applicant,
                full_name='Filler',
                college_department='CCIS',
                id_number=f'2020-fill-{i}',
                classification='student',
                plate_number=f'FILL-{i:03d}',
                vehicle_type='four_wheels',
                vehicle_color='black',
                is_owner=True,
                or_cr=make_doc(),
                drivers_license=make_doc(),
                status='scheduled',
            )
            Appointment.objects.create(application=filler_app, slot=self.slot, time='08:00')

        self.client.force_login(self.applicant)
        self.client.get(reverse('apply_renew', args=[self.expired_application.pk]))

        response = self.client.post(reverse('apply_step3'), data={
            'slot_id': self.slot.pk,
            'time': '08:00',
        })

        # Bounced back to the picker (pre-loaded on this date), not
        # forwarded to review — and nothing was saved to session.
        self.assertRedirects(
            response, f"{reverse('apply_step3')}?date={self.slot.date.isoformat()}"
        )
        self.assertNotIn('app_appointment', self.client.session)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class AppointmentPickerRenderingTests(TestCase):
    """
    The GET branches of apply_step3 (date list, then time list once a date
    is chosen) and apply_step4 (review) — none of these are exercised by
    RenewalFlowTests, which only follows the POST/redirect path, so a
    template bug here (bad variable name, missing context key, etc.) could
    slip through even with that test passing.
    """

    def setUp(self):
        self.applicant = User.objects.create_user(
            username='picker', password='pw-1234567', role='applicant'
        )
        today = timezone.localdate()
        self.window = RegistrationWindow.objects.create(
            start_date=today, end_date=today, is_active=True
        )
        self.slot = AppointmentSlot.objects.create(
            date=today + timedelta(days=1), is_active=True, capacity=5,
        )
        self.client.force_login(self.applicant)
        session = self.client.session
        session['app_step1'] = {
            'full_name': 'Picker Person', 'college_department': 'CCIS',
            'id_number': '2020-0050', 'classification': 'student',
        }
        session['app_step2'] = {
            'plate_number': 'PICK-001', 'vehicle_type': 'four_wheels',
            'vehicle_color': 'blue', 'is_owner': 'yes',
        }
        session.save()

    def test_step3_lists_dates_without_a_date_selected(self):
        response = self.client.get(reverse('apply_step3'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.slot.date.strftime('%B'))

    def test_step3_lists_times_once_a_date_is_selected(self):
        response = self.client.get(
            f"{reverse('apply_step3')}?date={self.slot.date.isoformat()}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '8:00 AM')
        self.assertContains(response, '5 left')

    def test_step4_renders_after_choosing_an_appointment(self):
        self.client.post(reverse('apply_step3'), data={
            'slot_id': self.slot.pk, 'time': '09:00',
        })
        response = self.client.get(reverse('apply_step4'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '9:00 AM')
        self.assertContains(response, 'PICK-001')

    def test_step4_redirects_to_picker_if_no_appointment_chosen_yet(self):
        response = self.client.get(reverse('apply_step4'))
        self.assertRedirects(response, reverse('apply_step3'))


class NotificationHelperTests(TestCase):
    """applications.notifications — the only channel that ever reaches an
    applicant, so it must never raise and must never notify a walk-in
    account that has no address to notify."""

    def test_no_op_when_applicant_has_no_email(self):
        applicant = User.objects.create_user(
            username='walkin', password='pw-1234567', role='applicant'
        )
        applicant.email = ''
        applicant.save()
        application = StickerApplication(applicant=applicant, full_name='No Email')
        sent = notify_approved(application)
        self.assertIsNone(sent)  # notify_* wrappers don't propagate a value
        self.assertEqual(len(mail.outbox), 0)
