import tempfile
from datetime import timedelta

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.forms import RegisterForm
from accounts.models import COLLEGE_CHOICES, PolicyAcceptance, User
from accounts.policy import CAMPUS_POLICY_VERSION
from appointments.models import AppointmentSlot, Appointment
from gate.models import AuditLog
from .models import RegistrationWindow, StickerApplication
from .notifications import notify_approved


def make_doc(name='doc.pdf'):
    return SimpleUploadedFile(name, b'%PDF-1.4 test document content', content_type='application/pdf')


def accept_policy(user):
    """
    CampusPolicyMiddleware redirects any applicant who hasn't accepted the
    current policy version to the policy page, ahead of every other view —
    including apply/ and serve_document. Only needed for applicant users
    that actually get force_login'd and make a request; users only used as
    foreign-key owners of test data don't need this.
    """
    PolicyAcceptance.objects.create(user=user, version=CAMPUS_POLICY_VERSION)


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
        accept_policy(self.owner)
        self.other_applicant = User.objects.create_user(
            username='intruder', password='pw-1234567', role='applicant'
        )
        accept_policy(self.other_applicant)
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
            official_receipt=make_doc('or.pdf'),
            vehicle_registration=make_doc('cr.pdf'),
            drivers_license=make_doc('license.pdf'),
            cor=make_doc('cor.pdf'),
            status='draft',
        )
        self.url = reverse(
            'serve_document', args=[self.application.pk, 'official_receipt']
        )
        self.cr_url = reverse(
            'serve_document', args=[self.application.pk, 'vehicle_registration']
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

    def test_the_cr_is_served_under_its_own_name(self):
        """
        The two documents are separately addressable, and each URL serves
        its own file — not one shared blob behind two names.
        """
        self.client.force_login(self.owner)
        or_response = self.client.get(self.url)
        cr_response = self.client.get(self.cr_url)
        self.assertEqual(cr_response.status_code, 200)
        self.assertEqual(
            or_response.filename, self.application.official_receipt.name.rsplit('/', 1)[-1]
        )
        self.assertEqual(
            cr_response.filename, self.application.vehicle_registration.name.rsplit('/', 1)[-1]
        )
        self.assertNotEqual(or_response.filename, cr_response.filename)

    def test_the_retired_or_cr_name_is_no_longer_a_document_field(self):
        """The field is gone; its URL must 404 rather than serve something."""
        self.client.force_login(self.owner)
        url = reverse('serve_document', args=[self.application.pk, 'or_cr'])
        self.assertEqual(self.client.get(url).status_code, 404)


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
            official_receipt=make_doc(), vehicle_registration=make_doc(),
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
        accept_policy(self.applicant)
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
            official_receipt=make_doc('old_or.pdf'),
            vehicle_registration=make_doc('old_cr.pdf'),
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
        accept_policy(stranger)
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
        # Both vehicle documents are carried over, separately.
        self.assertIsNotNone(session['app_temp_files']['official_receipt'])
        self.assertIsNotNone(session['app_temp_files']['vehicle_registration'])
        self.assertNotEqual(
            session['app_temp_files']['official_receipt']['path'],
            session['app_temp_files']['vehicle_registration']['path'],
        )
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
        self.assertTrue(new_application.official_receipt.name)
        self.assertTrue(new_application.vehicle_registration.name)

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
                official_receipt=make_doc(), vehicle_registration=make_doc(),
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
        accept_policy(self.applicant)
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


class CollegeChoiceTests(TestCase):
    """
    College is a fixed list, not free text. Reviewers filter and report on
    this column, and while it was a text input the same college arrived as
    "CCIS", "CS" and "College of Sciences" — three values, one place.
    """

    def setUp(self):
        self.applicant = User.objects.create_user(
            username='chooser', password='pw-1234567', role='applicant',
            college_department='College of Engineering',
            id_number='2020-0060', classification='student',
            first_name='Cho', last_name='Oser',
        )
        accept_policy(self.applicant)
        today = timezone.localdate()
        RegistrationWindow.objects.create(
            start_date=today, end_date=today, is_active=True
        )
        self.client.force_login(self.applicant)

    def base_post(self, college):
        return {
            'full_name': 'Cho Oser', 'college_department': college,
            'id_number': '2020-0060', 'classification': 'student',
        }

    def test_step1_renders_every_college_as_an_option(self):
        response = self.client.get(reverse('apply_step1'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<select', html=False)
        for college, _ in COLLEGE_CHOICES:
            self.assertContains(response, college)

    def test_step1_accepts_a_college_from_the_list(self):
        response = self.client.post(
            reverse('apply_step1'),
            self.base_post('College of Nursing and Health Sciences'),
        )
        self.assertRedirects(response, reverse('apply_step2'))
        self.assertEqual(
            self.client.session['app_step1']['college_department'],
            'College of Nursing and Health Sciences',
        )

    def test_step1_rejects_a_college_that_is_not_on_the_list(self):
        response = self.client.post(reverse('apply_step1'), self.base_post('CCIS'))
        # Re-rendered with the error rather than redirected onwards, and
        # nothing was written to the session.
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('app_step1', self.client.session)

    def test_register_form_rejects_a_college_that_is_not_on_the_list(self):
        form = RegisterForm({
            'username': 'newbie', 'first_name': 'New', 'last_name': 'Bie',
            'email': '202399999@psu.palawan.edu.ph',
            'college_department': 'Some Made-Up College',
            'password1': 'pw-1234567', 'password2': 'pw-1234567',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('college_department', form.errors)

    def test_register_form_leaves_college_optional(self):
        form = RegisterForm({
            'username': 'newbie', 'first_name': 'New', 'last_name': 'Bie',
            'email': '202399999@psu.palawan.edu.ph',
            'college_department': '',
            'password1': 'pw-1234567', 'password2': 'pw-1234567',
        })
        self.assertTrue(form.is_valid(), form.errors)


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
