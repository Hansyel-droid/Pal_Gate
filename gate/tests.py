from django.contrib.admin.sites import site as admin_site
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from .admin import AuditLogAdmin
from .masking import mask_name, mask_plate, mask_rfid, mask_sticker_id
from .models import AuditLog


class MaskingTests(TestCase):
    """
    gate.masking is what keeps plate numbers, RFID UIDs, sticker IDs, and
    names out of the gate log list and CSV export — pure functions, so
    these are plain input/output checks.
    """

    def test_mask_plate_reveals_only_last_two_chars(self):
        self.assertEqual(mask_plate('ABC1234'), '*****34')

    def test_mask_plate_handles_short_input(self):
        self.assertEqual(mask_plate('AB'), '***')
        self.assertEqual(mask_plate(''), '***')

    def test_mask_rfid_reveals_only_first_four_chars(self):
        self.assertEqual(mask_rfid('AB12CD34'), 'AB12****')

    def test_mask_sticker_id_reveals_only_last_four_of_code(self):
        self.assertEqual(mask_sticker_id('PalSU-A1B2C3D4'), 'PalSU-****C3D4')
        self.assertEqual(mask_sticker_id(None), '—')

    def test_mask_name_shows_initials_only(self):
        self.assertEqual(mask_name('Juan dela Cruz'), 'J*** d***')
        self.assertEqual(mask_name('Cher'), 'C***')
        self.assertEqual(mask_name(''), '—')


class GateViewsAccessTests(TestCase):
    """Every gate/ view is @role_required('security') — spot-check that an
    applicant can't wander into the live gate monitor or logs archive."""

    def setUp(self):
        self.applicant = User.objects.create_user(
            username='applicant1', password='pw-1234567', role='applicant'
        )
        self.security = User.objects.create_user(
            username='security1', password='pw-1234567', role='security'
        )

    def test_applicant_denied_gate_live(self):
        self.client.force_login(self.applicant)
        response = self.client.get(reverse('gate_live'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/dashboard/', response.url)

    def test_security_allowed_gate_live(self):
        self.client.force_login(self.security)
        response = self.client.get(reverse('gate_live'))
        self.assertEqual(response.status_code, 200)

    def test_applicant_denied_gate_logs(self):
        self.client.force_login(self.applicant)
        response = self.client.get(reverse('gate_logs'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/dashboard/', response.url)

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(reverse('gate_live'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)


class AuditLogImmutableTests(TestCase):
    """AuditLog must be genuinely append-only — no one, including a
    superuser in Django admin, can edit or delete a record after the fact."""

    def test_admin_class_blocks_add_change_delete(self):
        ma = AuditLogAdmin(AuditLog, admin_site)
        self.assertFalse(ma.has_add_permission(None))
        self.assertFalse(ma.has_change_permission(None))
        self.assertFalse(ma.has_delete_permission(None))

    def test_superuser_cannot_reach_add_page(self):
        superuser = User.objects.create_superuser(
            username='root', password='pw-1234567', email='root@example.com'
        )
        self.client.force_login(superuser)
        response = self.client.get('/palsu-system-admin-2025/gate/auditlog/add/')
        self.assertEqual(response.status_code, 403)
