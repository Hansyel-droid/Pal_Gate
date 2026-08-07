import json

from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User


@override_settings(API_KEYS=['valid-test-key'])
class ApiKeyAuthTests(TestCase):
    """
    api.authentication.require_api_key is the only thing standing between
    the internet (or anyone on the campus LAN) and the ESP32 gate
    endpoints. These endpoints are csrf_exempt precisely because hardware
    can't carry a CSRF token, which makes this check the entire perimeter.
    """

    def test_scan_without_key_is_rejected(self):
        response = self.client.post(
            reverse('api_scan'),
            data=json.dumps({'uid': 'ABC123', 'gate': 'Main Gate'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 401)
        self.assertFalse(response.json()['allowed'])

    def test_scan_with_wrong_key_is_rejected(self):
        response = self.client.post(
            reverse('api_scan'),
            data=json.dumps({'uid': 'ABC123', 'gate': 'Main Gate'}),
            content_type='application/json',
            HTTP_X_API_KEY='some-guessed-key',
        )
        self.assertEqual(response.status_code, 401)

    def test_scan_with_correct_key_is_authenticated(self):
        # Correct key + unregistered tag: authenticated, but the *gate
        # decision* is still a legitimate denial — the two are different
        # things and this asserts both.
        response = self.client.post(
            reverse('api_scan'),
            data=json.dumps({'uid': 'UNKNOWN-TAG', 'gate': 'Main Gate'}),
            content_type='application/json',
            HTTP_X_API_KEY='valid-test-key',
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body['allowed'])
        self.assertEqual(body['action'], 'denied')

    def test_non_ascii_key_is_rejected_not_crashed(self):
        # secrets.compare_digest refuses to compare str values holding
        # non-ASCII characters — it raises TypeError rather than returning
        # False. Django decodes request headers as latin-1, so a header of
        # raw high bytes reaches the comparison as a non-ASCII str and takes
        # the whole endpoint down with an unhandled 500, before any rate
        # limit applies and while writing a traceback to logs/errors.log on
        # every attempt. A bad key is a 401, whatever bytes it is made of.
        response = self.client.post(
            reverse('api_scan'),
            data=json.dumps({'uid': 'ABC123', 'gate': 'Main Gate'}),
            content_type='application/json',
            HTTP_X_API_KEY='vàlid-test-kéy',
        )
        self.assertEqual(response.status_code, 401)

    def test_non_ascii_key_is_rejected_on_every_hardware_endpoint(self):
        response = self.client.post(
            reverse('api_register_uid'),
            data=json.dumps({'uid': 'NEWTAG001'}),
            content_type='application/json',
            HTTP_X_API_KEY='\xff\xfe',
        )
        self.assertEqual(response.status_code, 401)

        response = self.client.get(
            reverse('api_gate_status'), HTTP_X_API_KEY='\xff\xfe'
        )
        self.assertEqual(response.status_code, 401)

    def test_register_uid_requires_key(self):
        response = self.client.post(
            reverse('api_register_uid'),
            data=json.dumps({'uid': 'NEWTAG001'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 401)

    def test_gate_status_requires_key(self):
        response = self.client.get(reverse('api_gate_status'))
        self.assertEqual(response.status_code, 401)

    def test_gate_status_with_key_succeeds(self):
        response = self.client.get(
            reverse('api_gate_status'), HTTP_X_API_KEY='valid-test-key'
        )
        self.assertEqual(response.status_code, 200)

    def test_latest_pending_uid_rejects_hardware_key_uses_session_instead(self):
        # This endpoint is deliberately session+role protected, not API-key
        # protected (it's polled by an authenticated admin's browser, not
        # hardware) — an API key alone must NOT be enough to reach it.
        response = self.client.get(
            reverse('api_latest_pending_uid'), HTTP_X_API_KEY='valid-test-key'
        )
        self.assertEqual(response.status_code, 302)  # redirected to login

        admin = User.objects.create_user(
            username='admin1', password='pw-1234567', role='admin'
        )
        self.client.force_login(admin)
        response = self.client.get(reverse('api_latest_pending_uid'))
        self.assertEqual(response.status_code, 200)
