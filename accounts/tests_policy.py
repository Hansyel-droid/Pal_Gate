"""
Tests for the Campus Access Policy gate.

What actually matters here is not "does the page render" but the three ways
this feature can fail badly:

  1. An applicant slips past the gate and uses the system without accepting.
  2. An applicant who HAS accepted gets trapped in a redirect loop, or is
     re-asked forever — the feature becoming an outage.
  3. A revised policy is published and nobody is asked to read it, so the
     acceptance records point at text that is no longer in force.
"""
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import PolicyAcceptance, User
from accounts.policy import CAMPUS_POLICY_VERSION, has_accepted_current_policy
from applications.models import RegistrationWindow


class PolicyGateTests(TestCase):

    def setUp(self):
        self.applicant = User.objects.create_user(
            username='applicant1',
            email='applicant1@psu.palawan.edu.ph',
            password='TestPass!2345',
            role='applicant',
            email_verified=True,
        )
        self.policy_url = reverse('campus_policy')

    def accept_for(self, user, version=CAMPUS_POLICY_VERSION):
        return PolicyAcceptance.objects.create(user=user, version=version)

    # ── The gate itself ──────────────────────────────────────────────────

    def test_applicant_who_has_not_accepted_is_redirected(self):
        self.client.force_login(self.applicant)
        response = self.client.get(reverse('applicant_home'))
        self.assertRedirects(
            response,
            f"{self.policy_url}?next={reverse('applicant_home')}",
        )

    def test_applicant_cannot_reach_the_wizard_without_accepting(self):
        # The gate exists to stop people applying, not merely to decorate
        # the dashboard — so the wizard entry point is checked directly.
        self.client.force_login(self.applicant)
        response = self.client.get(reverse('apply_step1'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(self.policy_url, response.url)

    def test_accepted_applicant_passes_through(self):
        self.accept_for(self.applicant)
        self.client.force_login(self.applicant)
        response = self.client.get(reverse('applicant_home'))
        self.assertEqual(response.status_code, 200)

    def test_policy_page_itself_is_never_gated(self):
        # If this fails the applicant is in an infinite redirect loop and
        # the site is unusable for them — the single worst outcome here.
        self.client.force_login(self.applicant)
        response = self.client.get(self.policy_url)
        self.assertEqual(response.status_code, 200)

    def test_logout_stays_reachable_without_accepting(self):
        # Someone who declines must be able to leave. Gating logout would
        # strand them in a signed-in session they can't act on.
        self.client.force_login(self.applicant)
        response = self.client.post(reverse('logout'))
        self.assertRedirects(response, reverse('login'))

    # ── Who is gated ─────────────────────────────────────────────────────

    def test_admin_and_security_are_not_gated(self):
        # Deliberate: a guard unable to open the gate screen at shift start
        # because of an unread notice is a safety problem, not compliance.
        for role, landing in (
            ('admin', 'admin_dashboard'),
            ('security', 'gate_live'),
        ):
            with self.subTest(role=role):
                staff = User.objects.create_user(
                    username=f'{role}_user',
                    email=f'{role}@psu.palawan.edu.ph',
                    password='TestPass!2345',
                    role=role,
                )
                self.client.force_login(staff)
                response = self.client.get(reverse(landing))
                self.assertEqual(response.status_code, 200)

    # ── Recording acceptance ─────────────────────────────────────────────

    def test_posting_agreement_records_it_and_lets_them_through(self):
        self.client.force_login(self.applicant)
        response = self.client.post(self.policy_url, {'policy_confirm': 'on'})

        self.assertTrue(
            PolicyAcceptance.objects.filter(
                user=self.applicant, version=CAMPUS_POLICY_VERSION
            ).exists()
        )
        self.assertEqual(response.status_code, 302)

        # And the gate is now open.
        self.assertEqual(
            self.client.get(reverse('applicant_home')).status_code, 200
        )

    def test_double_submit_does_not_500_or_double_record(self):
        # An impatient second click would hit the unique constraint if the
        # view wrote blindly. What must hold: no IntegrityError page, and
        # no second row.
        self.client.force_login(self.applicant)
        first = self.client.post(self.policy_url, {'policy_confirm': 'on'})
        second = self.client.post(self.policy_url, {'policy_confirm': 'on'})

        self.assertEqual(first.status_code, 302)

        # The second POST re-renders the page with the acceptance banner
        # rather than redirecting, because the write branch is skipped once
        # already_accepted is true. Either would be acceptable; what is
        # being pinned here is that it's a normal page and not a 500.
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.context['already_accepted'])

        self.assertEqual(
            PolicyAcceptance.objects.filter(user=self.applicant).count(), 1
        )

    def test_accepting_returns_them_to_where_they_were_headed(self):
        self.client.force_login(self.applicant)
        response = self.client.post(
            self.policy_url,
            {'policy_confirm': 'on', 'next': reverse('my_applications')},
        )
        self.assertRedirects(response, reverse('my_applications'))

    def test_next_cannot_be_used_as_an_open_redirect(self):
        # This parameter is handed to every applicant at login, which makes
        # it the worst possible place to leave an open redirect.
        self.client.force_login(self.applicant)
        response = self.client.post(
            self.policy_url,
            {'policy_confirm': 'on', 'next': 'https://evil.example.com/'},
        )
        # The important assertion: the attacker's host is not in the
        # redirect at all.
        self.assertNotIn('evil.example.com', response.url)

        # It falls back to the role-neutral dashboard, which then forwards
        # each role to its own landing page — so this asserts /dashboard/,
        # not the applicant's eventual /apply/.
        self.assertRedirects(
            response, reverse('dashboard'), fetch_redirect_response=False,
        )

    def test_protocol_relative_next_is_also_rejected(self):
        # '//evil.example.com' starts with '/' but browsers treat it as an
        # absolute URL — the classic way a startswith('/') check is bypassed.
        self.client.force_login(self.applicant)
        response = self.client.post(
            self.policy_url,
            {'policy_confirm': 'on', 'next': '//evil.example.com/'},
        )
        self.assertNotIn('evil.example.com', response.url)

    # ── Versioning ───────────────────────────────────────────────────────

    def test_accepting_an_old_version_does_not_count(self):
        # The whole point of versioning: a revised memorandum must be read
        # again, not silently inherited from a previous agreement.
        self.accept_for(self.applicant, version='2020-01-01')

        self.assertFalse(has_accepted_current_policy(self.applicant))

        self.client.force_login(self.applicant)
        response = self.client.get(reverse('applicant_home'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(self.policy_url, response.url)

    def test_old_acceptance_is_kept_after_accepting_the_new_one(self):
        # Superseded acceptances are evidence, not clutter — they answer
        # "did this person agree to the rules in force at the time?".
        self.accept_for(self.applicant, version='2020-01-01')
        self.client.force_login(self.applicant)
        self.client.post(self.policy_url, {'policy_confirm': 'on'})

        versions = set(
            self.applicant.policy_acceptances.values_list('version', flat=True)
        )
        self.assertEqual(versions, {'2020-01-01', CAMPUS_POLICY_VERSION})

    # ── The read-anytime path ────────────────────────────────────────────

    def test_accepted_applicant_can_reread_and_sees_their_acceptance_date(self):
        acceptance = self.accept_for(self.applicant)
        self.client.force_login(self.applicant)
        response = self.client.get(self.policy_url)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['already_accepted'])
        self.assertEqual(response.context['accepted_record'], acceptance)

    def test_revisiting_does_not_create_another_acceptance(self):
        self.accept_for(self.applicant)
        self.client.force_login(self.applicant)
        self.client.post(self.policy_url, {'policy_confirm': 'on'})

        self.assertEqual(
            PolicyAcceptance.objects.filter(user=self.applicant).count(), 1
        )

    def test_anonymous_user_is_sent_to_login_not_the_policy(self):
        response = self.client.get(self.policy_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)
