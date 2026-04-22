from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.utils import timezone
from datetime import timedelta, datetime
from django.db.models import Q, Count
from django.core.paginator import Paginator
from django.http import HttpResponse
import csv

from .models import GateLog
from sticker_portal.models import StickerApplication
from .forms import OfficerProfileForm


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

    context = {
        'total_today': total_today,
        'entries_today': entries_today,
        'exits_today': exits_today,
        'active_passes': active_passes,
        'live_logs': live_logs,
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

    paginator = Paginator(logs_qs, 50)
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