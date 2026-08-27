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
from .views import PENDING_SESSION_KEY, RESET_SENT_AT_KEY


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

    def test_verification_page_without_a_session_asks_for_identifier(self):
        # No pending session (nobody registered in this browser this run) —
        # rather than dead-ending with "your session expired, start over",
        # the page offers a way to finish verifying anyway: type back the
        # username/email plus the code. See OTPVerifyForm's docstring for
        # why session continuity can't be assumed with real users.
        response = self.client.get('/accounts/register/verify/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="identifier"')

    def test_another_session_cannot_verify_someone_elses_signup_by_code_alone(self):
        # A stranger's browser has no session pointing at the victim's
        # pending account, and they don't submit an identifier either — the
        # code by itself, with no way to resolve *whose* code it is, must
        # not be enough.
        self._register()
        code = self._sent_code()

        response = Client().post('/accounts/register/verify/', {'code': code})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(self._applicant().is_active)

    def test_knowing_the_identifier_is_not_enough_without_the_code(self):
        # The identifier fallback exists so a lost session doesn't strand a
        # real user — it must not become a second way in that skips the
        # code. A stranger who knows the victim's username/email still
        # can't activate the account without the code actually emailed.
        self._register()

        response = Client().post('/accounts/register/verify/', {
            'identifier': 'jdelacruz', 'code': '000000',
        })

        self.assertEqual(response.status_code, 200)
        self.assertFalse(self._applicant().is_active)

    def test_identifier_fallback_still_requires_the_real_code(self):
        # The positive case: a genuinely lost session (different device,
        # different browser) is recoverable with identifier + the actual
        # code — this is the whole point of the fallback.
        self._register()
        code = self._sent_code()

        response = Client().post('/accounts/register/verify/', {
            'identifier': self.EMAIL, 'code': code,
        })

        self.assertRedirects(response, '/accounts/login/')
        self.assertTrue(self._applicant().is_active)

    def _expire_pending_code(self):
        """Age out the outstanding code, i.e. the sign-up is now abandoned
        rather than in progress."""
        EmailOTP.objects.filter(user=self._applicant()).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )

    def test_abandoned_signup_does_not_lock_the_username_forever(self):
        self._register()
        first_id = self._applicant().pk
        self._expire_pending_code()

        # Same person, fresh browser, trying again once the code they never
        # used has expired.
        Client().post('/accounts/register/', self.FORM)

        user = self._applicant()
        self.assertNotEqual(user.pk, first_id)
        self.assertFalse(user.is_active)

    def test_starting_over_in_the_same_browser_releases_the_username(self):
        # The common case: they mistyped the address, so they go back and
        # register again rather than waiting ten minutes for the code to
        # lapse. Their own session owns the pending account, so it goes.
        self._register()
        first_id = self._applicant().pk

        self._register()

        self.assertNotEqual(self._applicant().pk, first_id)
        self.assertFalse(User.objects.filter(pk=first_id).exists())

    def test_in_progress_signup_cannot_be_deleted_by_a_stranger(self):
        # This cleanup runs on raw POST data before the form is validated,
        # so it acted on any username a caller typed. Someone sitting on
        # the verification page with a live code must survive a stranger
        # submitting their username, or knowing a username is enough to
        # strand them mid-sign-up.
        self._register()
        victim_id = self._applicant().pk
        code = self._sent_code()

        Client().post('/accounts/register/', {
            **self.FORM, 'email': 'attacker@psu.palawan.edu.ph',
        })

        self.assertTrue(User.objects.filter(pk=victim_id).exists())
        # And their code still works.
        response = self.client.post(
            '/accounts/register/verify/', {'code': code}
        )
        self.assertRedirects(response, '/accounts/login/')
        self.assertTrue(User.objects.get(pk=victim_id).is_active)

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


class PasswordResetTests(TestCase):
    """
    "Forgot password" has to work for every kind of account, not just the
    applicants who happen to sign up with an email address.

    cache.clear() in setUp because the reset endpoint is IP rate-limited
    (5/m in config.urls) against the file-based cache, which persists
    between test runs — without it the later tests in this class start
    getting 403s from django-ratelimit rather than exercising the flow.
    """

    def setUp(self):
        cache.clear()
        self.applicant = User.objects.create_user(
            username='rundeal', password='old-password-123',
            email='202380026@psu.palawan.edu.ph',
            role='applicant', email_verified=True,
        )
        self.officer = User.objects.create_user(
            username='wt_security', password='old-password-123',
            email='guardhouse@psu.palawan.edu.ph', role='security',
        )

    def _request_reset(self, identifier):
        return self.client.post(
            '/accounts/password-reset/', {'email': identifier}
        )

    def test_applicant_can_reset_with_their_campus_email(self):
        self._request_reset('202380026@psu.palawan.edu.ph')

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].to, ['202380026@psu.palawan.edu.ph']
        )

    def test_staff_can_reset_with_the_username_they_sign_in_with(self):
        """
        The whole reason the form takes more than an address: admins and
        security officers are created in the Django admin and know their
        username, not necessarily which mailbox is on the account.
        """
        self._request_reset('wt_security')

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['guardhouse@psu.palawan.edu.ph'])

    def test_lookup_is_case_insensitive(self):
        self._request_reset('WT_Security')

        self.assertEqual(len(mail.outbox), 1)

    def test_link_goes_to_the_account_not_to_whatever_was_typed(self):
        """
        Widening the lookup must not widen delivery. Typing someone
        else's username has to mail *them*, or the form becomes a way to
        have a reset link delivered to an address you chose.
        """
        self._request_reset('rundeal')

        self.assertEqual(mail.outbox[0].to, ['202380026@psu.palawan.edu.ph'])

    def test_email_names_the_username_and_comes_from_the_campus_domain(self):
        self._request_reset('202380026@psu.palawan.edu.ph')
        sent = mail.outbox[0]

        self.assertIn('rundeal', sent.body)
        self.assertIn('@psu.palawan.edu.ph', sent.from_email)
        self.assertIn('PalawanSU Gate', sent.subject)

    def test_emailed_link_actually_sets_a_new_password(self):
        self._request_reset('rundeal')
        link = re.search(r'/accounts/reset/\S+', mail.outbox[0].body).group()

        # The confirm view swaps the token for a session-held one and
        # redirects; the form is posted to that redirected URL.
        set_password_url = self.client.get(link, follow=True).redirect_chain[-1][0]
        self.client.post(set_password_url, {
            'new_password1': 'brand-new-pass-9', 'new_password2': 'brand-new-pass-9',
        })

        self.applicant.refresh_from_db()
        self.assertTrue(self.applicant.check_password('brand-new-pass-9'))

    def test_unverified_applicant_gets_no_link(self):
        """
        An account still waiting on its sign-up code is inactive and
        cannot log in, so a new password would not let them in either.
        They need to finish verifying, not to reset.
        """
        User.objects.create_user(
            username='half_signed_up', password='old-password-123',
            email='pending@psu.palawan.edu.ph',
            is_active=False, email_verified=False,
        )

        self._request_reset('half_signed_up')

        self.assertEqual(len(mail.outbox), 0)

    def test_account_with_no_email_gets_no_link(self):
        """
        Legacy walk-in records have no address. There is nowhere to send
        a link, and matching them must not mail some other row.
        """
        User.objects.create_user(
            username='walkin_1953', password='old-password-123', email='',
        )

        self._request_reset('walkin_1953')

        self.assertEqual(len(mail.outbox), 0)

    def test_unknown_identifier_does_not_reveal_that_it_is_unknown(self):
        response = self._request_reset('no-such-person')

        self.assertEqual(len(mail.outbox), 0)
        self.assertRedirects(response, '/accounts/password-reset/done/')

    def test_sign_in_page_offers_the_reset(self):
        response = self.client.get('/accounts/login/')

        self.assertContains(response, '/accounts/password-reset/')


class StaffAccountEmailRequirementTests(TestCase):
    """
    accounts.admin refuses to save a staff account with no email, because
    such an account has no way back in when its password is forgotten —
    the reset link has nowhere to go.
    """

    def _creation_form(self, **overrides):
        from .admin import StaffAwareUserCreationForm
        return StaffAwareUserCreationForm(data={
            'username': 'new_officer',
            'password1': 'a-long-enough-pw-42',
            'password2': 'a-long-enough-pw-42',
            'email': '',
            'role': 'security',
            **overrides,
        })

    def test_staff_account_needs_an_email(self):
        form = self._creation_form()

        self.assertFalse(form.is_valid())
        self.assertIn('email', form.errors)

    def test_staff_account_with_an_email_is_accepted(self):
        form = self._creation_form(email='guardhouse@psu.palawan.edu.ph')

        self.assertTrue(form.is_valid(), form.errors)

    def test_applicant_records_may_still_have_no_email(self):
        """
        Walk-in records predate the portal and were never logins. Making
        an address mandatory here would make those rows uneditable.
        """
        form = self._creation_form(role='applicant')

        self.assertTrue(form.is_valid(), form.errors)


class PasswordResetReachabilityTests(TestCase):
    """
    The reset flow existing is not the same as anyone being able to find
    it. There are two login forms in this project — the portal's, shared
    by all three roles, and the Django admin's — and a way out of a
    forgotten password has to be on both.
    """

    def setUp(self):
        cache.clear()

    def test_the_shared_sign_in_page_links_to_the_reset(self):
        """
        Applicants, sticker administrators and security officers all sign
        in here (accounts.views.login_view routes by role afterwards), so
        this one link covers every account type.
        """
        response = self.client.get('/accounts/login/')

        self.assertContains(response, '/accounts/password-reset/')

    def test_the_django_admin_login_links_to_the_reset(self):
        response = self.client.get('/palsu-system-admin-2025/login/')

        self.assertContains(response, '/palsu-system-admin-2025/password_reset/')

    def test_the_admin_reset_link_lands_on_the_portal_flow(self):
        response = self.client.get('/palsu-system-admin-2025/password_reset/')

        self.assertRedirects(response, '/accounts/password-reset/')


class PasswordReuseTests(TestCase):
    """
    Setting the password back to what it already was must fail loudly.

    Reporting "password changed" when nothing changed is worse than a
    plain refusal: the one person most likely to type their old password
    back is somebody resetting *because* they think that password is
    known to someone else.
    """

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username='rundeal', password='old-password-123',
            email='202380026@psu.palawan.edu.ph', email_verified=True,
        )

    def _set_password_url(self):
        self.client.post(
            '/accounts/password-reset/', {'email': 'rundeal'}
        )
        link = re.search(r'/accounts/reset/\S+', mail.outbox[0].body).group()
        return self.client.get(link, follow=True).redirect_chain[-1][0]

    def test_reusing_the_current_password_is_refused(self):
        response = self.client.post(self._set_password_url(), {
            'new_password1': 'old-password-123',
            'new_password2': 'old-password-123',
        })

        self.assertContains(response, 'must be different from your current')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('old-password-123'))

    def test_a_genuinely_new_password_is_accepted(self):
        self.client.post(self._set_password_url(), {
            'new_password1': 'brand-new-pass-9',
            'new_password2': 'brand-new-pass-9',
        })

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('brand-new-pass-9'))

    def test_signing_up_is_not_blocked_by_the_new_rule(self):
        """
        The rule is registered globally, so it also runs against the
        not-yet-created account on the sign-up form. That user has no
        current password, and must not be tripped up by a check for one.
        """
        form = RegisterForm(data={
            'username': 'newcomer', 'first_name': 'New', 'last_name': 'Comer',
            'email': '202399999@psu.palawan.edu.ph',
            'password1': 'a-fresh-password-1', 'password2': 'a-fresh-password-1',
        })

        self.assertTrue(form.is_valid(), form.errors)


class OTPInputRenderingTests(TestCase):
    """
    The one-digit-per-box code entry is built by JS in base.html from
    `data-otp-length` on the code field. That attribute is the whole
    contract between the template and the script, and it carries
    settings.OTP_LENGTH — so a 6 hardcoded in either place, or the
    attribute being dropped in a template edit, silently returns the page
    to a single input or builds the wrong number of boxes.
    """

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username='half_signed_up', password='pw-1234567890',
            email='pending@psu.palawan.edu.ph',
        )
        self.user.is_active = False
        self.user.email_verified = False
        self.user.save()

        session = self.client.session
        session[PENDING_SESSION_KEY] = self.user.pk
        session.save()

    def test_code_field_declares_the_configured_length(self):
        response = self.client.get('/accounts/register/verify/')

        self.assertContains(
            response, f'data-otp-length="{settings.OTP_LENGTH}"'
        )

    def test_the_field_still_posts_under_the_same_name(self):
        """
        The boxes write into this one input rather than replacing it, so
        the server contract is unchanged and the page keeps working with
        no JS at all.
        """
        response = self.client.get('/accounts/register/verify/')

        self.assertContains(response, 'name="code"')


class PasswordResetResendTests(TestCase):
    """
    Mail goes missing — filtered, delayed, mistyped address. "Check your
    email" is a dead end unless it can send again, and it must not make
    the person type their username a second time to do it.
    """

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username='rundeal', password='old-password-123',
            email='202380026@psu.palawan.edu.ph', email_verified=True,
        )

    def _request_reset(self):
        return self.client.post(
            '/accounts/password-reset/', {'email': 'rundeal'}
        )

    def _expire_the_cooldown(self):
        session = self.client.session
        session[RESET_SENT_AT_KEY] = (
            timezone.now()
            - timedelta(seconds=settings.OTP_RESEND_COOLDOWN_SECONDS + 1)
        ).isoformat()
        session.save()

    def test_resend_sends_another_link_without_retyping_anything(self):
        self._request_reset()
        self._expire_the_cooldown()

        self.client.post('/accounts/password-reset/resend/')

        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(mail.outbox[1].to, ['202380026@psu.palawan.edu.ph'])

    def test_the_resent_email_is_the_same_branded_one(self):
        """
        The resend builds its own form rather than going through the
        view, so it is the likeliest place to silently fall back to
        Django's stock template.
        """
        self._request_reset()
        self._expire_the_cooldown()
        self.client.post('/accounts/password-reset/resend/')

        resent = mail.outbox[1]
        self.assertIn('PalawanSU Gate', resent.subject)
        self.assertIn('Username: rundeal', resent.body)
        self.assertIn('@psu.palawan.edu.ph', resent.from_email)

    def test_the_resent_link_works(self):
        self._request_reset()
        self._expire_the_cooldown()
        self.client.post('/accounts/password-reset/resend/')

        link = re.search(r'/accounts/reset/\S+', mail.outbox[1].body).group()
        url = self.client.get(link, follow=True).redirect_chain[-1][0]
        self.client.post(url, {
            'new_password1': 'brand-new-pass-9',
            'new_password2': 'brand-new-pass-9',
        })

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('brand-new-pass-9'))

    def test_the_cooldown_blocks_an_immediate_second_link(self):
        """
        Without the wait, "send it again" is a button that floods
        somebody else's inbox for as long as you keep clicking it.
        """
        self._request_reset()

        self.client.post('/accounts/password-reset/resend/')

        self.assertEqual(len(mail.outbox), 1)

    def test_resending_without_having_asked_first_goes_back_to_the_form(self):
        response = self.client.post('/accounts/password-reset/resend/')

        self.assertRedirects(response, '/accounts/password-reset/')
        self.assertEqual(len(mail.outbox), 0)

    def test_resend_says_the_same_thing_for_an_unknown_identifier(self):
        """
        The done page never reveals whether an account exists. A resend
        that reported differently for real accounts would hand that back.
        """
        self.client.post(
            '/accounts/password-reset/', {'email': 'no-such-person'}
        )
        self._expire_the_cooldown()

        response = self.client.post(
            '/accounts/password-reset/resend/', follow=True
        )

        self.assertEqual(len(mail.outbox), 0)
        self.assertContains(response, 'another reset link is on its way')

    def test_the_done_page_offers_the_resend_after_a_request(self):
        self._request_reset()

        response = self.client.get('/accounts/password-reset/done/')

        self.assertContains(response, '/accounts/password-reset/resend/')

    def test_the_done_page_hides_the_resend_with_nothing_to_send(self):
        response = self.client.get('/accounts/password-reset/done/')

        self.assertNotContains(response, '/accounts/password-reset/resend/')

    def test_the_countdown_is_handed_to_the_page(self):
        """
        `data-resend-in` is what the script in base.html ticks down; the
        `disabled` attribute alone would leave the button stuck until a
        manual reload.
        """
        self._request_reset()

        response = self.client.get('/accounts/password-reset/done/')

        self.assertContains(response, 'data-resend-in=')

    def test_the_signup_code_button_carries_the_countdown_too(self):
        pending = User.objects.create_user(
            username='half_signed_up', password='pw-1234567890',
            email='pending@psu.palawan.edu.ph',
        )
        pending.is_active = False
        pending.email_verified = False
        pending.save()
        session = self.client.session
        session[PENDING_SESSION_KEY] = pending.pk
        session.save()

        response = self.client.get('/accounts/register/verify/')

        self.assertContains(response, 'data-resend-in=')


class TemplateCommentSyntaxTests(TestCase):
    """
    Django's {# #} comment cannot span lines: the lexer only matches it
    when the closing #} is on the same line as the opening {#. A
    multi-line one is not a comment at all — it is rendered to the page,
    verbatim, in front of the user. Nothing warns about it, and it is
    invisible in the source unless you already know the rule.

    {% comment %}/{% endcomment %} is the multi-line form, so the fix is
    always the same. This walks every template so the next one is caught
    here rather than by somebody reading a page.
    """

    def test_no_template_opens_a_comment_it_does_not_close_on_that_line(self):
        offenders = []

        for path in (settings.BASE_DIR / 'templates').rglob('*.html'):
            for number, line in enumerate(
                path.read_text(encoding='utf-8').splitlines(), start=1
            ):
                for match in re.finditer(r'\{#', line):
                    if '#}' not in line[match.end():]:
                        offenders.append(
                            f'{path.relative_to(settings.BASE_DIR)}:{number}'
                        )

        self.assertEqual(offenders, [], (
            'These open a {# comment #} that does not close on the same '
            'line, so Django renders it as visible text. Use '
            '{% comment %}...{% endcomment %} instead: '
            + ', '.join(offenders)
        ))
