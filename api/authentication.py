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

        # Constant-time comparison — a plain `in` check leaks timing
        # information about how many characters of a guess were correct.
        is_valid = bool(api_key) and any(
            secrets.compare_digest(api_key, key) for key in valid_keys
        )

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
