import csv
import io
from datetime import timedelta
from xml.sax.saxutils import escape

from django.shortcuts import render, get_object_or_404
from django.http import HttpResponse, StreamingHttpResponse
from django.utils import timezone
from django.db.models import Count, Max, OuterRef, Q, Subquery
from django.db.models.functions import TruncHour

from accounts.mixins import role_required
from .models import GateLog
from .masking import mask_plate, mask_name
from applications.models import StickerApplication

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet


class Echo:
    """
    A file-like object that just hands back whatever is written to it, so
    csv.writer can be used as a row-at-a-time formatter for a streaming
    response instead of writing into a buffer.
    """

    def write(self, value):
        return value


def csv_safe(value):
    """
    Neutralize CSV formula injection: Excel/Sheets treat a cell starting with
    =, +, -, or @ as a formula. Prefix those with a leading apostrophe so
    they're always read back as plain text.
    """
    text = '' if value is None else str(value)
    if text[:1] in ('=', '+', '-', '@'):
        return "'" + text
    return text


# The two posts in real use (PRODUCT.md). Shown even with zero scans yet,
# so a reader that has never spoken still gets an honest status line instead
# of silently vanishing from the scope control.
KNOWN_GATES = ['Main Gate', 'Back Gate']

# Below this, "last scan Ns ago" is the honest whole story. Past it, the
# reader may simply be quiet (no vehicles), so the copy adds a soft hedge
# instead of ever claiming "unreachable" — there is no heartbeat signal in
# this system, only scan history, and asserting a connectivity fact we can't
# see would violate PRODUCT.md's "never claim more than exists".
STALE_READER_THRESHOLD = timedelta(minutes=10)


def _format_ago(seconds):
    """
    Django's `timesince` filter never resolves below a minute, which reads
    as "0 minutes ago" for a scan that happened 4 seconds ago — exactly
    wrong for a screen whose job is showing what just happened.
    """
    if seconds < 5:
        return 'just now'
    if seconds < 60:
        return f'{seconds}s ago'
    minutes = seconds // 60
    if minutes < 60:
        return f'{minutes}m ago'
    hours = minutes // 60
    if hours < 24:
        return f'{hours}h ago'
    days = hours // 24
    return f'{days}d ago'


# How far back "who is currently inside" looks. Anything still showing as
# inside after a week is a missed exit scan, not a vehicle — but a week is
# generous enough that no genuine overstay (12h rule, 10 PM curfew) can fall
# out of the window, while still bounding the query to recent rows instead of
# the whole history of the gate.
INSIDE_LOOKBACK = timedelta(days=7)


def _latest_scan_per_plate(since):
    """
    The single most recent GateLog for every plate scanned since `since`.

    A correlated subquery picking each plate's newest row, rather than
    streaming every matching log into Python and keeping the first one per
    plate. Both callers used to do the latter with no lower bound at all, so
    answering "how many vehicles are on campus right now" read every scan the
    system had ever recorded — and the live dashboard did it on each of its
    5-second polls.
    """
    newest_for_plate = (
        GateLog.objects
        .filter(
            application__plate_number=OuterRef('application__plate_number'),
            timestamp__gte=since,
        )
        .order_by('-timestamp', '-id')
        .values('id')[:1]
    )
    return (
        GateLog.objects
        .filter(application__isnull=False, timestamp__gte=since)
        .filter(id=Subquery(newest_for_plate))
        .select_related('application')
    )


def _reader_statuses():
    """
    One entry per known gate, built only from real GateLog timestamps —
    the only signal this system actually has about reader activity.

    A single grouped Max() rather than the distinct-gates query plus one
    "latest log" query per gate it used to run. This is on the 5s poll path,
    so a per-gate query here is paid twelve times a minute per open
    dashboard.
    """
    now = timezone.localtime()
    last_seen = {
        row['gate_location']: row['last_seen']
        for row in GateLog.objects.order_by()
        .values('gate_location')
        .annotate(last_seen=Max('timestamp'))
    }

    known = list(KNOWN_GATES)
    for gate_name in last_seen:
        if gate_name and gate_name not in known:
            known.append(gate_name)

    statuses = []
    for gate_name in known:
        seen_at = last_seen.get(gate_name)
        if not seen_at:
            statuses.append({
                'gate': gate_name, 'last_seen': None,
                'ago': None, 'state': 'silent',
            })
            continue
        age = now - seen_at
        state = 'stale' if age > STALE_READER_THRESHOLD else 'active'
        statuses.append({
            'gate': gate_name, 'last_seen': seen_at,
            'ago': _format_ago(int(age.total_seconds())), 'state': state,
        })
    return statuses


def _gate_live_context(request):
    today = timezone.localdate()
    now = timezone.localtime()

    gate_filter = request.GET.get('gate', '').strip()
    if gate_filter not in KNOWN_GATES:
        gate_filter = ''  # '' means "All Gates"

    today_logs = GateLog.objects.filter(timestamp__date=today)
    if gate_filter:
        today_logs = today_logs.filter(gate_location=gate_filter)
    total_today = today_logs.count()
    entries_today = today_logs.filter(action='entry').count()
    exits_today = today_logs.filter(action='exit').count()

    # Active passes are a campus-wide fact regardless of the gate scope
    # control — a vehicle may enter one gate and exit the other, so this
    # number is never filtered by gate_filter (PRODUCT.md).
    #
    # Scoped to today, which is what this always meant: the old version only
    # considered plates scanned today, and for such a plate the newest log
    # today IS the newest log overall — so reading the whole history to find
    # it never changed the answer, it just cost the whole history. This runs
    # on every 5s poll, so it's the one query here that most needs a bound.
    # `now` is already local and aware, so midnight-today comes off it
    # directly rather than via a combine/make_aware dance.
    start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    active_passes = _latest_scan_per_plate(start_of_today).filter(
        action='entry'
    ).count()

    # The single most recent scan is the dominant, distance-first focus of
    # this screen — not filtered to "today" so the screen never falsely
    # implies a scan is missing right after midnight.
    latest_scan_qs = GateLog.objects.select_related('application')
    if gate_filter:
        latest_scan_qs = latest_scan_qs.filter(gate_location=gate_filter)
    latest_scan = latest_scan_qs.order_by('-timestamp').first()
    latest_scan_ago = None
    if latest_scan:
        latest_scan_ago = _format_ago(int((now - latest_scan.timestamp).total_seconds()))

    # Latest 20 logs — genuinely latest, all-time. The empty-state copy
    # must not claim "today" since this list isn't scoped to today.
    latest_logs_qs = GateLog.objects.select_related('application')
    if gate_filter:
        latest_logs_qs = latest_logs_qs.filter(gate_location=gate_filter)
    latest_logs = latest_logs_qs.order_by('-timestamp')[:20]

    reader_statuses = _reader_statuses()
    if gate_filter:
        reader_statuses = [r for r in reader_statuses if r['gate'] == gate_filter]

    return {
        'gate_filter': gate_filter,
        'known_gates': KNOWN_GATES,
        'reader_statuses': reader_statuses,
        'latest_scan': latest_scan,
        'latest_scan_ago': latest_scan_ago,
        'total_today': total_today,
        'entries_today': entries_today,
        'exits_today': exits_today,
        'active_passes': active_passes,
        'latest_logs': latest_logs,
        'server_time': now,
    }


@role_required('security')
def gate_live(request):
    context = _gate_live_context(request)

    if request.GET.get('partial'):
        # Polled by the page's own JS every 5s to refresh the live region
        # without location.reload() — which used to blow away scroll,
        # focus, and screen-reader position twelve times a minute.
        return render(request, 'gate/_live_fragment.html', context)

    # Hourly traffic chart: last 24 hours, campus-wide. This is a seated,
    # after-the-fact summary rather than the distance-first live scene, so
    # unlike the rest of this view it is not part of the 5s poll and is not
    # scoped by gate_filter — it renders once per full page load.
    context['hourly_data'] = _hourly_traffic()

    return render(request, 'gate/live.html', context)


def _hourly_traffic():
    """
    Entry/exit counts for each of the last 24 hours.

    One grouped aggregate rather than the 48 COUNTs this used to run — two
    per hour, every full page load. Buckets are whole clock hours (the
    labels always said '%H:00' regardless, so the old rolling windows were
    mislabelled by up to 59 minutes anyway).
    """
    now = timezone.localtime()
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    earliest = current_hour - timedelta(hours=23)

    counts = (
        GateLog.objects
        .filter(timestamp__gte=earliest, action__in=('entry', 'exit'))
        .annotate(hour=TruncHour('timestamp'))
        .values('hour', 'action')
        .annotate(total=Count('id'))
    )

    by_hour = {}
    for row in counts:
        bucket = by_hour.setdefault(
            timezone.localtime(row['hour']).replace(
                minute=0, second=0, microsecond=0
            ),
            {'entry': 0, 'exit': 0},
        )
        bucket[row['action']] += row['total']

    hourly_data = []
    for i in range(23, -1, -1):
        hour_start = current_hour - timedelta(hours=i)
        bucket = by_hour.get(hour_start, {'entry': 0, 'exit': 0})
        hourly_data.append({
            'label': hour_start.strftime('%H:00'),
            'entries': bucket['entry'],
            'exits': bucket['exit'],
        })
    return hourly_data


@role_required('security')
def gate_logs(request):
    logs = GateLog.objects.select_related('application').order_by('-timestamp')

    # Search
    query = request.GET.get('q', '')
    if query:
        logs = logs.filter(
            Q(application__plate_number__icontains=query) |
            Q(application__full_name__icontains=query) |
            Q(rfid_uid__icontains=query)
        )

    # Filter by action
    action_filter = request.GET.get('action', '')
    if action_filter:
        logs = logs.filter(action=action_filter)

    # Filter by gate location
    gate_filter = request.GET.get('gate', '')
    if gate_filter:
        logs = logs.filter(gate_location=gate_filter)

    # Filter by date range
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    if date_from:
        logs = logs.filter(timestamp__date__gte=date_from)
    if date_to:
        logs = logs.filter(timestamp__date__lte=date_to)

    # Get unique gate locations for the filter dropdown.
    # order_by() clears GateLog's default `-timestamp` ordering — without
    # it, Django silently adds timestamp to SELECT DISTINCT and this would
    # list the same gate location once per log instead of deduplicating.
    gate_locations = GateLog.objects.order_by().values_list(
        'gate_location', flat=True
    ).distinct()

    # Pagination — 20 per page
    from django.core.paginator import Paginator
    paginator = Paginator(logs, 20)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    return render(request, 'gate/logs.html', {
        'page_obj': page_obj,
        'query': query,
        'action_filter': action_filter,
        'gate_filter': gate_filter,
        'date_from': date_from,
        'date_to': date_to,
        'gate_locations': gate_locations,
        'total_count': paginator.count,
    })


@role_required('security')
def incident_report(request, pk):
    log = get_object_or_404(GateLog, pk=pk)

    # Try to find a sticker application even if not directly linked
    # (e.g. denied entry — match by plate if possible)
    application = log.application
    if not application and log.rfid_uid:
        application = StickerApplication.objects.filter(
            rfid_uid=log.rfid_uid
        ).first()

    return render(request, 'gate/incident_report.html', {
        'log': log,
        'application': application,
    })


@role_required('security')
def incident_pdf(request, pk):
    log = get_object_or_404(GateLog, pk=pk)
    application = log.application

    # Create PDF in memory
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []

    # Title
    story.append(Paragraph('PalSU Gate System', styles['Title']))
    story.append(Paragraph('Incident Report', styles['Heading1']))
    story.append(Spacer(1, 12))

    # Status
    action_colors = {
        'entry': 'Authorised Entry',
        'exit': 'Authorised Exit',
        'denied': 'Access Denied',
    }
    story.append(
        Paragraph(f"<b>Action:</b> {action_colors.get(log.action, log.action)}", styles['Normal'])
    )
    story.append(
        Paragraph(f"<b>Time:</b> {log.timestamp.strftime('%B %d, %Y at %I:%M %p')}", styles['Normal'])
    )
    story.append(
        Paragraph(f"<b>Gate:</b> {escape(log.gate_location)}", styles['Normal'])
    )
    story.append(
        Paragraph(f"<b>RFID UID:</b> {escape(log.rfid_uid)}", styles['Normal'])
    )
    if log.denial_reason:
        story.append(
            Paragraph(f"<b>Denial Reason:</b> {escape(log.denial_reason)}", styles['Normal'])
        )
    story.append(Spacer(1, 12))

    # Vehicle / Applicant info
    if application:
        story.append(Paragraph('Vehicle Information', styles['Heading2']))
        data = [
            ['Plate Number', application.plate_number],
            ['Sticker ID', application.sticker_id or '—'],
            ['Vehicle Type', application.get_vehicle_type_display()],
            ['Color', application.get_vehicle_color_display()],
        ]
        t = Table(data, colWidths=[150, 300])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), colors.lightgrey),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('PADDING', (0, 0), (-1, -1), 6),
        ]))
        story.append(t)
        story.append(Spacer(1, 12))

        story.append(Paragraph('Driver Information', styles['Heading2']))
        data2 = [
            ['Full Name', application.full_name],
            ['Classification', application.get_classification_display()],
            ['ID Number', application.id_number],
            ['College/Dept', application.college_department],
        ]
        t2 = Table(data2, colWidths=[150, 300])
        t2.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), colors.lightgrey),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('PADDING', (0, 0), (-1, -1), 6),
        ]))
        story.append(t2)

    # Build PDF
    doc.build(story)
    buffer.seek(0)

    response = HttpResponse(buffer, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="incident_report_{pk}.pdf"'
    return response


@role_required('security')
def export_csv(request):
    logs = GateLog.objects.select_related('application').order_by('-timestamp')

    # Apply same filters as the logs page
    query = request.GET.get('q', '')
    if query:
        logs = logs.filter(
            Q(application__plate_number__icontains=query) |
            Q(application__full_name__icontains=query)
        )
    action_filter = request.GET.get('action', '')
    if action_filter:
        logs = logs.filter(action=action_filter)
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    if date_from:
        logs = logs.filter(timestamp__date__gte=date_from)
    if date_to:
        logs = logs.filter(timestamp__date__lte=date_to)

    # Streamed rather than assembled in memory. With no filters applied this
    # is the whole table, and the previous version built every row of it into
    # a single HttpResponse body before sending a byte — one export of a
    # large history was enough to take the process down. iterator() keeps the
    # DB cursor from materialising the queryset alongside it.
    def rows():
        writer = csv.writer(Echo())
        yield writer.writerow([
            'Timestamp', 'Plate Number', 'Sticker ID',
            'Driver Name', 'Action', 'Gate', 'Denial Reason'
        ])
        for log in logs.iterator(chunk_size=2000):
            # Masked the same way the logs page is — this file gets
            # downloaded, emailed, and opened outside the system, so it
            # carries the same over-exposure risk as the browsing page.
            yield writer.writerow([csv_safe(v) for v in [
                log.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                mask_plate(log.application.plate_number) if log.application else '—',
                log.application.sticker_id if log.application else '—',
                mask_name(log.application.full_name) if log.application else '—',
                log.get_action_display(),
                log.gate_location,
                log.denial_reason,
            ]])

    response = StreamingHttpResponse(rows(), content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="gate_logs.csv"'
    return response


@role_required('security')
def time_tracker(request):
    now = timezone.localtime()

    # Rule 1: Inside for more than 12 hours = duration overstay
    OVERSTAY_HOURS = 12

    # Rule 2: Past 10 PM and still inside = curfew overstay
    # Build today's 10 PM in the same timezone
    curfew_time = now.replace(hour=22, minute=0, second=0, microsecond=0)
    is_past_curfew = now >= curfew_time

    inside_vehicles = []

    # Every plate whose most recent scan in the lookback window was an
    # entry — i.e. it went in and hasn't come back out. One query, bounded
    # to recent history. This used to pull the entire GateLog table into
    # Python to find the latest row per plate, so a year of scans was read
    # in full even to render an empty list.
    still_inside = _latest_scan_per_plate(now - INSIDE_LOOKBACK).filter(
        action='entry'
    ).order_by('timestamp')

    for last_log in still_inside:
        duration = now - last_log.timestamp
        total_seconds = int(duration.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60

        # Check both overstay conditions
        duration_overstay = duration.total_seconds() > (OVERSTAY_HOURS * 3600)
        curfew_overstay = is_past_curfew

        # Either condition triggers overstay
        is_overstay = duration_overstay or curfew_overstay

        # Build a reason string so the template knows WHY it's flagged
        overstay_reasons = []
        if duration_overstay:
            overstay_reasons.append(f'Inside for over {OVERSTAY_HOURS} hours')
        if curfew_overstay:
            overstay_reasons.append('Past 10:00 PM curfew')

        inside_vehicles.append({
            'log': last_log,
            'application': last_log.application,
            'entry_time': last_log.timestamp,
            'duration': f'{hours}h {minutes}m',
            'is_overstay': is_overstay,
            'overstay_reasons': overstay_reasons,
        })

    # Count total overstays for the alert banner
    overstay_count = sum(1 for v in inside_vehicles if v['is_overstay'])

    return render(request, 'gate/time_tracker.html', {
        'inside_vehicles': inside_vehicles,
        'now': now,
        'overstay_hours': OVERSTAY_HOURS,
        'is_past_curfew': is_past_curfew,
        'overstay_count': overstay_count,
        'curfew_time': curfew_time,
    })