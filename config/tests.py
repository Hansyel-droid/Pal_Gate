"""
Tests for the deployment-shaped settings — the ones whose value depends on
whether a reverse proxy is in front of Django.

These have to boot the settings module in a subprocess with a specific
environment, because the settings in question are decided by `if` branches
at import time. override_settings() can't re-run those branches, so an
in-process test would only ever assert against the developer's own .env and
would never catch a regression in the branch itself.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

BASE_DIR = Path(__file__).resolve().parent.parent

# Where possible this asks the libraries what they RESOLVE for a synthetic
# request, rather than reading back the knob we set. An earlier version of
# this file asserted AXES_IPWARE_PROXY_COUNT == 1 and passed while axes went
# on returning 127.0.0.1 — the knob is inert unless django-ipware is
# installed, which it isn't. Assert the outcome, not the configuration.
_PROBE = r"""
import json, os, sys
sys.path.insert(0, os.getcwd())
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
import django
django.setup()
from django.conf import settings
from django.test import RequestFactory
from django_ratelimit.core import _get_ip
from axes.helpers import get_client_ip_address
from gate.audit import get_client_ip

# One request as nginx would deliver it: REMOTE_ADDR is the proxy, and
# X-Forwarded-For carries the address nginx actually observed. The forged
# leading entry is what a client sends to try to look like someone else --
# nginx appends to it rather than replacing it.
request = RequestFactory().post(
    '/accounts/login/',
    REMOTE_ADDR='127.0.0.1',
    HTTP_X_FORWARDED_FOR='203.0.113.9, 10.0.0.5',
)
print('---SETTINGS---')
print(json.dumps({
    'SECURE_PROXY_SSL_HEADER': settings.SECURE_PROXY_SSL_HEADER,
    'SECURE_SSL_REDIRECT': settings.SECURE_SSL_REDIRECT,
    'SESSION_COOKIE_SECURE': settings.SESSION_COOKIE_SECURE,
    'CSRF_COOKIE_SECURE': settings.CSRF_COOKIE_SECURE,
    'SESSION_COOKIE_HTTPONLY': settings.SESSION_COOKIE_HTTPONLY,
    'ratelimit_resolves': _get_ip(request),
    'axes_resolves': get_client_ip_address(request),
    'audit_resolves': get_client_ip(request),
}))
"""


def resolve_settings(**env_overrides):
    """Boot config.settings under the given environment and hand back the
    settings Django ended up with."""
    env = dict(os.environ)
    # python-decouple reads os.environ ahead of the .env file, so these win
    # over whatever the developer has locally.
    env.update({k: str(v) for k, v in env_overrides.items()})
    result = subprocess.run(
        [sys.executable, '-c', _PROBE],
        cwd=str(BASE_DIR), env=env, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            f'settings failed to import:\n{result.stdout}\n{result.stderr}'
        )
    payload = result.stdout.split('---SETTINGS---', 1)[1]
    return json.loads(payload)


class ReverseProxySettingsTests(SimpleTestCase):
    """
    DEPLOYMENT.md puts nginx in front of Django, terminating TLS and
    proxying to 127.0.0.1:8000 over plain http. Everything below is about
    Django drawing the right conclusions from that arrangement.
    """

    def test_https_behind_proxy_does_not_cause_a_redirect_loop(self):
        # Django sees http; only X-Forwarded-Proto says otherwise. Without
        # SECURE_PROXY_SSL_HEADER, request.is_secure() is False, so
        # SECURE_SSL_REDIRECT 301s to https -> nginx proxies it back as
        # http -> forever. The site is unreachable the moment HTTPS is
        # switched on.
        resolved = resolve_settings(HTTPS_ENABLED=True, TRUST_X_FORWARDED_FOR=True)
        self.assertTrue(resolved['SECURE_SSL_REDIRECT'])
        self.assertEqual(
            resolved['SECURE_PROXY_SSL_HEADER'],
            ['HTTP_X_FORWARDED_PROTO', 'https'],
        )

    def test_forwarded_proto_is_not_trusted_without_a_proxy(self):
        # The other half. With nothing in front of Django, any client can
        # send X-Forwarded-Proto: https themselves — honouring it would let
        # them make request.is_secure() lie and skip the SSL redirect.
        resolved = resolve_settings(HTTPS_ENABLED=True, TRUST_X_FORWARDED_FOR=False)
        self.assertIsNone(resolved['SECURE_PROXY_SSL_HEADER'])

    def test_throttles_key_on_the_real_client_behind_a_proxy(self):
        # django-ratelimit and django-axes both read REMOTE_ADDR by default
        # and neither consults TRUST_X_FORWARDED_FOR — that setting only fed
        # gate.audit.get_client_ip. Behind nginx REMOTE_ADDR is 127.0.0.1 for
        # everyone, which collapses every per-IP throttle into one shared
        # bucket: five bad logins from any one person would lock out every
        # account on campus for AXES_COOLOFF_TIME.
        resolved = resolve_settings(TRUST_X_FORWARDED_FOR=True)
        self.assertEqual(resolved['ratelimit_resolves'], '10.0.0.5')
        self.assertEqual(resolved['axes_resolves'], '10.0.0.5')

    def test_forged_forwarded_for_prefix_is_ignored(self):
        # nginx uses $proxy_add_x_forwarded_for, which APPENDS the address it
        # observed to whatever the client sent. So a client sending
        # "X-Forwarded-For: 203.0.113.9" produces "203.0.113.9, <them>", and
        # reading the left-most entry would let them pick their own throttle
        # bucket per request — sidestepping the rate limit and the lockout
        # entirely, and poisoning the audit log while they were at it.
        resolved = resolve_settings(TRUST_X_FORWARDED_FOR=True)
        for consumer in ('ratelimit_resolves', 'axes_resolves', 'audit_resolves'):
            with self.subTest(consumer=consumer):
                self.assertNotEqual(resolved[consumer], '203.0.113.9')
                self.assertEqual(resolved[consumer], '10.0.0.5')

    def test_throttles_key_on_remote_addr_without_a_proxy(self):
        # Mirror image: with no proxy, X-Forwarded-For is client-controlled
        # end to end, so honouring any part of it would let an attacker
        # sidestep both the rate limit and the lockout by varying one header.
        resolved = resolve_settings(TRUST_X_FORWARDED_FOR=False)
        self.assertEqual(resolved['ratelimit_resolves'], '127.0.0.1')
        self.assertEqual(resolved['axes_resolves'], '127.0.0.1')
        self.assertEqual(resolved['audit_resolves'], '127.0.0.1')


class CookieSecuritySettingsTests(SimpleTestCase):
    """The flags that ride on HTTPS_ENABLED."""

    def test_secure_cookies_follow_https_enabled(self):
        resolved = resolve_settings(HTTPS_ENABLED=True)
        self.assertTrue(resolved['SESSION_COOKIE_SECURE'])
        self.assertTrue(resolved['CSRF_COOKIE_SECURE'])
        self.assertTrue(resolved['SECURE_SSL_REDIRECT'])

    def test_plain_http_deployment_does_not_set_secure_cookies(self):
        # A Secure cookie over plain http is simply never sent back, which
        # locks everyone out of a LAN deployment rather than protecting it.
        resolved = resolve_settings(HTTPS_ENABLED=False)
        self.assertFalse(resolved['SESSION_COOKIE_SECURE'])
        self.assertFalse(resolved['CSRF_COOKIE_SECURE'])

    def test_session_cookie_is_httponly_either_way(self):
        # Not conditional on anything — JS has no reason to read it.
        for https in (True, False):
            with self.subTest(https=https):
                self.assertTrue(
                    resolve_settings(HTTPS_ENABLED=https)['SESSION_COOKIE_HTTPONLY']
                )
