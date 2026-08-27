"""
Regression tests for the application wizard's temp-file handling.

Each test here pins a bug that shipped: the cleanup command that never
deleted anything, Step 2 silently discarding a returning applicant's work,
temp paths keyed on a user-controlled username, and a document row whose
file has gone missing 500ing instead of 404ing.
"""
import tempfile
from datetime import timedelta

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import PolicyAcceptance, User
from accounts.policy import CAMPUS_POLICY_VERSION
from .models import RegistrationWindow, StickerApplication
from .forms import ApplicationStep2Form

MEDIA = tempfile.mkdtemp()


def accept_policy(user):
    """
    CampusPolicyMiddleware redirects any applicant who hasn't accepted the
    current policy version to the policy page, ahead of every other view —
    including the whole apply/ wizard these tests exercise. Without this,
    every test below would hit that redirect instead of the page it means
    to test.
    """
    PolicyAcceptance.objects.create(user=user, version=CAMPUS_POLICY_VERSION)


def doc(name='doc.pdf'):
    return SimpleUploadedFile(name, b'%PDF-1.4 test', content_type='application/pdf')


def png(name='doc.png'):
    return SimpleUploadedFile(
        name, b'\x89PNG\r\n\x1a\n' + b'0' * 32, content_type='image/png'
    )


@override_settings(MEDIA_ROOT=MEDIA)
class CleanupCommandTests(TestCase):
    def test_deletes_old_temp_files(self):
        default_storage.save('temp_uploads/7/official_receipt', ContentFile(b'old'))
        self.assertTrue(default_storage.exists('temp_uploads/7/official_receipt'))

        # Backdate it well past the 24h cutoff.
        import os
        p = default_storage.path('temp_uploads/7/official_receipt')
        old = (timezone.now() - timedelta(hours=48)).timestamp()
        os.utime(p, (old, old))

        call_command('cleanup_temp_files')
        self.assertFalse(
            default_storage.exists('temp_uploads/7/official_receipt'),
            'cleanup_temp_files did not delete an expired temp file',
        )

    def test_keeps_fresh_temp_files(self):
        default_storage.save('temp_uploads/8/official_receipt', ContentFile(b'new'))
        call_command('cleanup_temp_files')
        self.assertTrue(default_storage.exists('temp_uploads/8/official_receipt'))


@override_settings(MEDIA_ROOT=MEDIA)
class Step2RevisitTests(TestCase):
    def setUp(self):
        today = timezone.localdate()
        RegistrationWindow.objects.create(
            is_active=True,
            start_date=today - timedelta(days=1),
            end_date=today + timedelta(days=30),
        )
        self.user = User.objects.create_user(
            username='stu', password='pw-1234567', role='applicant',
            classification='student',
        )
        accept_policy(self.user)
        self.client.force_login(self.user)
        s = self.client.session
        s['app_step1'] = {
            'full_name': 'Stu Dent', 'college_department': 'CCIS',
            'id_number': '2020-1', 'classification': 'student',
        }
        s.save()

    def post_step2(self, **extra):
        data = {
            'plate_number': 'ABC 123', 'vehicle_type': 'four_wheels',
            'vehicle_color': 'blue', 'is_owner': 'yes',
            'official_receipt': doc('or.pdf'),
            'vehicle_registration': doc('cr.pdf'),
            'drivers_license': doc('dl.pdf'),
            'cor': doc('cor.pdf'),
        }
        data.update(extra)
        return self.client.post(reverse('apply_step2'), data)

    def test_first_pass_stores_everything(self):
        r = self.post_step2()
        self.assertRedirects(r, reverse('apply_step3'))
        files = self.client.session['app_temp_files']
        self.assertEqual(files['official_receipt']['original_name'], 'or.pdf')
        # Path keyed on pk, not username, and carries no extension.
        self.assertEqual(
            files['official_receipt']['path'],
            f'temp_uploads/{self.user.pk}/official_receipt',
        )
        # The CR is stored separately, under its own name — the whole point
        # of the split is that these two never share a slot.
        self.assertEqual(files['vehicle_registration']['original_name'], 'cr.pdf')
        self.assertEqual(
            files['vehicle_registration']['path'],
            f'temp_uploads/{self.user.pk}/vehicle_registration',
        )
        self.assertNotEqual(
            files['official_receipt']['path'],
            files['vehicle_registration']['path'],
        )

    def test_get_seeds_from_session(self):
        self.post_step2()
        r = self.client.get(reverse('apply_step2'))
        self.assertContains(r, 'ABC 123')
        self.assertContains(r, 'On file: or.pdf')
        self.assertContains(r, 'On file: cr.pdf')

    def test_revisit_without_reuploading_documents(self):
        self.post_step2()
        before = self.client.session['app_temp_files']

        r = self.client.post(reverse('apply_step2'), {
            'plate_number': 'XYZ 999', 'vehicle_type': 'four_wheels',
            'vehicle_color': 'red', 'is_owner': 'yes',
        })
        self.assertRedirects(r, reverse('apply_step3'))
        self.assertEqual(self.client.session['app_step2']['plate_number'], 'XYZ 999')
        after = self.client.session['app_temp_files']
        self.assertEqual(after['official_receipt'], before['official_receipt'])
        self.assertEqual(after['vehicle_registration'], before['vehicle_registration'])
        self.assertEqual(after['drivers_license'], before['drivers_license'])
        self.assertEqual(after['cor'], before['cor'])

    def test_reupload_different_extension_replaces(self):
        self.post_step2()
        first = self.client.session['app_temp_files']['official_receipt']['path']
        self.post_step2(official_receipt=png('or.png'))
        second = self.client.session['app_temp_files']
        self.assertEqual(
            second['official_receipt']['path'], first, 'orphaned the old temp file'
        )
        self.assertEqual(second['official_receipt']['original_name'], 'or.png')
        _, files = default_storage.listdir(f'temp_uploads/{self.user.pk}')
        self.assertEqual(
            sorted(files),
            ['cor', 'drivers_license', 'official_receipt', 'vehicle_registration'],
        )

    def test_replacing_the_or_leaves_the_cr_alone(self):
        """
        The two documents share a code path but must not share a file. Before
        the split there was only one slot, so this could not be asked.
        """
        self.post_step2()
        cr_before = self.client.session['app_temp_files']['vehicle_registration']

        self.post_step2(official_receipt=png('new-or.png'))

        after = self.client.session['app_temp_files']
        self.assertEqual(after['official_receipt']['original_name'], 'new-or.png')
        self.assertEqual(after['vehicle_registration'], cr_before)

    def test_dotdot_username_does_not_crash(self):
        dots = User.objects.create_user(
            username='..', password='pw-1234567', role='applicant',
            classification='faculty',
        )
        accept_policy(dots)
        self.client.force_login(dots)
        s = self.client.session
        s['app_step1'] = {
            'full_name': 'Dot Dot', 'college_department': 'CCIS',
            'id_number': '2020-2', 'classification': 'faculty',
        }
        s.save()
        r = self.client.post(reverse('apply_step2'), {
            'plate_number': 'DOT 001', 'vehicle_type': 'four_wheels',
            'vehicle_color': 'blue', 'is_owner': 'yes',
            'official_receipt': doc(), 'vehicle_registration': doc(),
            'drivers_license': doc('dl.pdf'),
        })
        self.assertRedirects(r, reverse('apply_step3'))

    def test_submission_round_trips_file_contents(self):
        """
        Documents are streamed from temp storage onto the model rather than
        read into memory. The bytes still have to arrive intact, and the
        extension has to survive — the model randomises the stored filename,
        so the original name is only ever carried for its suffix.
        """
        from appointments.models import AppointmentSlot

        # Inspections open the day after the registration window closes,
        # which setUp put 30 days out.
        slot = AppointmentSlot.objects.create(
            date=timezone.localdate() + timedelta(days=40)
        )
        body = b'%PDF-1.4 ' + b'payload-' * 5000
        self.post_step2(
            official_receipt=SimpleUploadedFile('my or.pdf', body,
                                                content_type='application/pdf')
        )
        self.client.post(reverse('apply_step3'), {
            'slot_id': slot.pk, 'time': '08:00',
        })
        r = self.client.post(reverse('apply_step4'), {})
        self.assertRedirects(r, reverse('my_applications'))

        app = StickerApplication.objects.get(plate_number='ABC 123')
        self.assertTrue(
            app.official_receipt.name.endswith('.pdf'), app.official_receipt.name
        )
        with app.official_receipt.open('rb') as f:
            self.assertEqual(f.read(), body)

        # The CR landed on its own field, from its own temp file.
        self.assertTrue(app.vehicle_registration.name)
        self.assertNotEqual(
            app.official_receipt.name, app.vehicle_registration.name
        )

        # Temp storage is emptied once the submission commits.
        for field in ('official_receipt', 'vehicle_registration'):
            self.assertFalse(
                default_storage.exists(f'temp_uploads/{self.user.pk}/{field}')
            )

    def test_form_still_requires_docs_on_a_fresh_form(self):
        form = ApplicationStep2Form(data={
            'plate_number': 'A 1', 'vehicle_type': 'four_wheels',
            'vehicle_color': 'blue', 'is_owner': 'yes',
        })
        form.data = form.data.copy()
        form.data['step1_classification'] = 'student'
        self.assertFalse(form.is_valid())
        for f in ('official_receipt', 'vehicle_registration',
                  'drivers_license', 'cor'):
            self.assertIn(f, form.errors)

    def test_the_cr_alone_is_not_enough(self):
        """Uploading only one of the two no longer satisfies the pair."""
        form = ApplicationStep2Form(data={
            'plate_number': 'A 1', 'vehicle_type': 'four_wheels',
            'vehicle_color': 'blue', 'is_owner': 'yes',
        }, files={'vehicle_registration': doc('cr.pdf')})
        form.data = form.data.copy()
        form.data['step1_classification'] = 'faculty'
        self.assertFalse(form.is_valid())
        self.assertIn('official_receipt', form.errors)
        self.assertNotIn('vehicle_registration', form.errors)


@override_settings(MEDIA_ROOT=MEDIA)
class ServeMissingDocumentTests(TestCase):
    def test_missing_file_is_404_not_500(self):
        owner = User.objects.create_user(
            username='own', password='pw-1234567', role='applicant'
        )
        accept_policy(owner)
        app = StickerApplication.objects.create(
            applicant=owner, full_name='O', college_department='C',
            id_number='1', classification='student', plate_number='GONE-1',
            vehicle_type='four_wheels', vehicle_color='blue', is_owner=True,
            official_receipt=doc('or.pdf'), vehicle_registration=doc('cr.pdf'),
            drivers_license=doc('dl.pdf'), status='draft',
        )
        import os
        os.remove(app.official_receipt.path)

        self.client.force_login(owner)
        r = self.client.get(
            reverse('serve_document', args=[app.pk, 'official_receipt'])
        )
        self.assertEqual(r.status_code, 404)
