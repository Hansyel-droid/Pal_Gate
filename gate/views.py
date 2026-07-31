import csv
import io
from datetime import timedelta
from xml.sax.saxutils import escape

from django.shortcuts import render, get_object_or_404
from django.http import HttpResponse
from django.utils import timezone
from django.db.models import Q

from accounts.mixins import role_required
from .models import GateLog
from .masking import mask_plate, mask_name
from applications.models import StickerApplication

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet


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


@role_required('security')
def gate_live(request):
    today = timezone.localdate()
    now = timezone.localtime()
    last_24h = now - timedelta(hours=24)

    # Today's counts
    today_logs = GateLog.objects.filter(timestamp__date=today)
    total_today = today_logs.count()
    entries_today = today_logs.filter(action='entry').count()
    exits_today = today_logs.filter(action='exit').count()

    # Active passes = vehicles that entered but haven't exited.
    # One query for the plates seen today, one query for their latest logs
    # (across all history, since a vehicle may have entered on a prior day)
    # instead of a query per plate.
    # order_by() clears GateLog's default `-timestamp` ordering — without
    # it, Django silently adds timestamp to SELECT DISTINCT and this would
    # yield one row per log instead of deduplicating by plate.
    plates_seen = [
        p for p in today_logs.order_by().values_list(
            'application__plate_number', flat=True
        ).distinct()
        if p
    ]
    latest_by_plate = {}
    for log in GateLog.objects.filter(
        application__plate_number__in=plates_seen
    ).select_related('application').order_by('application__plate_number', '-timestamp'):
        plate = log.application.plate_number
        latest_by_plate.setdefault(plate, log)
    active_passes = sum(
        1 for log in latest_by_plate.values() if log.action == 'entry'
    )

    # Latest 20 logs
    latest_logs = GateLog.objects.select_related(
        'application'
    ).order_by('-timestamp')[:20]

    # Hourly data for the traffic chart (last 24 hours)
    # Build a list of 24 hours with entry/exit counts
    hourly_data = []
    for i in range(23, -1, -1):
        hour_start = now - timedelta(hours=i+1)
        hour_end = now - timedelta(hours=i)
        hour_label = hour_start.strftime('%H:00')
        entries = GateLog.objects.filter(
            action='entry',
            timestamp__gte=hour_start,
            timestamp__lt=hour_end
        ).count()
        exits = GateLog.objects.filter(
            action='exit',
            timestamp__gte=hour_start,
            timestamp__lt=hour_end
        ).count()
        hourly_data.append({
            'label': hour_label,
            'entries': entries,
            'exits': exits,
        })

    return render(request, 'gate/live.html', {
        'total_today': total_today,
        'entries_today': entries_today,
        'exits_today': exits_today,
        'active_passes': active_passes,
        'latest_logs': latest_logs,
        'hourly_data': hourly_data,
    })


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

    # Build CSV response
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="gate_logs.csv"'

    writer = csv.writer(response)
    writer.writerow([
        'Timestamp', 'Plate Number', 'Sticker ID',
        'Driver Name', 'Action', 'Gate', 'Denial Reason'
    ])

    for log in logs:
        # Masked the same way the logs page is — this file gets downloaded,
        # emailed, and opened outside the system, so it carries the same
        # over-exposure risk as the browsing page.
        writer.writerow([csv_safe(v) for v in [
            log.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            mask_plate(log.application.plate_number) if log.application else '—',
            log.application.sticker_id if log.application else '—',
            mask_name(log.application.full_name) if log.application else '—',
            log.get_action_display(),
            log.gate_location,
            log.denial_reason,
        ]])

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

    # order_by() clears GateLog's default `-timestamp` ordering — without
    # it, Django silently adds timestamp to SELECT DISTINCT so this would
    # return the same plate once per entry log instead of deduplicating.
    plates = list(GateLog.objects.filter(
        action='entry',
        application__isnull=False
    ).order_by().values_list(
        'application__plate_number', flat=True
    ).distinct())

    # One query for the latest log per plate instead of one query per plate.
    latest_by_plate = {}
    for log in GateLog.objects.filter(
        application__plate_number__in=plates
    ).select_related('application').order_by('application__plate_number', '-timestamp'):
        latest_by_plate.setdefault(log.application.plate_number, log)

    for plate in plates:
        last_log = latest_by_plate.get(plate)

        if last_log and last_log.action == 'entry':
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

    # Sort by entry time, oldest first
    inside_vehicles.sort(key=lambda x: x['entry_time'])

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