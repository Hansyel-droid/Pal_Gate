from django.conf import settings
from .models import AuditLog


def get_client_ip(request):
    """
    Extract the client IP from the request.

    X-Forwarded-For is only honored when TRUST_X_FORWARDED_FOR is enabled —
    with no reverse proxy in front of Django, that header can be set to
    anything by the client, and trusting it would let anyone forge their
    IP in the audit log.

    The LAST entry is the client, not the first. nginx is configured with
    `$proxy_add_x_forwarded_for` (DEPLOYMENT.md section 8), which appends the
    address it actually observed to whatever the client already sent. So a
    client that sends nothing produces "<client>", but a client that sends
    "X-Forwarded-For: 1.2.3.4" produces "1.2.3.4, <client>" — reading the
    left-most entry hands the attacker their own forged value. With exactly
    one trusted proxy appending exactly one entry, the right-most is the only
    address in the list nginx vouched for.
    """
    if getattr(settings, 'TRUST_X_FORWARDED_FOR', False):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            forwarded = [p.strip() for p in x_forwarded_for.split(',') if p.strip()]
            if forwarded:
                return forwarded[-1]
    return request.META.get('REMOTE_ADDR')


def throttle_client_ip(request):
    """
    The same address, for django-axes and django-ratelimit.

    Both libraries resolve the client IP themselves and both default to
    REMOTE_ADDR, which is the proxy behind nginx — that collapsed every
    per-IP throttle into one campus-wide bucket. Neither has ever consulted
    TRUST_X_FORWARDED_FOR, so they are pointed here instead (see
    AXES_CLIENT_IP_CALLABLE / RATELIMIT_IP_META_KEY in config/settings.py)
    and the lockout, the rate limit and the audit log now agree on who the
    client is by construction rather than by three parallel implementations.

    Differs from get_client_ip only in never returning None: the audit log
    stores a nullable address, but django-ratelimit feeds this straight into
    ipaddress.ip_network() and would raise on None.
    """
    return get_client_ip(request) or '0.0.0.0'


def log_action(request_or_user, action, description,
               target_user='', extra_data=None, ip=None):
    """
    Log an audit event. Call this from any view.

    Usage:
        from gate.audit import log_action
        log_action(request, 'app_approved', f'Approved application for {name}')
    """
    from accounts.models import User

    actor = None
    ip_address = ip

    if hasattr(request_or_user, 'user'):
        # It's a request object
        request = request_or_user
        actor = request.user if request.user.is_authenticated else None
        ip_address = ip or get_client_ip(request)
    elif isinstance(request_or_user, User):
        actor = request_or_user

    AuditLog.objects.create(
        actor=actor,
        action=action,
        description=description,
        ip_address=ip_address,
        target_user=target_user,
        extra_data=extra_data or {},
    )