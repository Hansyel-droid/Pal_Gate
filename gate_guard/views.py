from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.utils import timezone
from datetime import timedelta, datetime, date
from django.db.models import Q, Count
from django.core.paginator import Paginator
from django.http import HttpResponse
import csv

from accounts.models import User
from .models import GateLog, RFIDTag, PendingRFIDRegistration, SystemConfig
from sticker_portal.models import StickerApplication, Vehicle
from .forms import OfficerProfileForm, RFIDRegistrationForm


# ------------------------------------------------------------------
# Helper functions for role-based access
# ------------------------------------------------------------------
def is_security_officer(user):
    return user.is_authenticated and user.user_type == 'security_officer'


# ------------------------------------------------------------------
# Gate Guard Views
# ------------------------------------------------------------------

@login_required
@user_passes_test(is_security_officer, login_url='/accounts/login/')
def overview(request):
    today = timezone.now().date()
    start_of_day = timezone.make_aware(
        timezone.datetime.combine(today, timezone.datetime.min.time())
    )
    end_of_day = start_of_day + timedelta(days=1)

    total_today = GateLog.objects.filter(
        timestamp__gte=start_of_day, timestamp__lt=end_of_day
    ).count()
    entries_today = GateLog.objects.filter(
        timestamp__gte=start_of_day, timestamp__lt=end_of_day, action='entry'
    ).count()
    exits_today = GateLog.objects.filter(
        timestamp__gte=start_of_day, timestamp__lt=end_of_day, action='exit'
    ).count()

    active_passes = StickerApplication.objects.filter(
        status='approved', expiry_date__gte=today
    ).count()

    live_logs = GateLog.objects.select_related(
        'rfid_tag__sticker_application__vehicle'
    ).order_by('-timestamp')[:20]

    traffic_data = get_hourly_traffic_data()
    
    context = {
        'total_today': total_today,
        'entries_today': entries_today,
        'exits_today': exits_today,
        'active_passes': active_passes,
        'live_logs': live_logs,
        'traffic_data': traffic_data,
    }
    return render(request, 'gate_guard/overview.html', context)


@login_required
@user_passes_test(is_security_officer, login_url='/accounts/login/')
def logs(request):
    search_query = request.GET.get('search', '')
    gate_filter = request.GET.get('gate', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')

    logs_qs = GateLog.objects.select_related(
        'rfid_tag__sticker_application__vehicle'
    ).order_by('-timestamp')

    if search_query:
        logs_qs = logs_qs.filter(
            Q(plate_number__icontains=search_query) |
            Q(driver_name__icontains=search_query)
        )

    if gate_filter:
        logs_qs = logs_qs.filter(gate=gate_filter)

    if date_from:
        try:
            date_from_obj = datetime.strptime(date_from, '%Y-%m-%d').date()
            logs_qs = logs_qs.filter(timestamp__date__gte=date_from_obj)
        except ValueError:
            pass

    if date_to:
        try:
            date_to_obj = datetime.strptime(date_to, '%Y-%m-%d').date()
            logs_qs = logs_qs.filter(timestamp__date__lte=date_to_obj)
        except ValueError:
            pass

    paginator = Paginator(logs_qs, 15)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj': page_obj,
        'search_query': search_query,
        'gate_filter': gate_filter,
        'date_from': date_from,
        'date_to': date_to,
        'gate_choices': GateLog.GATE_CHOICES,
    }
    return render(request, 'gate_guard/logs.html', context)


@login_required
@user_passes_test(is_security_officer, login_url='/accounts/login/')
def incident_report(request, log_id):
    log = get_object_or_404(
        GateLog.objects.select_related(
            'rfid_tag__sticker_application__applicant',
            'rfid_tag__sticker_application__vehicle'
        ),
        id=log_id
    )

    vehicle_info = {
        'plate_number': log.plate_number or 'N/A',
        'model': log.vehicle_model or 'N/A',
        'color': log.vehicle_color or 'N/A',
    }
    driver_info = {
        'name': log.driver_name or 'Unknown',
        'affiliation': '',
        'contact': '',
    }

    if log.rfid_tag and log.rfid_tag.sticker_application:
        app = log.rfid_tag.sticker_application
        applicant = app.applicant
        vehicle = app.vehicle
        vehicle_info.update({
            'plate_number': vehicle.plate_number,
            'model': vehicle.model,
            'color': vehicle.color,
            'sticker_id': f'PSU-{app.id:04d}',
        })
        driver_info.update({
            'name': applicant.get_full_name(),
            'affiliation': applicant.get_classification_display() or 'N/A',
            'contact': applicant.contact_number or 'N/A',
        })

    context = {
        'log': log,
        'vehicle_info': vehicle_info,
        'driver_info': driver_info,
        'primary_image': 'https://images.unsplash.com/photo-1533473359331-0135ef1b58bf?auto=format&fit=crop&q=80&w=600',
        'side_image': 'https://images.unsplash.com/photo-1544620347-c4fd4a3d5957?auto=format&fit=crop&q=80&w=200',
        'plate_zoom': 'https://images.unsplash.com/photo-1503376780353-7e6692767b70?auto=format&fit=crop&q=80&w=200',
    }
    return render(request, 'gate_guard/incident_report.html', context)


@login_required
@user_passes_test(is_security_officer, login_url='/accounts/login/')
def campus_map(request):
    gate_counts = GateLog.objects.values('gate').annotate(count=Count('id'))

    region_counts = {
        'North': 0,
        'South': 0,
        'East': 0,
        'West': 0,
    }
    for item in gate_counts:
        gate = item['gate']
        count = item['count']
        if gate == 'main_gate':
            region_counts['North'] = count
            region_counts['South'] = count
        elif gate == 'back_gate':
            region_counts['East'] = count
            region_counts['West'] = count

    context = {
        'region_counts': region_counts,
    }
    return render(request, 'gate_guard/campus_map.html', context)


@login_required
@user_passes_test(is_security_officer, login_url='/accounts/login/')
def settings(request):
    if request.method == 'POST':
        if 'update_profile' in request.POST:
            profile_form = OfficerProfileForm(request.POST, instance=request.user)
            if profile_form.is_valid():
                profile_form.save()
                messages.success(request, 'Profile updated successfully.')
                return redirect('gate_guard:settings')
        elif 'change_password' in request.POST:
            password_form = PasswordChangeForm(user=request.user, data=request.POST)
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)
                messages.success(request, 'Password changed successfully.')
                return redirect('gate_guard:settings')
            else:
                messages.error(request, 'Please correct the password errors.')
    else:
        profile_form = OfficerProfileForm(instance=request.user)
        password_form = PasswordChangeForm(user=request.user)

    context = {
        'profile_form': profile_form,
        'password_form': password_form,
    }
    return render(request, 'gate_guard/settings.html', context)


@login_required
@user_passes_test(is_security_officer, login_url='/accounts/login/')
def export_logs_csv(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="gate_logs.csv"'
    writer = csv.writer(response)
    writer.writerow([
        'Timestamp', 'Plate Number', 'Action', 'Gate',
        'Driver Name', 'Vehicle Model', 'Color'
    ])
    logs = GateLog.objects.all().values_list(
        'timestamp', 'plate_number', 'action', 'gate',
        'driver_name', 'vehicle_model', 'vehicle_color'
    )
    for log in logs:
        writer.writerow(log)
    return response


from django.db.models.functions import TruncHour
from django.db.models import Count, Q

def get_hourly_traffic_data():
    """Return hourly entry and exit counts for the last 24 hours."""
    end_time = timezone.now()
    start_time = end_time - timedelta(hours=24)

    entries = GateLog.objects.filter(
        timestamp__gte=start_time,
        timestamp__lt=end_time,
        action='entry'
    ).annotate(hour=TruncHour('timestamp')).values('hour').annotate(count=Count('id')).order_by('hour')

    exits = GateLog.objects.filter(
        timestamp__gte=start_time,
        timestamp__lt=end_time,
        action='exit'
    ).annotate(hour=TruncHour('timestamp')).values('hour').annotate(count=Count('id')).order_by('hour')

    # Fill missing hours with zeros
    hours = [(start_time + timedelta(hours=i)).strftime('%H:%M') for i in range(24)]
    entry_counts = [0] * 24
    exit_counts = [0] * 24

    for e in entries:
        idx = (e['hour'].hour - start_time.hour) % 24
        entry_counts[idx] = e['count']
    for e in exits:
        idx = (e['hour'].hour - start_time.hour) % 24
        exit_counts[idx] = e['count']

    return {
        'labels': hours,
        'entries': entry_counts,
        'exits': exit_counts,
    }

from django.http import FileResponse
from .pdf_utils import generate_incident_report_pdf
import io

@login_required
@user_passes_test(is_security_officer, login_url='/accounts/login/')
def download_incident_pdf(request, log_id):
    log = get_object_or_404(GateLog, id=log_id)
    buffer = generate_incident_report_pdf(log)
    return FileResponse(buffer, as_attachment=True, filename=f'incident_report_{log.id}.pdf')


@login_required
@user_passes_test(lambda u: u.user_type in ['security_officer', 'sticker_admin'], login_url='/accounts/login/')
def register_rfid(request):
    # Get the latest pending UID (if any)
    last_pending = PendingRFIDRegistration.objects.order_by('-created_at').first()
    pending_uid = last_pending.rfid_uid if last_pending else ''

    if request.method == 'POST':
        form = RFIDRegistrationForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data

            # 1. Create or get the driver (User)
            username = data['email'].split('@')[0]  # simple username from email
            user, created = User.objects.get_or_create(
                email=data['email'],
                defaults={
                    'username': username,
                    'first_name': data['driver_name'].split(' ')[0],
                    'last_name': ' '.join(data['driver_name'].split(' ')[1:]) or 'NoSurname',
                    'user_type': 'applicant',
                    'classification': data['classification'],
                    'college_department': data['college_department'],
                    'contact_number': data['contact_number'],
                }
            )
            if not created:
                # Update existing user info
                user.first_name = data['driver_name'].split(' ')[0]
                user.last_name = ' '.join(data['driver_name'].split(' ')[1:]) or user.last_name
                user.classification = data['classification']
                user.college_department = data['college_department']
                user.contact_number = data['contact_number']
                user.save()

            # 2. Create or get Vehicle
            vehicle, _ = Vehicle.objects.get_or_create(
                plate_number=data['plate_number'],
                defaults={
                    'model': data['vehicle_model'],
                    'color': data['vehicle_color'],
                    'owner': user,
                    'is_owner': data['is_owner'],
                }
            )

            # 3. Create an approved StickerApplication
            application = StickerApplication.objects.create(
                applicant=user,
                vehicle=vehicle,
                status='approved',
                expiry_date=data['expiry_date'],
                approved_at=timezone.now(),
                approved_by=request.user,
                submitted_at=timezone.now()
            )

            # 4. Check for existing RFID tag
            if RFIDTag.objects.filter(tag_id=data['rfid_uid']).exists():
                messages.error(request, f'RFID UID {data["rfid_uid"]} already exists!')
            else:
                RFIDTag.objects.create(
                    tag_id=data['rfid_uid'],
                    sticker_application=application,
                    is_active=True
                )
                messages.success(request, f'RFID {data["rfid_uid"]} registered successfully for {data["driver_name"]} ({data["plate_number"]})')
                # After successful registration, delete all pending UIDs
                PendingRFIDRegistration.objects.all().delete()
                return redirect('gate_guard:register_rfid')   # stay on page or redirect to log

    else:
        initial = {}
        if pending_uid:
            initial['rfid_uid'] = pending_uid
        form = RFIDRegistrationForm(initial=initial)

    return render(request, 'gate_guard/register_rfid.html', {
        'form': form,
        'pending_uid': pending_uid,
    })


@login_required
@user_passes_test(is_security_officer, login_url='/accounts/login/')
def toggle_admin_mode(request):
    config = SystemConfig.load()
    if request.method == 'POST':
        config.admin_mode = not config.admin_mode
        config.save()
        status = 'ON' if config.admin_mode else 'OFF'
        messages.success(request, f'Admin Mode turned {status}.')
        return redirect('gate_guard:overview')   # or wherever you want
    return redirect('gate_guard:overview')