from datetime import datetime, timedelta, time
from .models import AppointmentSlot, Appointment
from applications.models import RegistrationWindow


def get_available_time_slots(slot):
    """
    Returns a list of time strings (e.g. '08:00', '08:30')
    that are NOT yet taken for a given AppointmentSlot.
    """
    all_times = [
        '08:00', '08:30', '09:00', '09:30',
        '10:00', '10:30', '11:00', '11:30',
        '13:00', '13:30', '14:00', '14:30',
        '15:00', '15:30', '16:00', '16:30',
    ]
    # Get times already booked for this slot
    booked_times = slot.appointments.values_list('time', flat=True)
    # Return only times that are not booked
    return [t for t in all_times if t not in booked_times]


def assign_appointment(application):
    """
    Automatically finds the next available appointment slot
    and assigns it to the application.

    Rules:
    - Must be after the registration window ends
    - Must be a weekday (Monday-Friday)
    - Must be an activated AppointmentSlot
    - Must have available capacity
    - First-come, first-served
    """

    # Find the active registration window to know when it ends
    try:
        window = RegistrationWindow.objects.filter(is_active=True).latest('created_at')
        # Appointments start the day after the window ends
        start_from = window.end_date + timedelta(days=1)
    except RegistrationWindow.DoesNotExist:
        # If no window exists, start from tomorrow
        start_from = datetime.today().date() + timedelta(days=1)

    # Find all active slots that are after the registration window
    # and still have capacity, ordered by date (earliest first)
    available_slots = AppointmentSlot.objects.filter(
        is_active=True,
        date__gte=start_from,
    ).order_by('date')

    for slot in available_slots:
        # Skip if this slot is already full
        if slot.is_full():
            continue

        # Get available times for this slot
        available_times = get_available_time_slots(slot)
        if not available_times:
            continue

        # Take the first available time
        assigned_time = available_times[0]

        # Create the appointment
        appointment = Appointment.objects.create(
            application=application,
            slot=slot,
            time=assigned_time,
        )

        # Update the application status
        application.status = 'scheduled'
        application.save()

        return appointment  # Success

    # If we reach here, no slots were available
    return None