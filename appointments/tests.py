import tempfile
from datetime import timedelta

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import User
from applications.models import RegistrationWindow, StickerApplication
from .models import AppointmentSlot
from .services import book_appointment, get_bookable_dates, get_time_options


def make_doc(name='doc.pdf'):
    return SimpleUploadedFile(name, b'%PDF-1.4 test content', content_type='application/pdf')


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class AppointmentCapacityTests(TestCase):
    """
    appointments.services.book_appointment: capacity is PER TIME SLOT (e.g.
    20 seats at 8:00 AM, another 20 at 8:30 AM), not per day — covers that
    a time fills independently of other times on the same day, and that
    booking a full/invalid time is rejected rather than silently
    overbooking it.
    """

    def setUp(self):
        self.applicant = User.objects.create_user(
            username='booker', password='pw-1234567', role='applicant'
        )
        self.slot = AppointmentSlot.objects.create(
            date=timezone.localdate() + timedelta(days=10),
            is_active=True,
            capacity=2,  # small on purpose, to exercise the "full" path quickly
        )

    def _make_application(self, plate):
        return StickerApplication.objects.create(
            applicant=self.applicant,
            full_name='Booker Person',
            college_department='CCIS',
            id_number='2020-0099',
            classification='student',
            plate_number=plate,
            vehicle_type='four_wheels',
            vehicle_color='blue',
            is_owner=True,
            or_cr=make_doc(),
            drivers_license=make_doc(),
            status='draft',
        )

    def test_booking_up_to_capacity_then_rejecting_the_next(self):
        app1 = self._make_application('CAP-001')
        app2 = self._make_application('CAP-002')
        app3 = self._make_application('CAP-003')

        self.assertIsNotNone(book_appointment(app1, self.slot.pk, '08:00'))
        self.assertIsNotNone(book_appointment(app2, self.slot.pk, '08:00'))
        # Capacity is 2 — a third booking for the same (day, time) must fail.
        self.assertIsNone(book_appointment(app3, self.slot.pk, '08:00'))

        app3.refresh_from_db()
        self.assertEqual(app3.status, 'draft')  # untouched by the failed booking

    def test_different_times_have_independent_capacity(self):
        app1 = self._make_application('CAP-004')
        app2 = self._make_application('CAP-005')
        app3 = self._make_application('CAP-006')

        self.assertIsNotNone(book_appointment(app1, self.slot.pk, '08:00'))
        self.assertIsNotNone(book_appointment(app2, self.slot.pk, '08:00'))
        # 8:30 has its own capacity, unaffected by 8:00 being full.
        self.assertIsNotNone(book_appointment(app3, self.slot.pk, '08:30'))

    def test_booking_sets_application_status_and_time_options_reflect_it(self):
        app1 = self._make_application('CAP-007')
        appointment = book_appointment(app1, self.slot.pk, '09:00')
        app1.refresh_from_db()
        self.assertEqual(app1.status, 'scheduled')
        self.assertEqual(appointment.time, '09:00')

        options = {o['value']: o for o in get_time_options(self.slot)}
        self.assertEqual(options['09:00']['remaining'], 1)
        self.assertFalse(options['09:00']['is_full'])
        self.assertEqual(options['08:00']['remaining'], 2)  # untouched

    def test_booking_inactive_slot_fails(self):
        self.slot.is_active = False
        self.slot.save()
        app1 = self._make_application('CAP-008')
        self.assertIsNone(book_appointment(app1, self.slot.pk, '08:00'))

    def test_booking_invalid_time_fails(self):
        app1 = self._make_application('CAP-009')
        self.assertIsNone(book_appointment(app1, self.slot.pk, '23:59'))


class SqliteConcurrencySettingsTests(TestCase):
    """
    Guards the two SQLite options that make simultaneous submissions
    survivable. Without transaction_mode=IMMEDIATE, two connections both
    holding a read lock and both trying to upgrade to a write lock means
    one fails instantly — and busy_timeout is deliberately NOT honoured for
    that case, so raising it doesn't help. Measured before the fix: 60
    concurrent bookings produced 55 "database is locked" errors.

    These are easy to drop during a settings refactor and the damage only
    shows up under real load, which no other test here reproduces.
    """

    def test_transaction_mode_is_immediate(self):
        options = settings.DATABASES['default'].get('OPTIONS', {})
        self.assertEqual(options.get('transaction_mode'), 'IMMEDIATE')

    def test_busy_timeout_is_configured(self):
        options = settings.DATABASES['default'].get('OPTIONS', {})
        self.assertIn('busy_timeout', options.get('init_command', ''))


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class BookableDatesQueryCountTests(TestCase):
    """
    The applicant's date picker renders one card per open date, each asking
    the slot whether it's full and how many seats remain. Those must come
    from a single annotated query — otherwise every extra date the admin
    activates adds another COUNT to the page, and this is the page a whole
    cohort loads at once when registration opens.
    """

    def setUp(self):
        today = timezone.localdate()
        RegistrationWindow.objects.create(
            start_date=today, end_date=today, is_active=True
        )
        for i in range(1, 16):
            AppointmentSlot.objects.create(
                date=today + timedelta(days=i), is_active=True, capacity=20
            )

    def test_query_count_does_not_grow_with_the_number_of_dates(self):
        def count_queries():
            with CaptureQueriesContext(connection) as ctx:
                for slot in get_bookable_dates():
                    # Exactly what the template touches per row.
                    slot.is_full()
                    slot.slots_remaining()
            return len(ctx)

        with_15 = count_queries()

        today = timezone.localdate()
        for i in range(16, 46):
            AppointmentSlot.objects.create(
                date=today + timedelta(days=i), is_active=True, capacity=20
            )
        with_45 = count_queries()

        self.assertEqual(
            with_15, with_45,
            f'Query count grew with the number of dates '
            f'({with_15} -> {with_45}); the booked_count annotation was lost.'
        )
        self.assertLessEqual(with_45, 2)
