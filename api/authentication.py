import functools
import secrets
from django.conf import settings
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
