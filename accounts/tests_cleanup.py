from datetime import timedelta

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from gate.models import AuditLog
from .models import EmailOTP, User


class CleanupAbandonedSignupsCommandTests(TestCase):
    """
    accounts.cleanup_abandoned_signups — the no-retry-required version of
    _release_abandoned_signups() (accounts/views.py). Same safety rule,
    checked here directly against the database rather than through the
    register view: never touch an account whose code might still be used.
    """

    def _pending(self, username, expires_delta, purpose=EmailOTP.PURPOSE_REGISTER):
        user = User.objects.create_user(
            username=username,
            email=f'{username}@psu.palawan.edu.ph',
            password='x',
            is_active=False,
            email_verified=False,
        )
        EmailOTP.objects.create(
            user=user,
            purpose=purpose,
            code_hash='x',
            expires_at=timezone.now() + expires_delta,
        )
        return user

    def test_deletes_accounts_whose_every_code_has_expired(self):
        expired = self._pending('expired_user', timedelta(minutes=-10))

        call_command('cleanup_abandoned_signups', verbosity=0)

        self.assertFalse(User.objects.filter(pk=expired.pk).exists())

    def test_leaves_accounts_with_a_still_live_code_alone(self):
        live = self._pending('live_user', timedelta(minutes=10))

        call_command('cleanup_abandoned_signups', verbosity=0)

        self.assertTrue(User.objects.filter(pk=live.pk).exists())

    def test_ignores_accounts_with_no_registration_code_at_all(self):
        # Shouldn't exist in practice (issue_otp always mints one at
        # sign-up), but an inactive/unverified account with nothing in
        # accounts_emailotp is exactly the kind of row this command must
        # not touch just because it superficially matches on state flags.
        odd = User.objects.create_user(
            username='no_otp_user',
            email='no_otp_user@psu.palawan.edu.ph',
            password='x', is_active=False, email_verified=False,
        )

        call_command('cleanup_abandoned_signups', verbosity=0)

        self.assertTrue(User.objects.filter(pk=odd.pk).exists())

    def test_leaves_active_accounts_alone_regardless_of_otp_history(self):
        active = User.objects.create_user(
            username='active_user',
            email='active_user@psu.palawan.edu.ph',
            password='x', is_active=True, email_verified=True,
        )
        EmailOTP.objects.create(
            user=active, purpose=EmailOTP.PURPOSE_REGISTER, code_hash='x',
            expires_at=timezone.now() - timedelta(minutes=10),
        )

        call_command('cleanup_abandoned_signups', verbosity=0)

        self.assertTrue(User.objects.filter(pk=active.pk).exists())

    def test_leaves_accounts_that_have_logged_in_alone(self):
        # is_active/email_verified can't both be true for an account that
        # has ever logged in through the normal flow, but last_login is
        # checked independently on purpose — belt and braces against
        # whatever state a hand-edited or legacy row might be in.
        used = User.objects.create_user(
            username='used_user',
            email='used_user@psu.palawan.edu.ph',
            password='x', is_active=False, email_verified=False,
        )
        used.last_login = timezone.now()
        used.save(update_fields=['last_login'])
        EmailOTP.objects.create(
            user=used, purpose=EmailOTP.PURPOSE_REGISTER, code_hash='x',
            expires_at=timezone.now() - timedelta(minutes=10),
        )

        call_command('cleanup_abandoned_signups', verbosity=0)

        self.assertTrue(User.objects.filter(pk=used.pk).exists())

    def test_writes_an_audit_log_entry_for_each_deleted_account(self):
        expired = self._pending('audited_user', timedelta(minutes=-10))

        call_command('cleanup_abandoned_signups', verbosity=0)

        entry = AuditLog.objects.get(action='signup_abandoned')
        self.assertEqual(entry.target_user, 'audited_user')
        self.assertEqual(entry.extra_data.get('user_id'), expired.pk)
