import functools
import hashlib
import hmac
import secrets
import time
from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse


def require_api_key(view_func):
    """
    Decorator that checks for a valid API key in the X-API-Key header.
    ESP32 devices must include: X-API-Key: <key>

    Keys are configured in settings.API_KEYS (loaded from .env).
    """
    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        api_key = request.headers.get('X-API-Key', '').strip()
        valid_keys = getattr(settings, 'API_KEYS', [])

        # Compared as bytes, not str. secrets.compare_digest refuses to
        # compare str values holding non-ASCII characters — it raises
        # TypeError rather than returning False. Django decodes request
        # headers as latin-1, so `X-API-Key: <any high byte>` reached the
        # comparison as a non-ASCII str and took the endpoint down with an
        # unhandled 500 (before the rate limit, and writing a traceback to
        # logs/errors.log every time). Encoding first makes a wrong key a
        # wrong key regardless of what bytes it is made of.
        supplied = api_key.encode('utf-8', 'surrogateescape')

        # Constant-time comparison — a plain `in` check leaks timing
        # information about how many characters of a guess were correct.
        # Accumulated rather than short-circuited with any(), so the work
        # done doesn't depend on which configured key matched.
        is_valid = False
        if supplied:
            for key in valid_keys:
                if secrets.compare_digest(supplied, key.encode('utf-8')):
                    is_valid = True

        if not is_valid:
            return JsonResponse(
                {
                    'error': 'Unauthorized. Valid API key required.',
                    'allowed': False,
                },
                status=401
            )

        return view_func(request, *args, **kwargs)

    return wrapper


# How far a device's clock may drift from the server's before a signed
# request is refused, in either direction. Generous enough for an ESP32's
# NTP sync (normally accurate to well under a second) plus ordinary request
# latency; tight enough that a captured signature is useless for long.
SIGNATURE_WINDOW_SECONDS = 120


def _unauthorized(reason):
    return JsonResponse(
        {'error': f'Unauthorized. {reason}', 'allowed': False},
        status=401,
    )


def _verify_signature(device_id, timestamp_str, signature_hex, body_bytes):
    """
    Recomputes HMAC-SHA256("<device_id>|<timestamp>|<raw request body>",
    key=that device's secret) and compares it to what the device sent.

    Signing the raw body bytes rather than re-derived fields means the
    check covers exactly what was actually sent — a byte changed anywhere
    in the JSON invalidates the signature — and both sides only ever need
    to agree on one thing: the exact bytes on the wire, not a shared
    canonical re-encoding of them.
    """
    device_secrets = getattr(settings, 'DEVICE_SECRETS', {})
    secret = device_secrets.get(device_id)
    if not secret:
        return False

    message = device_id.encode('utf-8') + b'|' + timestamp_str.encode('utf-8') + b'|' + body_bytes
    expected = hmac.new(secret.encode('utf-8'), message, hashlib.sha256).hexdigest()

    supplied = signature_hex.encode('utf-8', 'surrogateescape')
    return secrets.compare_digest(expected.encode('utf-8'), supplied)


def require_device_auth(view_func):
    """
    Authenticates an ESP32 device request one of two ways, tried in order:

    1. Signed (preferred) — X-Device-ID, X-Timestamp, X-Signature headers.
       The device computes HMAC-SHA256 over its own device id, the current
       Unix timestamp, and the exact request body, using a secret that
       never travels on the wire (see _verify_signature). This proves two
       things a shared static key cannot: which specific device sent the
       request, and that this exact request — not a copy of one seen
       earlier — is what triggered it.

       Requires the timestamp to be within SIGNATURE_WINDOW_SECONDS of the
       server's own clock, and each (device, timestamp, signature) triple
       can only be accepted once — cache.add() is atomic, so two requests
       racing on the same instant can't both pass, the same primitive
       AbandonedSignupCleanupMiddleware relies on elsewhere in this project.

    2. Legacy — a bare X-API-Key, checked against every configured device
       secret with no identity attached. This is the original scheme,
       kept only so the registration device and the gate scanner can each
       be reflashed with signed-request firmware on their own schedule
       instead of both at once breaking the moment this decorator ships.
       Remove this branch once both are confirmed migrated.

    Either path sets request.device_id ('unknown' for the legacy path,
    since a shared key can't say who sent it) and enforces
    settings.DEVICE_ALLOWED_ENDPOINTS, so one device's credentials cannot
    be used to call another device's endpoint — the legacy path can't be
    scoped this way, which is exactly the gap signing closes.
    """
    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        device_secrets = getattr(settings, 'DEVICE_SECRETS', {})
        allowed_endpoints = getattr(settings, 'DEVICE_ALLOWED_ENDPOINTS', {})
        endpoint_name = view_func.__name__

        device_id = request.headers.get('X-Device-ID', '').strip()
        timestamp_str = request.headers.get('X-Timestamp', '').strip()
        signature = request.headers.get('X-Signature', '').strip()

        if device_id or timestamp_str or signature:
            # Partial signed headers is refused outright rather than falling
            # through to the legacy path — a device attempting to sign and
            # getting it wrong should see a clear failure, not silently be
            # accepted through the weaker check because one header was cut off.
            if not (device_id and timestamp_str and signature):
                return _unauthorized('Incomplete signed request.')

            if device_id not in device_secrets:
                return _unauthorized('Unknown device.')

            try:
                timestamp = int(timestamp_str)
            except ValueError:
                return _unauthorized('Malformed timestamp.')

            if abs(time.time() - timestamp) > SIGNATURE_WINDOW_SECONDS:
                return _unauthorized('Timestamp outside the allowed window.')

            if not _verify_signature(device_id, timestamp_str, signature, request.body):
                return _unauthorized('Signature does not match.')

            replay_key = f'device_sig_seen:{device_id}:{timestamp_str}:{signature[:16]}'
            if not cache.add(replay_key, True, timeout=SIGNATURE_WINDOW_SECONDS + 10):
                return _unauthorized('This request has already been used.')

            if endpoint_name not in allowed_endpoints.get(device_id, set()):
                return _unauthorized('This device may not call this endpoint.')

            request.device_id = device_id
        else:
            # ---- Legacy shared-key path (no device identity attached) ----
            # Checked against settings.API_KEYS rather than DEVICE_SECRETS
            # directly — in the real settings.py the former is always
            # derived from the latter, but keeping this read the same
            # setting require_api_key does means the two decorators stay
            # interchangeable for anything that configures keys for tests.
            api_key = request.headers.get('X-API-Key', '').strip()
            supplied = api_key.encode('utf-8', 'surrogateescape')
            valid_keys = getattr(settings, 'API_KEYS', [])

            is_valid = False
            if supplied:
                for key in valid_keys:
                    if secrets.compare_digest(supplied, key.encode('utf-8')):
                        is_valid = True

            if not is_valid:
                return _unauthorized('Valid device credentials required.')

            request.device_id = 'unknown'

        return view_func(request, *args, **kwargs)

    return wrapper
