from datetime import datetime, timedelta
from django.db import transaction
from django.db.models import Count
from django.urls import reverse
from .models import AppointmentSlot, Appointment
from applications.models import RegistrationWindow
from applications.notifications import notify_appointment_assigned
from accounts.notifications import notify_admins


def _bookable_start_date():
    """
    The earliest date an applicant is allowed to book an inspection for —
    the day after the current registration window closes, mirroring the
    old rule that applicants apply during the window, then come in for
    inspection once it's done. Falls back to tomorrow if no window is
    active (shouldn't normally happen, since apply_step1 already blocks
    submissions without an active window).
    """
    try:
        window = RegistrationWindow.objects.filter(is_active=True).latest('created_at')
        return window.end_date + timedelta(days=1)
    except RegistrationWindow.DoesNotExist:
        return datetime.today().date() + timedelta(days=1)


def get_bookable_dates():
    """
    Active AppointmentSlot days an applicant can currently choose from.

    Annotated with booked_count so the template can call .is_full and
    .slots_remaining on every date without firing a COUNT per row — this
    page renders one card per open date, so that adds up fast during a
    registration rush.
    """
    return AppointmentSlot.objects.with_booked_count().filter(
        is_active=True,
        date__gte=_bookable_start_date(),
    ).order_by('date')


def get_bookable_slot(slot_id):
    """
    Fetches a slot by id, but only if it's actually one the applicant is
    allowed to book right now. Returns None instead of raising, so callers
    can treat "invalid" and "no longer bookable" the same way (e.g. an
    admin deactivated it, or it's now in the past).
    """
    return get_bookable_dates().filter(pk=slot_id).first()


def get_time_options(slot):
    """
    Every bookable time for this day, annotated with how many seats are
    left — what the applicant-facing time picker renders.
    """
    booked_counts = dict(
        slot.appointments.values('time')
        .annotate(count=Count('id'))
        .values_list('time', 'count')
    )
    options = []
    for value, label in Appointment.TIME_CHOICES:
        booked = booked_counts.get(value, 0)
        remaining = max(slot.capacity - booked, 0)
        options.append({
            'value': value,
            'label': label,
            'remaining': remaining,
            'is_full': remaining <= 0,
        })
    return options


def book_appointment(application, slot_id, time):
    """
    Books the specific (date, time) the applicant chose earlier in the
    wizard. Locks the parent AppointmentSlot row for the duration of the
    check-then-create, so a burst of concurrent submissions targeting the
    same day can't all read "still has room" for the same time and
    overbook it past capacity.

    Returns the created Appointment, or None if it's no longer available
    (full, deactivated, or otherwise invalid) — the caller should send the
    applicant back to the picker rather than assume success.
    """
    with transaction.atomic():
        try:
            slot = AppointmentSlot.objects.select_for_update().get(
                pk=slot_id, is_active=True,
            )
        except AppointmentSlot.DoesNotExist:
            return None

        valid_times = dict(Appointment.TIME_CHOICES)
        if time not in valid_times:
            return None

        booked = slot.appointments.filter(time=time).count()
        if booked >= slot.capacity:
            return None

        appointment = Appointment.objects.create(
            application=application,
            slot=slot,
            time=time,
        )

        application.status = 'scheduled'
        application.save()

        # Deferred until the transaction actually commits — if anything
        # later in the caller's outer transaction (e.g.
        # applications.views.apply_step4) rolls back, the applicant was
        # never told about an appointment that doesn't exist.
        transaction.on_commit(
            lambda: notify_appointment_assigned(application, appointment)
        )

        # This is the one place a brand new application reliably passes
        # through — book_appointment runs once per submission (initial or
        # renewal), right after the application row is created. That makes
        # it the single hook for "an admin needs to look at this", rather
        # than duplicating the notification at every call site that can
        # create an application.
        transaction.on_commit(
            lambda: notify_admins(
                f'New application from {application.applicant.get_full_name()} '
                f'— plate {application.plate_number}.',
                link=reverse('application_detail', args=[application.pk]),
            )
        )

        return appointment
