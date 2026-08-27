from urllib.parse import urlencode

from django.conf import settings
from django.shortcuts import redirect
from django.contrib.auth import logout
from django.contrib import messages
from django.urls import reverse
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


class CampusPolicyMiddleware:
    """
    Applicants must read and accept the Campus Access Policy before they
    can use the system. Anyone who hasn't accepted the version currently in
    force is redirected to the policy page.

    Applicants only. Admins and security staff are deliberately not gated:
    the guard on duty being unable to open the gate screen at the start of
    a shift because of an unread notice would be a safety problem, not a
    compliance win. Their duties under this memorandum are enforced through
    employment, not through this app.

    Runs AFTER IdleTimeoutMiddleware in the MIDDLEWARE list, so an idle
    session is logged out before it can be redirected to a page it will
    immediately be bounced off again.
    """

    # Paths that must stay reachable even by someone who hasn't accepted.
    # Getting this list wrong is how you build a redirect loop or trap a
    # person in a page they cannot leave, so each entry is here on purpose:
    #   - the policy page itself (obviously)
    #   - logout, so refusing to accept doesn't strand them signed in
    #   - the auth pages, which unauthenticated users need anyway
    #   - the Django admin, whose own auth is separate from this flow
    #   - static/media, so the page can render its own CSS
    EXEMPT_PREFIXES = (
        '/accounts/',
        '/palsu-system-admin-2025/',
        '/static/',
        '/media/',
        '/api/',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)

        if (
            user is not None
            and user.is_authenticated
            and getattr(user, 'role', None) == 'applicant'
            and not request.path.startswith(self.EXEMPT_PREFIXES)
        ):
            # Imported here rather than at module level: this module is
            # imported from settings' MIDDLEWARE at startup, and reaching
            # into models that early raises AppRegistryNotReady.
            from .policy import has_accepted_current_policy

            if not has_accepted_current_policy(user):
                policy_url = reverse('campus_policy')
                # Carry where they were headed so accepting drops them
                # back there instead of a generic dashboard.
                query = urlencode({'next': request.get_full_path()})
                return redirect(f'{policy_url}?{query}')

        return self.get_response(request)
