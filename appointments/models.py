from django.db import models
from applications.models import StickerApplication


class AppointmentSlot(models.Model):
    """
    Represents a single day that is open for physical inspections.
    The administrator activates these dates.
    Each slot holds up to 'capacity' appointments (default 20).
    """
    date = models.DateField(unique=True)
    is_active = models.BooleanField(default=True)
    capacity = models.IntegerField(default=20)

    def slots_remaining(self):
        """How many open spots are left on this day."""
        return self.capacity - self.appointments.count()

    def is_full(self):
        """Returns True if no more appointments can be booked."""
        return self.slots_remaining() <= 0

    def __str__(self):
        return f"{self.date} ({self.slots_remaining()} slots remaining)"

    class Meta:
        ordering = ['date']


class Appointment(models.Model):
    """
    A specific appointment assigned to one application.
    Links to an AppointmentSlot (the day) and stores the exact time.
    """
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
        ('16:30', '4:30 PM'),
    ]

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
        # Prevent double-booking the same time slot on the same day
        unique_together = ['slot', 'time']