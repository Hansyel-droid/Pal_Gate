import csv
import io
from datetime import timedelta

from django.shortcuts import render, get_object_or_404
from django.http import HttpResponse
from django.utils import timezone
from django.db.models import Q

from accounts.mixins import role_required
from .models import GateLog
from applications.models import StickerApplication

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet


@role_required('security')
def gate_live(request):
    today = timezone.now().date()
    now = timezone.now()
    last_24h = now - timedelta(hours=24)

    # Today's counts
    today_logs = GateLog.objects.filter(timestamp__date=today)
    total_today = today_logs.count()
    entries_today = today_logs.filter(action='entry').count()
    exits_today = today_logs.filter(action='exit').count()

    # Active passes = vehicles that entered but haven't exited
    # We get all plates that have logs today and check their last action
    active_passes = 0
    plates_seen = today_logs.values_list(
        'application__plate_number', flat=True
    ).distinct()
    for plate in plates_seen:
        if plate:
            last_log = GateLog.objects.filter(
                application__plate_number=plate
            ).order_by('-timestamp').first()
            if last_log and last_log.action == 'entry':
                active_passes += 1

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

    # Get unique gate locations for the filter dropdown
    gate_locations = GateLog.objects.values_list(
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
        'total_count': logs.count(),
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
        Paragraph(f"<b>Gate:</b> {log.gate_location}", styles['Normal'])
    )
    story.append(
        Paragraph(f"<b>RFID UID:</b> {log.rfid_uid}", styles['Normal'])
    )
    if log.denial_reason:
        story.append(
            Paragraph(f"<b>Denial Reason:</b> {log.denial_reason}", styles['Normal'])
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
        writer.writerow([
            log.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            log.application.plate_number if log.application else '—',
            log.application.sticker_id if log.application else '—',
            log.application.full_name if log.application else '—',
            log.get_action_display(),
            log.gate_location,
            log.denial_reason,
        ])

    return response


@role_required('security')
def time_tracker(request):
    now = timezone.now()

    # Rule 1: Inside for more than 12 hours = duration overstay
    OVERSTAY_HOURS = 12

    # Rule 2: Past 10 PM and still inside = curfew overstay
    # Build today's 10 PM in the same timezone
    curfew_time = now.replace(hour=22, minute=0, second=0, microsecond=0)
    is_past_curfew = now >= curfew_time

    inside_vehicles = []

    plates = GateLog.objects.filter(
        action='entry',
        application__isnull=False
    ).values_list(
        'application__plate_number', flat=True
    ).distinct()

    for plate in plates:
        last_log = GateLog.objects.filter(
            application__plate_number=plate
        ).order_by('-timestamp').first()

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