from django.shortcuts import render
from django.utils import timezone
from datetime import timedelta
from .models import GateLog
from sticker_portal.models import StickerApplication
import csv
from django.http import HttpResponse
from django.core.paginator import Paginator
from django.db.models import Q

def overview(request):
    today = timezone.now().date()
    start_of_day = timezone.make_aware(timezone.datetime.combine(today, timezone.datetime.min.time()))
    end_of_day = start_of_day + timedelta(days=1)

    total_today = GateLog.objects.filter(timestamp__gte=start_of_day, timestamp__lt=end_of_day).count()
    entries_today = GateLog.objects.filter(
        timestamp__gte=start_of_day, timestamp__lt=end_of_day, action='entry'
    ).count()
    exits_today = GateLog.objects.filter(
        timestamp__gte=start_of_day, timestamp__lt=end_of_day, action='exit'
    ).count()

    active_passes = StickerApplication.objects.filter(status='approved', expiry_date__gte=today).count()
    live_logs = GateLog.objects.select_related('rfid_tag__sticker_application__vehicle').order_by('-timestamp')[:20]

    context = {
        'total_today': total_today,
        'entries_today': entries_today,
        'exits_today': exits_today,
        'active_passes': active_passes,
        'live_logs': live_logs,
    }
    return render(request, 'gate_guard/overview.html', context)


from django.core.paginator import Paginator
from django.db.models import Q
from django.utils import timezone
import datetime

def logs(request):
    # Get filter parameters from GET request
    search_query = request.GET.get('search', '')
    gate_filter = request.GET.get('gate', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')

    # Base queryset
    logs_qs = GateLog.objects.select_related('rfid_tag__sticker_application__vehicle').order_by('-timestamp')

    # Apply filters
    if search_query:
        logs_qs = logs_qs.filter(
            Q(plate_number__icontains=search_query) |
            Q(driver_name__icontains=search_query)
        )

    if gate_filter:
        logs_qs = logs_qs.filter(gate=gate_filter)

    if date_from:
        try:
            date_from_obj = datetime.datetime.strptime(date_from, '%Y-%m-%d').date()
            logs_qs = logs_qs.filter(timestamp__date__gte=date_from_obj)
        except ValueError:
            pass

    if date_to:
        try:
            date_to_obj = datetime.datetime.strptime(date_to, '%Y-%m-%d').date()
            logs_qs = logs_qs.filter(timestamp__date__lte=date_to_obj)
        except ValueError:
            pass

    # Pagination (50 items per page)
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

def incident_report(request, log_id):
    return HttpResponse(f"Incident report for log {log_id}")


def campus_map(request):
    # Count vehicles per gate/section (mapping gate to region)
    from django.db.models import Count
    gate_counts = GateLog.objects.values('gate').annotate(count=Count('id'))

    # Map gate values to region names (customize as needed)
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
            region_counts['South'] = count  # Example mapping
        elif gate == 'back_gate':
            region_counts['East'] = count
            region_counts['West'] = count

    # For demonstration, we'll use more realistic numbers later; this is just a start

    context = {
        'region_counts': region_counts,
    }
    return render(request, 'gate_guard/campus_map.html', context)


from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from .forms import OfficerProfileForm  # we'll create this next

@login_required
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


def export_logs_csv(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="gate_logs.csv"'
    writer = csv.writer(response)
    writer.writerow(['Timestamp', 'Plate Number', 'Action', 'Gate', 'Driver Name', 'Vehicle Model', 'Color'])
    logs = GateLog.objects.all().values_list('timestamp', 'plate_number', 'action', 'gate', 'driver_name', 'vehicle_model', 'vehicle_color')
    for log in logs:
        writer.writerow(log)
    return response