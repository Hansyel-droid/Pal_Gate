from django.db import models
from applications.models import StickerApplication

# Bookable inspection times for a single day, 8:00 AM to 4:00 PM in 30-minute
# increments, with a lunch break skipped (12:00-1:00 PM). Shared at module
# level so both models below can reference it without import ordering
# issues.
TIME_CHOICES = [
    ('08:00', '8:00 AM'),
    ('08:30', '8:30 AM'),
    ('09:00', '9:00 AM'),
    ('09:30', '9:30 AM'),
    ('10:00', '10:00 AM'),
    ('10:30', '10:30 AM'),
    ('11:00', '11:00 AM'),
    ('11:30', '11:30 AM'),
    ('13:00', '1:00 PM'),
    ('13:30', '1:30 PM'),
    ('14:00', '2:00 PM'),
    ('14:30', '2:30 PM'),
    ('15:00', '3:00 PM'),
    ('15:30', '3:30 PM'),
    ('16:00', '4:00 PM'),
]


class AppointmentSlotQuerySet(models.QuerySet):
    def with_booked_count(self):
        """
        Annotates each slot with how many appointments it holds, so views
        that render a list of dates don't fire one COUNT per row. Read by
        AppointmentSlot.total_booked() / .is_full() / .has_bookings().
        """
        return self.annotate(booked_count=models.Count('appointments'))


class AppointmentSlot(models.Model):
    """
    Represents a single day that is open for physical inspections.
    The administrator activates these dates.

    'capacity' is PER TIME SLOT, not per day — e.g. capacity=20 means 20
    applicants can book 8:00 AM on this day, another 20 can book 8:30 AM,
    and so on for every time in TIME_CHOICES. A day's total capacity is
    therefore capacity * len(TIME_CHOICES).
    """
    date = models.DateField(unique=True)
    is_active = models.BooleanField(default=True)
    capacity = models.IntegerField(default=20)

    objects = AppointmentSlotQuerySet.as_manager()

    def booked_count_for_time(self, time):
        """How many applicants have already booked this exact time today."""
        return self.appointments.filter(time=time).count()

    def remaining_for_time(self, time):
        """How many seats are left for this exact time today."""
        return self.capacity - self.booked_count_for_time(time)

    def is_time_full(self, time):
        return self.remaining_for_time(time) <= 0

    def total_capacity(self):
        """Combined capacity across every bookable time today."""
        return self.capacity * len(TIME_CHOICES)

    def total_booked(self):
        """
        Total appointments booked today, across all times.

        Prefers a `booked_count` annotation when the caller supplied one
        (see AppointmentSlot.objects.with_booked_count()). Without that,
        every slot rendered in a list would fire its own COUNT query —
        which is exactly the N+1 the applicant's date picker and the admin
        date list both hit, since both call this once per row.
        """
        annotated = getattr(self, 'booked_count', None)
        if annotated is not None:
            return annotated
        return self.appointments.count()

    def slots_remaining(self):
        """How many open spots (any time) are left on this day."""
        return self.total_capacity() - self.total_booked()

    def is_full(self):
        """Returns True if every time today has reached capacity."""
        return self.slots_remaining() <= 0

    def has_bookings(self):
        """
        Whether anything is booked today at all — used to decide if a date
        is safe to delete. Reuses the same annotation as total_booked() so
        it costs nothing extra in a list.
        """
        return self.total_booked() > 0

    def __str__(self):
        return f"{self.date} ({self.slots_remaining()} slots remaining)"

    class Meta:
        ordering = ['date']


class Appointment(models.Model):
    """
    A specific appointment an applicant chose for their inspection.
    Links to an AppointmentSlot (the day) and stores the exact time.
    """
    TIME_CHOICES = TIME_CHOICES

    # Each application gets exactly one appointment
    application = models.OneToOneField(
        StickerApplication,
        on_delete=models.CASCADE,
        related_name='appointment'
    )

    slot = models.ForeignKey(
        AppointmentSlot,
        on_delete=models.CASCADE,
        related_name='appointments'
    )

    time = models.CharField(max_length=5, choices=TIME_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.application.full_name} – {self.slot.date} at {self.time}"

    class Meta:
        ordering = ['slot__date', 'time']
        # NOTE: no longer unique_together on (slot, time) — many applicants
        # now share the same (day, time) pair, up to
        # AppointmentSlot.capacity each. Capacity is enforced in
        # appointments.services.book_appointment, not at the DB level,
        # since it's a "count of rows", not a uniqueness rule.