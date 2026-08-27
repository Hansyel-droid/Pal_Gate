import json
from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from gate.models import GateLog, PendingRFID

from api.rfid_auth import derive_tag_credentials, derive_tag_credentials_hex


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


class PendingUidStalenessTests(TestCase):
    """
    The issuing station polls latest_pending_uid every 3 seconds and
    auto-fills the RFID field from it, so whatever this endpoint returns is
    what a member of staff is about to bind to somebody's application.
    """

    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin1', password='pw-1234567', role='admin'
        )
        self.client.force_login(self.admin)

    def scanned_at(self, uid, ago):
        # registered_at is auto_now_add, so it has to be pushed back after
        # the fact rather than passed in.
        pending = PendingRFID.objects.create(uid=uid, claimed=False)
        PendingRFID.objects.filter(pk=pending.pk).update(
            registered_at=timezone.now() - ago
        )
        return pending

    def offered_uid(self):
        response = self.client.get(reverse('api_latest_pending_uid'))
        self.assertEqual(response.status_code, 200)
        return response.json()['uid']

    def test_a_fresh_scan_is_offered(self):
        self.scanned_at('FRESH-TAG', timedelta(seconds=30))
        self.assertEqual(self.offered_uid(), 'FRESH-TAG')

    def test_a_scan_past_the_ttl_is_not_offered(self):
        # Before the TTL this returned the newest unclaimed row however old
        # it was, so staff opening the page the next morning were handed
        # yesterday's UID with no indication it was stale.
        self.scanned_at('YESTERDAYS-TAG', PendingRFID.OFFER_TTL + timedelta(minutes=1))
        self.assertIsNone(self.offered_uid())

    def test_a_stale_scan_does_not_mask_a_fresh_one(self):
        self.scanned_at('OLD-TAG', PendingRFID.OFFER_TTL + timedelta(hours=3))
        self.scanned_at('NEW-TAG', timedelta(seconds=5))
        self.assertEqual(self.offered_uid(), 'NEW-TAG')

    def test_planted_uid_expires_instead_of_waiting_for_staff(self):
        # The attack the TTL is really for: anyone who can reach
        # /api/register-uid/ parks a UID of their choosing and waits for the
        # next person to issue a sticker, which would bind their own tag to
        # a legitimate application. It now has to be within OFFER_TTL of
        # someone actually issuing.
        self.scanned_at('PLANTED-TAG', PendingRFID.OFFER_TTL + timedelta(seconds=1))
        self.assertIsNone(self.offered_uid())


@override_settings(API_KEYS=['valid-test-key'])
class HardwareInputBoundsTests(TestCase):
    """
    uid and gate arrive as free-form JSON from the ESP32 and land straight
    in GateLog. SQLite doesn't enforce varchar length and
    Model.objects.create() skips full_clean(), so nothing in the stack was
    holding these to the lengths the model declares.
    """

    def post_scan(self, **body):
        return self.client.post(
            reverse('api_scan'),
            data=json.dumps(body),
            content_type='application/json',
            HTTP_X_API_KEY='valid-test-key',
        )

    def test_oversized_uid_is_rejected(self):
        response = self.post_scan(uid='A' * 5000, gate='Main Gate')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(GateLog.objects.exists())

    def test_oversized_gate_is_rejected(self):
        # gate_location is echoed onto the live dashboard's scope control,
        # which builds its list from the distinct values actually logged.
        response = self.post_scan(uid='ABC123', gate='G' * 5000)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(GateLog.objects.exists())

    def test_oversized_uid_is_rejected_on_register(self):
        response = self.client.post(
            reverse('api_register_uid'),
            data=json.dumps({'uid': 'A' * 5000}),
            content_type='application/json',
            HTTP_X_API_KEY='valid-test-key',
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(PendingRFID.objects.exists())

    def test_non_string_uid_is_rejected_not_crashed(self):
        # {"uid": 12345} used to reach .strip() on an int and 500 — an
        # authenticated crash, but a crash a misflashed reader could cause
        # by itself without anyone attacking anything.
        response = self.post_scan(uid=12345, gate='Main Gate')
        self.assertEqual(response.status_code, 400)

    def test_ordinary_values_still_pass(self):
        # The bound must not narrow what real hardware sends: a normal tag
        # UID at a gate name that isn't one of the two hardcoded ones (a
        # third reader should not need a code change to start scanning).
        response = self.post_scan(uid='04A2B3C4D5', gate='Service Gate')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            GateLog.objects.filter(gate_location='Service Gate').exists()
        )


@override_settings(
    API_KEYS=['valid-test-key'],
    RFID_MASTER_KEY='test-master-key-not-the-real-one',
)
class TagPasswordDerivationTests(TestCase):
    """
    NTAG215 tags are locked with a per-tag PWD_AUTH password derived from
    their UID (api/rfid_auth.py). Two properties have to hold or the scheme
    is worthless:

      - Deterministic. The tag is locked once, at registration, with
        whatever password the server derived that day. Every later
        authentication has to arrive at the identical 4 bytes from the UID
        alone. A derivation that drifts — between processes, across a
        restart, or because two clients normalise the UID differently —
        bricks the tag rather than protecting it, and does so silently,
        months later, at a gate.
      - Per-tag. The whole reason for HMAC-ing the UID instead of shipping
        one shared password is that recovering one password must not
        compromise any other tag.
    """

    def test_derivation_is_deterministic(self):
        first = derive_tag_credentials('04A2B3C4D5E6F0')
        second = derive_tag_credentials('04A2B3C4D5E6F0')
        self.assertEqual(first, second)

    def test_derivation_matches_a_fixed_vector(self):
        # Deterministic within one call is not the claim being made; the
        # claim is that a tag locked today still opens next year, from a
        # different process and a different release. A hard-coded vector
        # catches anything that would quietly change the derivation — a
        # different digest, a different truncation, a normalisation tweak —
        # which comparing two live calls to each other never would.
        credentials = derive_tag_credentials_hex('04A2B3C4D5E6F0')
        self.assertEqual(credentials['pwd'], '1D96A72B')
        self.assertEqual(credentials['pack'], '70F4')

    def test_different_uids_yield_different_passwords(self):
        pwd_a, pack_a = derive_tag_credentials('04A2B3C4D5E6F0')
        pwd_b, pack_b = derive_tag_credentials('04A2B3C4D5E6F1')
        self.assertNotEqual(pwd_a, pwd_b)
        self.assertNotEqual(pack_a, pack_b)

    def test_many_uids_yield_distinct_passwords(self):
        # A 4-byte password has 2^32 values, so a collision in 256 samples
        # is theoretically possible and practically not. If this ever fails,
        # the derivation has stopped depending on the UID properly — the one
        # failure that would still look fine in the two tests above.
        passwords = {
            derive_tag_credentials(f'04A2B3C4D5E6{n:02X}')[0]
            for n in range(256)
        }
        self.assertEqual(len(passwords), 256)

    def test_password_and_pack_are_the_sizes_ntag215_expects(self):
        # PWD is all 4 bytes of page 133; PACK is the first 2 bytes of page
        # 134. The firmware parses these into fixed-width buffers, so a
        # change in width here is a buffer overrun there.
        pwd, pack = derive_tag_credentials('04A2B3C4D5E6F0')
        self.assertEqual(len(pwd), 4)
        self.assertEqual(len(pack), 2)

        credentials = derive_tag_credentials_hex('04A2B3C4D5E6F0')
        self.assertEqual(len(credentials['pwd']), 8)
        self.assertEqual(len(credentials['pack']), 4)

    def test_hex_keeps_leading_zeros(self):
        # A password of 00FF1234 rendered as 'FF1234' is a different
        # password and a tag nobody can open. Find a UID whose first byte
        # derives to zero and assert the width survives the hex round-trip.
        for n in range(4096):
            uid = f'ZEROTEST{n:04X}'
            if derive_tag_credentials(uid)[0][0] == 0:
                rendered = derive_tag_credentials_hex(uid)['pwd']
                self.assertEqual(len(rendered), 8)
                self.assertTrue(rendered.startswith('00'))
                return
        self.skipTest('no UID with a leading zero byte in the sampled range')

    def test_uid_case_and_whitespace_do_not_change_the_password(self):
        # The registration firmware upper-cases its UIDs; a future gate
        # reader written by someone else might not. Both have to open the
        # same physical tag.
        canonical = derive_tag_credentials('04A2B3C4D5E6F0')
        self.assertEqual(derive_tag_credentials('04a2b3c4d5e6f0'), canonical)
        self.assertEqual(derive_tag_credentials('  04A2B3C4D5E6F0  '), canonical)

    def test_master_key_actually_participates(self):
        # A derivation that ignored the key would pass every test above
        # while being forgeable by anyone who can read a UID off a tag.
        under_test_key = derive_tag_credentials('04A2B3C4D5E6F0')
        with override_settings(RFID_MASTER_KEY='a-completely-different-key'):
            under_other_key = derive_tag_credentials('04A2B3C4D5E6F0')
        self.assertNotEqual(under_test_key, under_other_key)

    def test_blank_master_key_raises_rather_than_deriving_from_nothing(self):
        with override_settings(RFID_MASTER_KEY=''):
            with self.assertRaises(ValueError):
                derive_tag_credentials('04A2B3C4D5E6F0')

    def test_empty_uid_is_rejected(self):
        for bad in ('', '   '):
            with self.assertRaises(ValueError):
                derive_tag_credentials(bad)

    def test_every_derived_value_parses_under_the_firmware_rules(self):
        # The device parses these with a deliberately strict reader: exact
        # length, every character a hex digit, no leading-zero trimming
        # (hexToBytes() in registration_device_for_testing/src/main.cpp).
        # Anything the server can emit that the device would reject is a tag
        # that fails to lock in the field, so the contract is asserted here
        # rather than discovered there.
        import re

        pwd_shape = re.compile(r'^[0-9A-F]{8}$')
        pack_shape = re.compile(r'^[0-9A-F]{4}$')
        for n in range(1000):
            credentials = derive_tag_credentials_hex(f'04A2B3C4D5{n:04X}')
            self.assertRegex(credentials['pwd'], pwd_shape)
            self.assertRegex(credentials['pack'], pack_shape)


@override_settings(
    API_KEYS=['valid-test-key'],
    RFID_MASTER_KEY='test-master-key-not-the-real-one',
)
class RegisterUidCredentialResponseTests(TestCase):
    """
    The registration device gets one shot at the tag: it writes the password
    while the tag is still in the RF field, in the same interaction that
    registered it. That makes these response fields load-bearing rather than
    informational.
    """

    def register(self, uid='04A2B3C4D5E6F0'):
        return self.client.post(
            reverse('api_register_uid'),
            data=json.dumps({'uid': uid}),
            content_type='application/json',
            HTTP_X_API_KEY='valid-test-key',
        )

    def test_response_carries_pwd_and_pack(self):
        response = self.register()
        self.assertEqual(response.status_code, 200)
        body = response.json()

        expected = derive_tag_credentials_hex('04A2B3C4D5E6F0')
        self.assertEqual(body['pwd'], expected['pwd'])
        self.assertEqual(body['pack'], expected['pack'])

    def test_existing_response_fields_are_unchanged(self):
        # The sticker station and the already-deployed firmware both read
        # these. Adding fields must not have disturbed them.
        response = self.register()
        body = response.json()
        self.assertTrue(body['success'])
        self.assertEqual(body['uid'], '04A2B3C4D5E6F0')

    def test_registration_still_stores_the_pending_uid(self):
        self.register()
        self.assertTrue(
            PendingRFID.objects.filter(
                uid='04A2B3C4D5E6F0', claimed=False
            ).exists()
        )

    def test_credentials_are_not_exposed_without_an_api_key(self):
        response = self.client.post(
            reverse('api_register_uid'),
            data=json.dumps({'uid': '04A2B3C4D5E6F0'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 401)
        self.assertNotIn('pwd', response.json())

    def test_two_tags_registered_in_sequence_get_different_credentials(self):
        first = self.register('04A2B3C4D5E6F0').json()
        second = self.register('04A2B3C4D5E6F1').json()
        self.assertNotEqual(first['pwd'], second['pwd'])

    def test_blank_master_key_fails_without_leaving_a_pending_registration(self):
        # A pending UID the device was never told how to lock would be
        # offered to the sticker station and issued as though it were
        # protected. Better to fail the registration outright.
        with override_settings(RFID_MASTER_KEY=''):
            response = self.register()
        self.assertEqual(response.status_code, 500)
        self.assertFalse(PendingRFID.objects.exists())
