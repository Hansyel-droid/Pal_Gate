import functools
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

        if not api_key or api_key not in valid_keys:
            return JsonResponse(
                {
                    'error': 'Unauthorized. Valid API key required.',
                    'allowed': False,
                },
                status=401
            )

        return view_func(request, *args, **kwargs)

    return wrapper
