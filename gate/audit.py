from .models import AuditLog


def get_client_ip(request):
    """Extract real IP from request, handling proxies."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


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