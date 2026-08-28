import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse

from accounts.notifications import notify_user

logger = logging.getLogger('django')


def _send(application, subject, template_name, context=None):
    """
    Sends a plain-text status email to the applicant who owns this
    application.

    This is currently the ONLY notification channel in the system (see
    PRODUCT.md) — an applicant otherwise only learns what happened by
    logging back in and checking their status. That makes two things
    important here:

    1. Silently do nothing if the applicant has no email on file. Every
       account created through the portal has one, but legacy walk-in
       records from the removed quick-register flow don't (they were made
       with set_unusable_password() and no email, since they were never
       portal logins) — there's nobody to notify.
    2. Never let a mail failure break the admin action that triggered it.
       Approving, rejecting, scheduling, or issuing a sticker must always
       succeed even if the mail server is down or misconfigured — the
       failure is logged instead so it doesn't silently disappear.
    """
    applicant = application.applicant
    if not applicant.email:
        return False

    ctx = {'application': application, 'applicant': applicant}
    ctx.update(context or {})

    try:
        body = render_to_string(f'emails/{template_name}.txt', ctx)
        send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[applicant.email],
            fail_silently=False,
        )
        return True
    except Exception:
        logger.exception(
            'Failed to send "%s" email for application #%s to %s',
            template_name, application.pk, applicant.email,
        )
        return False


def notify_appointment_assigned(application, appointment):
    _send(
        application,
        subject='PalawanSU Gate — Your inspection appointment is scheduled',
        template_name='appointment_assigned',
        context={'appointment': appointment},
    )
    notify_user(
        application.applicant,
        f'Your inspection for plate {application.plate_number} is scheduled '
        f'for {appointment.slot.date:%B %d, %Y}.',
        link=reverse('my_applications'),
    )


def notify_approved(application):
    _send(
        application,
        subject='PalawanSU Gate — Your sticker application was approved',
        template_name='application_approved',
    )
    notify_user(
        application.applicant,
        f'Your application for plate {application.plate_number} was approved.',
        link=reverse('my_applications'),
    )


def notify_rejected(application):
    _send(
        application,
        subject='PalawanSU Gate — Your sticker application needs attention',
        template_name='application_rejected',
    )
    notify_user(
        application.applicant,
        f'Your application for plate {application.plate_number} needs '
        f'attention — see the reason on your applications page.',
        link=reverse('my_applications'),
    )


def notify_sticker_issued(application):
    _send(
        application,
        subject='PalawanSU Gate — Your sticker has been issued',
        template_name='sticker_issued',
    )
    notify_user(
        application.applicant,
        f'Your sticker for plate {application.plate_number} has been issued.',
        link=reverse('my_applications'),
    )
