from django.conf import settings
from django.shortcuts import redirect
from django.contrib.auth import logout
from django.contrib import messages
from django.utils import timezone


class IdleTimeoutMiddleware:
    """
    Logs out users who have been idle for more than IDLE_TIMEOUT seconds.
    Default: 30 minutes.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.timeout = getattr(settings, 'IDLE_TIMEOUT', 1800)

    def __call__(self, request):
        if request.user.is_authenticated:
            last_activity_str = request.session.get('last_activity')

            if last_activity_str:
                try:
                    # Use timezone-aware datetime throughout
                    from datetime import datetime, timezone as dt_timezone
                    last_activity = datetime.fromisoformat(last_activity_str)

                    # Make timezone-aware if naive (handles both cases)
                    if last_activity.tzinfo is None:
                        last_activity = last_activity.replace(
                            tzinfo=dt_timezone.utc
                        )

                    now = timezone.now()
                    elapsed = (now - last_activity).total_seconds()

                    if elapsed > self.timeout:
                        logout(request)
                        messages.warning(
                            request,
                            'You have been logged out due to 30 minutes of '
                            'inactivity. Please log in again.'
                        )
                        return redirect('login')
                except (ValueError, TypeError):
                    # Malformed timestamp — reset it
                    pass

            # Refresh last activity on every request
            request.session['last_activity'] = timezone.now().isoformat()

        return self.get_response(request)
