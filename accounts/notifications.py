"""
In-app notifications — the red-dot inbox in the topbar.

This is a thin, deliberately dumb layer: create a row, that's it. No
digesting, no batching, no "mark stale ones read automatically". The
call sites in applications/notifications.py, appointments/services.py and
sticker_admin/views.py are the same ones that already send the email
equivalent — see accounts.models.Notification for why the two are kept
independent rather than one calling the other.
"""
from .models import Notification, User


def notify_user(user, message, link=''):
    """
    Create one notification for one person. Silently skips a None user
    (e.g. a legacy record with no linked account) rather than raising —
    the caller is usually deep inside a transaction it doesn't want to
    fail over a notification.
    """
    if user is None:
        return None
    return Notification.objects.create(recipient=user, message=message, link=link)


def notify_admins(message, link=''):
    """
    Create one notification per active admin account — used for events
    the whole admin team should see, like a new application entering the
    review queue, rather than a single applicant's own update.

    Scoped to role='admin' specifically (not 'security'): sticker_admin's
    own views are all @role_required('admin'), so a security account
    could never open the link anyway.
    """
    admins = User.objects.filter(role='admin', is_active=True)
    Notification.objects.bulk_create([
        Notification(recipient=admin, message=message, link=link)
        for admin in admins
    ])
