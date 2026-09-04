from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import EmailOTP, User
from gate.audit import log_action


class Command(BaseCommand):
    """
    Delete sign-ups nobody ever finished.

    _release_abandoned_signups() (accounts/views.py) clears these out too,
    but only reactively — only when someone submits the register form again
    with the same username or email. A sign-up that's abandoned and never
    retried just sits there forever otherwise, despite the registration
    email itself promising "the unverified account will be removed
    automatically". This command is what actually keeps that promise, on a
    schedule, independent of anyone coming back.

    Deliberately the same scope as _release_abandoned_signups: inactive,
    unverified, never logged in, and — the part that matters — every
    registration code ever issued to the account has expired. A row with
    a still-live code is somebody who might complete verification in the
    next few minutes; touching it here would be the exact bug the manual
    fix in accounts/views.py just undid, done again on a timer instead of
    a request.
    """
    help = (
        'Delete unverified applicant accounts whose registration code(s) '
        'have all expired and who never logged in.'
    )

    def handle(self, *args, **kwargs):
        now = timezone.now()

        candidates = User.objects.filter(
            is_active=False,
            email_verified=False,
            last_login__isnull=True,
            email_otps__purpose=EmailOTP.PURPOSE_REGISTER,
        ).distinct()

        still_live_ids = set(
            EmailOTP.objects.filter(
                purpose=EmailOTP.PURPOSE_REGISTER,
                used_at__isnull=True,
                expires_at__gt=now,
            ).values_list('user_id', flat=True)
        )

        to_delete = [u for u in candidates if u.pk not in still_live_ids]

        for user in to_delete:
            log_action(
                None,
                'signup_abandoned',
                f'Removed abandoned sign-up for {user.username} '
                f'({user.email}) — never verified, code(s) expired.',
                target_user=user.username,
                extra_data={'email': user.email, 'user_id': user.pk},
            )

        count = len(to_delete)
        if count:
            User.objects.filter(pk__in=[u.pk for u in to_delete]).delete()

        # verbosity=0 (how AbandonedSignupCleanupMiddleware invokes this on
        # every throttle window) has to actually mean quiet. BaseCommand
        # doesn't gate self.stdout.write() calls on its own — it just hands
        # the option through in kwargs — so this command has to check it
        # itself or "Removed 0 abandoned sign-up(s)." would print to the
        # production log on a timer, forever.
        if kwargs.get('verbosity', 1) > 0:
            self.stdout.write(f'Removed {count} abandoned sign-up(s).')
