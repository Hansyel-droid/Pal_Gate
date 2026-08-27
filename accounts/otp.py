"""
Issuing, emailing, and checking the one-time codes that confirm an
applicant owns the campus email address they signed up with.

Kept separate from views.py so the rules (expiry, attempt limit, resend
cooldown, what invalidates a code) live in one place and can be tested
without going through the HTTP layer.
"""

import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.mail import send_mail
from django.db.models import F
from django.template.loader import render_to_string
from django.utils import timezone

from .models import EmailOTP

logger = logging.getLogger('django')


def _generate_code():
    """A cryptographically random zero-padded numeric code."""
    upper_bound = 10 ** settings.OTP_LENGTH
    return f'{secrets.randbelow(upper_bound):0{settings.OTP_LENGTH}d}'


def latest_otp(user, purpose=EmailOTP.PURPOSE_REGISTER):
    """The most recent code issued to this user that hasn't been consumed."""
    return (
        EmailOTP.objects
        .filter(user=user, purpose=purpose, used_at__isnull=True)
        .order_by('-created_at')
        .first()
    )


def seconds_until_resend(user, purpose=EmailOTP.PURPOSE_REGISTER):
    """
    How long the applicant still has to wait before we'll send another
    code. 0 means they can request one now.

    Without this, the "Resend code" button is a free way to flood
    somebody else's inbox — the sender only needs to know their address.
    """
    otp = latest_otp(user, purpose)
    if otp is None:
        return 0

    ready_at = otp.created_at + timedelta(
        seconds=settings.OTP_RESEND_COOLDOWN_SECONDS
    )
    remaining = (ready_at - timezone.now()).total_seconds()
    return max(0, int(remaining + 0.999))


def issue_otp(user, purpose=EmailOTP.PURPOSE_REGISTER):
    """
    Retire any outstanding code for this user, mint a new one, and email
    it. Returns (otp, sent) — `sent` is False if the mail failed, so the
    caller can tell the applicant instead of leaving them staring at an
    empty inbox.
    """
    # Marking older rows used means only the newest code ever works,
    # so a resend cleanly supersedes whatever was sent before.
    EmailOTP.objects.filter(
        user=user, purpose=purpose, used_at__isnull=True
    ).update(used_at=timezone.now())

    code = _generate_code()
    otp = EmailOTP.objects.create(
        user=user,
        purpose=purpose,
        code_hash=make_password(code),
        expires_at=timezone.now() + timedelta(
            minutes=settings.OTP_EXPIRY_MINUTES
        ),
    )

    return otp, _email_code(user, code)


def _email_code(user, code):
    try:
        body = render_to_string('emails/registration_otp.txt', {
            'user': user,
            'code': code,
            'expiry_minutes': settings.OTP_EXPIRY_MINUTES,
        })
        send_mail(
            subject=f'PalawanSU Gate — Your verification code is {code}',
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
        return True
    except Exception:
        # Never let a mail outage 500 the sign-up page. The code is
        # already stored, so a resend once mail is back will work.
        logger.exception(
            'Failed to send registration code to %s', user.email
        )
        return False


def verify_otp(user, code, purpose=EmailOTP.PURPOSE_REGISTER):
    """
    Check a submitted code. Returns (ok, error_message).

    Every failure path burns an attempt, so guessing a 6-digit code is
    capped at OTP_MAX_ATTEMPTS tries per issued code rather than being
    limited only by how fast someone can POST.
    """
    otp = latest_otp(user, purpose)
    if otp is None:
        return False, 'That code is no longer valid. Request a new one below.'

    if otp.is_expired():
        return False, 'That code has expired. Request a new one below.'

    if otp.attempts >= settings.OTP_MAX_ATTEMPTS:
        return False, (
            'Too many incorrect attempts on this code. '
            'Request a new one below.'
        )

    if not check_password(code, otp.code_hash):
        EmailOTP.objects.filter(pk=otp.pk).update(attempts=F('attempts') + 1)
        otp.refresh_from_db(fields=['attempts'])
        remaining = max(settings.OTP_MAX_ATTEMPTS - otp.attempts, 0)
        if remaining == 0:
            return False, (
                'Incorrect code. Too many attempts — request a new one below.'
            )
        plural = '' if remaining == 1 else 's'
        return False, f'Incorrect code. {remaining} attempt{plural} left.'

    otp.used_at = timezone.now()
    otp.save(update_fields=['used_at'])
    return True, ''
