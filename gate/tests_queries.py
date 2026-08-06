"""
Query-shape regression tests for the gate views.

These pin the property that actually regresses silently: cost that grows
with how much history the gate has recorded. The live dashboard re-runs its
context every 5 seconds, so a query that scales with the log table is paid
twelve times a minute per open screen.
"""
import tempfile
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.http import StreamingHttpResponse
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from applications.models import StickerApplication
from gate.models import GateLog

MEDIA = tempfile.mkdtemp()


def doc(name='doc.pdf'):
    return SimpleUploadedFile(name, b'%PDF-1.4 x', content_type='application/pdf')


def make_app(applicant, plate):
    return StickerApplication.objects.create(
        applicant=applicant, full_name=f'Driver {plate}',
        college_department='CCIS', id_number=plate, classification='student',
        plate_number=plate, vehicle_type='four_wheels', vehicle_color='blue',
        is_owner=True, or_cr=doc(), drivers_license=doc(), status='issued',
    )


class query_count(CaptureQueriesContext):
    """CaptureQueriesContext with the count read off it after the fact."""

    def __init__(self):
        super().__init__(connection)

    def __exit__(self, *exc):
        super().__exit__(*exc)
        self.count = len(self.captured_queries)


@override_settings(MEDIA_ROOT=MEDIA)
class GateQueryShapeTests(TestCase):
    def setUp(self):
        self.security = User.objects.create_user(
            username='sec', password='pw-1234567', role='security'
        )
        self.applicant = User.objects.create_user(
            username='ap2', password='pw-1234567', role='applicant'
        )
        self.client.force_login(self.security)
        self._seeded = 0

    def seed(self, plates, scans_per_plate):
        """Adds `plates` more vehicles, each with a run of recent scans."""
        now = timezone.now()
        for _ in range(plates):
            i = self._seeded
            self._seeded += 1
            app = make_app(self.applicant, f'P-{i:04d}')
            for j in range(scans_per_plate):
                log = GateLog.objects.create(
                    rfid_uid=f'uid{i}', application=app,
                    action='entry' if j % 2 == 0 else 'exit',
                    gate_location='Main Gate',
                )
                # timestamp is auto_now_add, so backdate it directly.
                GateLog.objects.filter(pk=log.pk).update(
                    timestamp=now - timedelta(hours=j * 3)
                )

    def assert_flat(self, url, params=None):
        self.seed(3, 4)
        with query_count() as small:
            self.client.get(url, params or {})
        self.seed(20, 6)
        with query_count() as big:
            response = self.client.get(url, params or {})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            small.count, big.count,
            f'{url} ran {small.count} queries over 12 logs but {big.count} '
            f'over 132 — cost is growing with the history',
        )

    def test_gate_live_is_flat(self):
        self.assert_flat(reverse('gate_live'))

    def test_gate_live_poll_is_flat(self):
        self.assert_flat(reverse('gate_live'), {'partial': '1'})

    def test_time_tracker_is_flat(self):
        self.assert_flat(reverse('time_tracker'))

    def test_time_tracker_ignores_history_outside_the_lookback(self):
        """
        A vehicle whose last scan was months ago is not on campus; it's a
        missed exit scan. The unbounded version listed it as currently
        inside forever.
        """
        now = timezone.now()
        inside = make_app(self.applicant, 'IN-001')
        left = make_app(self.applicant, 'OUT-01')
        stale = make_app(self.applicant, 'OLD-01')

        def log(app, action, ago):
            entry = GateLog.objects.create(
                rfid_uid='u', application=app, action=action
            )
            GateLog.objects.filter(pk=entry.pk).update(timestamp=now - ago)

        log(inside, 'entry', timedelta(hours=2))
        log(left, 'entry', timedelta(hours=5))
        log(left, 'exit', timedelta(hours=1))
        log(stale, 'entry', timedelta(days=30))

        response = self.client.get(reverse('time_tracker'))
        plates = [
            v['application'].plate_number
            for v in response.context['inside_vehicles']
        ]
        self.assertEqual(plates, ['IN-001'])

    def test_export_csv_streams(self):
        self.seed(5, 2)
        response = self.client.get(reverse('export_csv'))
        self.assertIsInstance(response, StreamingHttpResponse)
        lines = [
            line for line in
            b''.join(response.streaming_content).decode().splitlines()
            if line.strip()
        ]
        self.assertTrue(lines[0].startswith('Timestamp,'))
        self.assertEqual(len(lines), 11)  # header + 10 logs

    def test_hourly_chart_counts_only_the_last_24_hours(self):
        now = timezone.localtime()
        app = make_app(self.applicant, 'HR-001')

        def log(action, ago):
            entry = GateLog.objects.create(
                rfid_uid='u', application=app, action=action
            )
            GateLog.objects.filter(pk=entry.pk).update(timestamp=now - ago)

        log('entry', timedelta(minutes=5))
        log('entry', timedelta(minutes=10))
        log('exit', timedelta(minutes=15))
        log('entry', timedelta(days=3))  # outside the window

        data = self.client.get(reverse('gate_live')).context['hourly_data']
        self.assertEqual(len(data), 24)
        self.assertEqual(sum(d['entries'] for d in data), 2)
        self.assertEqual(sum(d['exits'] for d in data), 1)
        self.assertEqual(data[-1]['label'], now.strftime('%H:00'))
