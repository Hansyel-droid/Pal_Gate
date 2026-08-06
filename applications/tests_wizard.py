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

from accounts.models import User
from .models import RegistrationWindow, StickerApplication
from .forms import ApplicationStep2Form

MEDIA = tempfile.mkdtemp()


def doc(name='doc.pdf'):
    return SimpleUploadedFile(name, b'%PDF-1.4 test', content_type='application/pdf')


def png(name='doc.png'):
    return SimpleUploadedFile(
        name, b'\x89PNG\r\n\x1a\n' + b'0' * 32, content_type='image/png'
    )


@override_settings(MEDIA_ROOT=MEDIA)
class CleanupCommandTests(TestCase):
    def test_deletes_old_temp_files(self):
        default_storage.save('temp_uploads/7/or_cr', ContentFile(b'old'))
        self.assertTrue(default_storage.exists('temp_uploads/7/or_cr'))

        # Backdate it well past the 24h cutoff.
        import os
        p = default_storage.path('temp_uploads/7/or_cr')
        old = (timezone.now() - timedelta(hours=48)).timestamp()
        os.utime(p, (old, old))

        call_command('cleanup_temp_files')
        self.assertFalse(
            default_storage.exists('temp_uploads/7/or_cr'),
            'cleanup_temp_files did not delete an expired temp file',
        )

    def test_keeps_fresh_temp_files(self):
        default_storage.save('temp_uploads/8/or_cr', ContentFile(b'new'))
        call_command('cleanup_temp_files')
        self.assertTrue(default_storage.exists('temp_uploads/8/or_cr'))


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
            'or_cr': doc('or_cr.pdf'), 'drivers_license': doc('dl.pdf'),
            'cor': doc('cor.pdf'),
        }
        data.update(extra)
        return self.client.post(reverse('apply_step2'), data)

    def test_first_pass_stores_everything(self):
        r = self.post_step2()
        self.assertRedirects(r, reverse('apply_step3'))
        files = self.client.session['app_temp_files']
        self.assertEqual(files['or_cr']['original_name'], 'or_cr.pdf')
        # Path keyed on pk, not username, and carries no extension.
        self.assertEqual(files['or_cr']['path'], f'temp_uploads/{self.user.pk}/or_cr')

    def test_get_seeds_from_session(self):
        self.post_step2()
        r = self.client.get(reverse('apply_step2'))
        self.assertContains(r, 'ABC 123')
        self.assertContains(r, 'On file: or_cr.pdf')

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
        self.assertEqual(after['or_cr'], before['or_cr'])
        self.assertEqual(after['drivers_license'], before['drivers_license'])
        self.assertEqual(after['cor'], before['cor'])

    def test_reupload_different_extension_replaces(self):
        self.post_step2()
        first = self.client.session['app_temp_files']['or_cr']['path']
        self.post_step2(or_cr=png('or_cr.png'))
        second = self.client.session['app_temp_files']
        self.assertEqual(second['or_cr']['path'], first, 'orphaned the old temp file')
        self.assertEqual(second['or_cr']['original_name'], 'or_cr.png')
        _, files = default_storage.listdir(f'temp_uploads/{self.user.pk}')
        self.assertEqual(sorted(files), ['cor', 'drivers_license', 'or_cr'])

    def test_dotdot_username_does_not_crash(self):
        dots = User.objects.create_user(
            username='..', password='pw-1234567', role='applicant',
            classification='faculty',
        )
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
            'or_cr': doc(), 'drivers_license': doc('dl.pdf'),
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
            or_cr=SimpleUploadedFile('my or cr.pdf', body,
                                     content_type='application/pdf')
        )
        self.client.post(reverse('apply_step3'), {
            'slot_id': slot.pk, 'time': '08:00',
        })
        r = self.client.post(reverse('apply_step4'), {})
        self.assertRedirects(r, reverse('my_applications'))

        app = StickerApplication.objects.get(plate_number='ABC 123')
        self.assertTrue(app.or_cr.name.endswith('.pdf'), app.or_cr.name)
        with app.or_cr.open('rb') as f:
            self.assertEqual(f.read(), body)

        # Temp storage is emptied once the submission commits.
        self.assertFalse(
            default_storage.exists(f'temp_uploads/{self.user.pk}/or_cr')
        )

    def test_form_still_requires_docs_on_a_fresh_form(self):
        form = ApplicationStep2Form(data={
            'plate_number': 'A 1', 'vehicle_type': 'four_wheels',
            'vehicle_color': 'blue', 'is_owner': 'yes',
        })
        form.data = form.data.copy()
        form.data['step1_classification'] = 'student'
        self.assertFalse(form.is_valid())
        for f in ('or_cr', 'drivers_license', 'cor'):
            self.assertIn(f, form.errors)


@override_settings(MEDIA_ROOT=MEDIA)
class ServeMissingDocumentTests(TestCase):
    def test_missing_file_is_404_not_500(self):
        owner = User.objects.create_user(
            username='own', password='pw-1234567', role='applicant'
        )
        app = StickerApplication.objects.create(
            applicant=owner, full_name='O', college_department='C',
            id_number='1', classification='student', plate_number='GONE-1',
            vehicle_type='four_wheels', vehicle_color='blue', is_owner=True,
            or_cr=doc('or_cr.pdf'), drivers_license=doc('dl.pdf'), status='draft',
        )
        import os
        os.remove(app.or_cr.path)

        self.client.force_login(owner)
        r = self.client.get(reverse('serve_document', args=[app.pk, 'or_cr']))
        self.assertEqual(r.status_code, 404)
