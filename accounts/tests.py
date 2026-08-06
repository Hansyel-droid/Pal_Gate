import re
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.core import mail
from django.core.cache import cache
from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase
from django.utils import timezone

from .forms import RegisterForm
from .mixins import role_required
from .models import EmailOTP, User


@role_required('admin')
def _dummy_admin_only_view(request):
    return HttpResponse('ok')


def _attach_session_and_messages(request):
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    setattr(request, '_messages', FallbackStorage(request))


class RoleRequiredDecoratorTests(TestCase):
    """
    accounts.mixins.role_required gates almost every view in the app
    (applications, sticker_admin, gate all use it). Testing the decorator
    directly against a dummy view isolates its behavior from any one
    view's business logic.
    """

    def setUp(self):
        self.factory = RequestFactory()

    def test_anonymous_user_redirected_to_login(self):
        request = self.factory.get('/dummy/')
        request.user = AnonymousUser()
        _attach_session_and_messages(request)

        response = _dummy_admin_only_view(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/accounts/login/')

    def test_wrong_role_redirected_to_dashboard_not_allowed_through(self):
        user = User.objects.create_user(
            username='security1', password='pw-1234567', role='security'
        )
        request = self.factory.get('/dummy/')
        request.user = user
        _attach_session_and_messages(request)

        response = _dummy_admin_only_view(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/dashboard/')

    def test_matching_role_is_allowed_through(self):
        user = User.objects.create_user(
            username='admin1', password='pw-1234567', role='admin'
        )
        request = self.factory.get('/dummy/')
        request.user = user
        _attach_session_and_messages(request)

        response = _dummy_admin_only_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'ok')


class RootUrlTests(TestCase):
    """
    config.urls.root_redirect — the bare site address. Nothing was mapped
    at '/' originally, so anyone typing the plain URL (the normal thing to
    do when handed a link) got a 404 and concluded the site was down.
    """

    def test_anonymous_visitor_is_sent_to_login(self):
        response = self.client.get('/')
        self.assertRedirects(response, '/accounts/login/')

    def test_signed_in_user_is_sent_to_their_dashboard(self):
        user = User.objects.create_user(
            username='rooter', password='pw-1234567', role='admin'
        )
        self.client.force_login(user)
        response = self.client.get('/', follow=True)
        # '/' -> '/dashboard/' -> the role's own landing page.
        self.assertEqual(
            response.redirect_chain,
            [('/dashboard/', 302), ('/sticker-admin/', 302)],
        )
        self.assertEqual(response.status_code, 200)


class RegistrationEmailDomainTests(TestCase):
    """
    Self-registration is restricted to official campus addresses — the
    emailed code only means something if the mailbox itself implies
    membership in the university.
    """

    BASE = {
        'username': 'jdelacruz',
        'first_name': 'Juan',
        'last_name': 'Dela Cruz',
        'password1': 'Str0ng-Passw0rd!',
        'password2': 'Str0ng-Passw0rd!',
    }

    def test_campus_address_is_accepted(self):
        form = RegisterForm({**self.BASE, 'email': '202380158@psu.palawan.edu.ph'})
        self.assertTrue(form.is_valid(), form.errors)

    def test_outside_address_is_rejected(self):
        form = RegisterForm({**self.BASE, 'email': 'juan@gmail.com'})
        self.assertFalse(form.is_valid())
        self.assertIn('psu.palawan.edu.ph', str(form.errors['email']))

    def test_lookalike_domain_is_rejected(self):
        # Plain substring matching would let this through; anchoring the
        # check on the "@" is what stops it.
        form = RegisterForm({**self.BASE, 'email': 'juan@evil-psu.palawan.edu.ph'})
        self.assertFalse(form.is_valid())

    def test_domain_as_prefix_is_rejected(self):
        form = RegisterForm({
            **self.BASE, 'email': 'juan@psu.palawan.edu.ph.attacker.com'
        })
        self.assertFalse(form.is_valid())

    def test_address_is_normalized_to_lowercase(self):
        form = RegisterForm({**self.BASE, 'email': '202380158@PSU.Palawan.Edu.PH'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data['email'], '202380158@psu.palawan.edu.ph'
        )

    def test_duplicate_address_is_rejected(self):
        User.objects.create_user(
            username='existing', password='pw-1234567',
            email='202380158@psu.palawan.edu.ph',
        )
        form = RegisterForm({
            **self.BASE, 'username': 'other',
            'email': '202380158@psu.palawan.edu.ph',
        })
        self.assertFalse(form.is_valid())


class RegistrationOTPFlowTests(TestCase):
    """
    End-to-end coverage of the two-step sign-up: no usable account exists
    until the emailed code comes back.
    """

    EMAIL = '202380158@psu.palawan.edu.ph'
    PASSWORD = 'Str0ng-Passw0rd!'
    FORM = {
        'username': 'jdelacruz',
        'first_name': 'Juan',
        'last_name': 'Dela Cruz',
        'email': EMAIL,
        'password1': PASSWORD,
        'password2': PASSWORD,
    }

    def _applicant(self):
        return User.objects.get(username='jdelacruz')

    def setUp(self):
        # django-ratelimit counts hits in the shared file-based cache,
        # which outlives a single test. Without this, whichever test runs
        # eleventh gets a 403 instead of exercising the view.
        cache.clear()

    def _register(self, **overrides):
        return self.client.post(
            '/accounts/register/', {**self.FORM, **overrides}
        )

    def _sent_code(self):
        """Pull the code back out of the email the test backend captured."""
        body = mail.outbox[-1].body
        match = re.search(r'\b(\d{6})\b', body)
        self.assertIsNotNone(match, f'No code in email body:\n{body}')
        return match.group(1)

    def test_register_creates_inactive_account_and_emails_a_code(self):
        response = self._register()

        self.assertRedirects(response, '/accounts/register/verify/')
        user = self._applicant()
        self.assertFalse(user.is_active)
        self.assertFalse(user.email_verified)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['202380158@psu.palawan.edu.ph'])

    def test_unverified_account_cannot_log_in(self):
        self._register()

        response = self.client.post('/accounts/login/', {
            'username': 'jdelacruz', 'password': self.PASSWORD,
        })

        self.assertEqual(response.status_code, 200)  # re-rendered, not signed in
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_correct_code_activates_the_account(self):
        self._register()

        response = self.client.post(
            '/accounts/register/verify/', {'code': self._sent_code()}
        )

        self.assertRedirects(response, '/accounts/login/')
        user = self._applicant()
        self.assertTrue(user.is_active)
        self.assertTrue(user.email_verified)
        # Sign in through the view — django-axes' backend needs a real
        # request, so Client.login() can't be used here.
        self.client.post('/accounts/login/', {
            'username': 'jdelacruz', 'password': self.PASSWORD,
        })
        self.assertEqual(
            self.client.session.get('_auth_user_id'), str(user.pk)
        )

    def test_code_is_not_stored_in_plain_text(self):
        self._register()
        code = self._sent_code()

        self.assertNotIn(code, EmailOTP.objects.get().code_hash)

    def test_wrong_code_leaves_the_account_inactive(self):
        self._register()

        self.client.post('/accounts/register/verify/', {'code': '000000'})

        self.assertFalse(self._applicant().is_active)

    def test_guessing_attempts_are_capped(self):
        self._register()
        real_code = self._sent_code()
        wrong = '000000' if real_code != '000000' else '111111'

        for _ in range(settings.OTP_MAX_ATTEMPTS):
            self.client.post('/accounts/register/verify/', {'code': wrong})

        # Even the right code is refused once the cap is reached.
        self.client.post('/accounts/register/verify/', {'code': real_code})
        self.assertFalse(self._applicant().is_active)

    def test_expired_code_is_refused(self):
        self._register()
        code = self._sent_code()
        EmailOTP.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

        self.client.post('/accounts/register/verify/', {'code': code})

        self.assertFalse(self._applicant().is_active)

    def test_resending_invalidates_the_previous_code(self):
        self._register()
        first_code = self._sent_code()

        # Step past the cooldown so the resend is allowed through.
        EmailOTP.objects.update(
            created_at=timezone.now() - timedelta(
                seconds=settings.OTP_RESEND_COOLDOWN_SECONDS + 1
            )
        )
        self.client.post('/accounts/register/resend/')
        second_code = self._sent_code()
        self.assertNotEqual(first_code, second_code)

        self.client.post('/accounts/register/verify/', {'code': first_code})
        self.assertFalse(self._applicant().is_active)

        self.client.post('/accounts/register/verify/', {'code': second_code})
        self.assertTrue(self._applicant().is_active)

    def test_resend_respects_the_cooldown(self):
        self._register()

        self.client.post('/accounts/register/resend/')

        self.assertEqual(len(mail.outbox), 1)  # no second email went out

    def test_verification_page_needs_a_pending_signup(self):
        response = self.client.get('/accounts/register/verify/')

        self.assertRedirects(response, '/accounts/register/')

    def test_another_session_cannot_verify_someone_elses_signup(self):
        self._register()
        code = self._sent_code()

        response = Client().post('/accounts/register/verify/', {'code': code})

        self.assertRedirects(response, '/accounts/register/')
        self.assertFalse(self._applicant().is_active)

    def test_abandoned_signup_does_not_lock_the_username_forever(self):
        self._register()
        first_id = self._applicant().pk

        # Same person, fresh browser, trying again.
        Client().post('/accounts/register/', self.FORM)

        user = self._applicant()
        self.assertNotEqual(user.pk, first_id)
        self.assertFalse(user.is_active)

    def test_verified_account_is_not_released_by_a_re_registration(self):
        self._register()
        self.client.post(
            '/accounts/register/verify/', {'code': self._sent_code()}
        )
        verified_id = self._applicant().pk

        Client().post('/accounts/register/', self.FORM)

        self.assertTrue(User.objects.filter(pk=verified_id).exists())

    def test_suspended_account_cannot_be_deleted_by_re_registering(self):
        """
        The abandoned-signup cleanup must never become a way to wipe a
        real account that an admin has deactivated.
        """
        suspended = User.objects.create_user(
            username='suspended', password='pw-1234567',
            email='999999999@psu.palawan.edu.ph',
        )
        suspended.is_active = False
        suspended.email_verified = False
        suspended.save()

        self.client.post('/accounts/register/', {
            **self.FORM, 'username': 'suspended',
            'email': '999999999@psu.palawan.edu.ph',
        })

        self.assertTrue(User.objects.filter(pk=suspended.pk).exists())
