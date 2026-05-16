from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.utils import timezone
from datetime import timedelta, date, datetime, time as dt_time
from django.core.files.storage import default_storage
from django.views.decorators.http import require_POST
from django.db.models import Count, Q
import calendar
import json
from django.http import JsonResponse
from .models import StickerApplication, Vehicle, Document, AvailableDate, RegistrationPeriod
from .forms import (
    StickerAdminProfileForm,
    VehicleForm,
    StickerApplicationForm,
    DocumentUploadForm,
)
from gate_guard.forms import RFIDRegistrationForm
from gate_guard.models import PendingRFIDRegistration, RFIDTag
from accounts.models import User


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def is_sticker_admin(user):
    return user.is_authenticated and user.user_type == 'sticker_admin'


def is_applicant(user):
    return user.is_authenticated and user.user_type == 'applicant'


def is_registration_open():
    period = RegistrationPeriod.load()
    today = timezone.now().date()
    return period.start_date <= today <= period.end_date


def auto_assign_schedule(application):
    """Assign the next available weekday slot after the registration window ends."""
    MAX_PER_DAY = 20
    SLOT_DURATION = 30
    START_TIME = dt_time(8, 0)

    period = RegistrationPeriod.load()
    # Start looking the day after registration ends, or tomorrow – whichever is later
    start_from = max(date.today() + timedelta(days=1), period.end_date + timedelta(days=1))

    active_dates = AvailableDate.objects.filter(
        date__gte=start_from,
        is_active=True
    ).order_by('date')

    for avail in active_dates:
        day = avail.date
        # Skip weekends
        if day.weekday() >= 5:   # Saturday or Sunday
            continue

        assigned_count = StickerApplication.objects.filter(
            scheduled_datetime__date=day
        ).count()
        if assigned_count >= MAX_PER_DAY:
            continue

        slot_time = START_TIME
        assigned_times = set(
            StickerApplication.objects.filter(
                scheduled_datetime__date=day
            ).values_list('scheduled_datetime', flat=True)
        )

        while slot_time <= dt_time(17, 0):
            dt_candidate = timezone.make_aware(datetime.combine(day, slot_time))
            if dt_candidate not in assigned_times:
                application.scheduled_datetime = dt_candidate
                application.save()
                return True
            slot_time = (datetime.combine(day, slot_time) + timedelta(minutes=SLOT_DURATION)).time()
    return False


# ----------------------------------------------------------------------
# Sticker Admin Views
# ----------------------------------------------------------------------
@login_required
@user_passes_test(is_sticker_admin, login_url='/accounts/login/')
def dashboard(request):
    pending_count = StickerApplication.objects.filter(status='pending').count()
    approved_today = StickerApplication.objects.filter(
        status='approved',
        approved_at__date=timezone.now().date()
    ).count()
    waiting_claim = StickerApplication.objects.filter(status='approved').count()
    rejected_incomplete = StickerApplication.objects.filter(status='rejected').count()

    three_days_ago = timezone.now() - timedelta(days=3)
    high_priority = StickerApplication.objects.filter(
        status='pending',
        submitted_at__lte=three_days_ago
    ).count()

    processed_today = StickerApplication.objects.filter(
        approved_at__date=timezone.now().date()
    ).count()

    recent_activity = StickerApplication.objects.order_by('-updated_at')[:5]

    period = RegistrationPeriod.load()

    context = {
        'pending_count': pending_count,
        'approved_today': approved_today,
        'waiting_claim': waiting_claim,
        'rejected_incomplete': rejected_incomplete,
        'high_priority': high_priority,
        'processed_today': processed_today,
        'recent_activity': recent_activity,
        'registration_period': period,
    }
    return render(request, 'sticker_portal/dashboard.html', context)



@login_required
@user_passes_test(is_sticker_admin, login_url='/accounts/login/')
def set_registration_period(request):
    if request.method == 'POST':
        start = request.POST.get('start_date')
        end = request.POST.get('end_date')
        try:
            start_date = datetime.strptime(start, '%Y-%m-%d').date()
            end_date = datetime.strptime(end, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            messages.error(request, 'Invalid dates.')
            return redirect('sticker_portal:dashboard')

        period = RegistrationPeriod.load()
        period.start_date = start_date
        period.end_date = end_date
        period.save()
        messages.success(request, f'Registration period set: {start_date} to {end_date}.')
    return redirect('sticker_portal:dashboard')


@login_required
@user_passes_test(is_sticker_admin, login_url='/accounts/login/')
def application_detail(request, app_id):
    application = get_object_or_404(StickerApplication, id=app_id)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'approve':
            application.status = 'approved'
            application.approved_at = timezone.now()
            application.approved_by = request.user
            application.save()
            messages.success(request, f'Application #{app_id} approved.')
        elif action == 'reject':
            application.status = 'rejected'
            application.rejection_reason = request.POST.get('reason', '')
            application.save()
            messages.success(request, f'Application #{app_id} rejected.')
        elif action == 'set_schedule':
            schedule_date = request.POST.get('schedule_date')
            schedule_time = request.POST.get('schedule_time')
            if schedule_date and schedule_time:
                try:
                    dt = datetime.strptime(f"{schedule_date} {schedule_time}", "%Y-%m-%d %H:%M")
                    application.scheduled_datetime = timezone.make_aware(dt)
                    application.save()
                    messages.success(request, 'Schedule set successfully.')
                except ValueError:
                    messages.error(request, 'Invalid date/time.')
            else:
                messages.error(request, 'Please provide both date and time.')
        return redirect('sticker_portal:application_detail', app_id=application.id)

    documents = application.documents.all()
    return render(request, 'sticker_portal/application_detail.html', {
        'application': application,
        'documents': documents,
    })


@login_required
@user_passes_test(is_sticker_admin, login_url='/accounts/login/')
def settings(request):
    if request.method == 'POST':
        if 'update_profile' in request.POST:
            profile_form = StickerAdminProfileForm(request.POST, instance=request.user)
            if profile_form.is_valid():
                profile_form.save()
                messages.success(request, 'Profile updated successfully.')
                return redirect('sticker_portal:settings')
        elif 'change_password' in request.POST:
            password_form = PasswordChangeForm(user=request.user, data=request.POST)
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)
                messages.success(request, 'Password changed successfully.')
                return redirect('sticker_portal:settings')
            else:
                messages.error(request, 'Please correct the password errors.')
        elif 'text_size' in request.POST:
            new_size = request.POST['text_size']
            if new_size in ['small', 'medium', 'large']:
                request.user.text_size = new_size
                request.user.save()
                messages.success(request, f'Text size set to {new_size}.')
            return redirect('sticker_portal:settings')
    else:
        profile_form = StickerAdminProfileForm(instance=request.user)
        password_form = PasswordChangeForm(user=request.user)

    context = {
        'profile_form': profile_form,
        'password_form': password_form,
        'current_text_size': request.user.text_size,
    }
    return render(request, 'sticker_portal/settings.html', context)


# ----------------------------------------------------------------------
# Appointment Management
# ----------------------------------------------------------------------
@login_required
@user_passes_test(is_sticker_admin, login_url='/accounts/login/')
def appointment_management(request):
    selected_date = request.GET.get('date', date.today().isoformat())
    search_query = request.GET.get('search', '').strip()

    try:
        selected_date_obj = datetime.strptime(selected_date, '%Y-%m-%d').date()
    except ValueError:
        selected_date_obj = date.today()
        selected_date = selected_date_obj.isoformat()

    applications = StickerApplication.objects.select_related(
        'applicant', 'vehicle'
    ).prefetch_related('documents')

    if search_query:
        applications = applications.filter(
            Q(applicant__first_name__icontains=search_query) |
            Q(applicant__last_name__icontains=search_query) |
            Q(full_name__icontains=search_query) |
            Q(student_id__icontains=search_query) |
            Q(college_department__icontains=search_query) |
            Q(vehicle__plate_number__icontains=search_query) |
            Q(id__icontains=search_query)
        )
        show_time_column = False
    else:
        applications = applications.filter(
            scheduled_datetime__date=selected_date_obj
        )
        show_time_column = True

    applications = applications.order_by('-submitted_at')

    # Calendar setup
    today = date.today()
    month = selected_date_obj.month
    year = selected_date_obj.year

    cal = calendar.Calendar(firstweekday=6)
    month_days = cal.monthdayscalendar(year, month)

    active_dates = set(
        AvailableDate.objects.filter(
            date__year=year, date__month=month, is_active=True
        ).values_list('date', flat=True)
    )

    weeks = []
    for week in month_days:
        week_data = []
        for day in week:
            if day == 0:
                week_data.append(None)
            else:
                d = date(year, month, day)
                week_data.append({
                    'day': day,
                    'date': d.isoformat(),
                    'is_today': d == today,
                    'is_active': d.isoformat() in active_dates,
                    'is_past': d < today,
                })
        weeks.append(week_data)

    first_day = date(year, month, 1)
    last_day = date(year, month, calendar.monthrange(year, month)[1])

    # ---- Smart defaults for schedule range & auto-assign ----
    period = RegistrationPeriod.load()
    registration_end = period.end_date
    default_from = registration_end + timedelta(days=1)
    # End = last day of the month that default_from falls in
    next_month = default_from.replace(day=28) + timedelta(days=4)
    default_to = next_month - timedelta(days=next_month.day)
    if default_to < default_from:
        default_to = default_from + timedelta(days=30)

    schedule_start = default_from.isoformat()
    schedule_end = default_to.isoformat()

    context = {
        'selected_date': selected_date,
        'selected_date_obj': selected_date_obj,
        'applications': applications,
        'weeks': weeks,
        'month_name': calendar.month_name[month],
        'year': year,
        'month': month,
        'first_day_iso': first_day.isoformat(),
        'last_day_iso': last_day.isoformat(),
        'search_query': search_query,
        'show_time_column': show_time_column,
        'schedule_start': schedule_start,    # NEW
        'schedule_end': schedule_end,        # NEW
    }
    return render(request, 'sticker_portal/appointment_management.html', context)


@require_POST
@login_required
@user_passes_test(is_sticker_admin)
def toggle_available_date(request):
    date_str = request.POST.get('date')
    action = request.POST.get('action', 'toggle')

    if not date_str:
        return JsonResponse({'success': False, 'error': 'Missing date'}, status=400)

    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'success': False, 'error': 'Invalid date format'}, status=400)

    obj, created = AvailableDate.objects.get_or_create(date=target_date)

    if action == 'activate':
        obj.is_active = True
        obj.save()
    elif action == 'deactivate':
        obj.is_active = False
        obj.save()
    else:
        obj.is_active = not obj.is_active
        obj.save()

    return JsonResponse({'success': True, 'is_active': obj.is_active})


# ----------------------------------------------------------------------
# Applicant Views – three‑step wizard
# ----------------------------------------------------------------------

@login_required
@user_passes_test(is_applicant, login_url='/accounts/login/applicant/')
def apply_personal(request, app_id=None):
    """Step 1: Personal details."""
    if app_id:
        application = get_object_or_404(
            StickerApplication, id=app_id, applicant=request.user, status='draft'
        )
        initial_app = {
            'full_name': application.full_name,
            'college_department': application.college_department,
            'student_id': application.student_id,
            'classification': request.user.classification,
        }
        app_form = StickerApplicationForm(initial=initial_app, user=request.user)
    else:
        application = None
        app_form = StickerApplicationForm(user=request.user)

    if request.method == 'POST':
        if not is_registration_open():
            messages.error(request, 'Registration is currently closed.')
            return redirect('sticker_portal:my_applications')

        app_form = StickerApplicationForm(request.POST, user=request.user)
        if app_form.is_valid():
            if app_id:
                application = get_object_or_404(
                    StickerApplication, id=app_id, applicant=request.user, status='draft'
                )
            else:
                application = StickerApplication(applicant=request.user)

            application.full_name = app_form.cleaned_data['full_name']
            application.college_department = app_form.cleaned_data['college_department']
            application.student_id = app_form.cleaned_data['student_id']
            application.status = 'draft'
            application.expiry_date = timezone.now().date() + timedelta(days=365)
            application.save()
            messages.success(request, 'Personal details saved. Please provide vehicle information.')
            return redirect('sticker_portal:apply_vehicle', app_id=application.id)

    context = {
        'app_form': app_form,
        'application': application,
        'registration_open': is_registration_open(),
    }
    return render(request, 'sticker_portal/application_form_personal.html', context)


@login_required
@user_passes_test(is_applicant, login_url='/accounts/login/applicant/')
def apply_vehicle(request, app_id):
    """Step 2: Vehicle info + documents."""
    application = get_object_or_404(
        StickerApplication, id=app_id, applicant=request.user, status='draft'
    )

    try:
        vehicle = application.vehicle
        vehicle_form = VehicleForm(instance=vehicle)
    except Vehicle.DoesNotExist:
        vehicle = None
        vehicle_form = VehicleForm()

    doc_form = DocumentUploadForm()

    if request.method == 'POST':
        if not is_registration_open():
            messages.error(request, 'Registration is currently closed.')
            return redirect('sticker_portal:my_applications')

        if vehicle:
            vehicle_form = VehicleForm(request.POST, instance=vehicle)
        else:
            vehicle_form = VehicleForm(request.POST)
        doc_form = DocumentUploadForm(request.POST, request.FILES)

        if vehicle_form.is_valid() and doc_form.is_valid():
            vehicle = vehicle_form.save(commit=False)
            if vehicle.type_of_vehicle == 'other':
                vehicle.type_of_vehicle = request.POST.get('vehicle_type_other', 'other')
            if vehicle.color == 'other':
                vehicle.color = request.POST.get('color_other', 'other')
            vehicle.owner = request.user
            vehicle.save()

            application.vehicle = vehicle
            application.save()

            # Document handling (only replace if new file is uploaded)
            for doc_type, field_name in [
                ('or_cr', 'or_cr'),
                ('drivers_license', 'drivers_license'),
                ('cor', 'cor'),
                ('auth_letter', 'auth_letter'),
            ]:
                if field_name in request.FILES:
                    application.documents.filter(document_type=doc_type).delete()
                    Document.objects.create(
                        application=application,
                        document_type=doc_type,
                        file=request.FILES[field_name]
                    )

            # Conditional requirements
            if application.applicant.classification != 'student':
                application.documents.filter(document_type='cor').delete()
            if vehicle_form.cleaned_data.get('is_owner', True):
                application.documents.filter(document_type='auth_letter').delete()

            messages.success(request, 'Vehicle information saved. Please review your application.')
            return redirect('sticker_portal:confirm_application', app_id=application.id)

    context = {
        'application': application,
        'vehicle_form': vehicle_form,
        'doc_form': doc_form,
        'registration_open': is_registration_open(),
    }
    return render(request, 'sticker_portal/application_form_vehicle.html', context)


@login_required
@user_passes_test(is_applicant, login_url='/accounts/login/applicant/')
def confirm_application(request, app_id):
    application = get_object_or_404(
        StickerApplication, id=app_id, applicant=request.user, status='draft'
    )

    if request.method == 'POST':
        if not is_registration_open():
            messages.error(request, 'Registration is currently closed.')
            return redirect('sticker_portal:my_applications')

        application.status = 'pending'
        application.submitted_at = timezone.now()
        application.save()

        if not application.scheduled_datetime:
            scheduled = auto_assign_schedule(application)
            if not scheduled:
                messages.warning(request, 'Your application has been submitted, but no appointment could be automatically assigned. The administrator will assign one shortly.')
            else:
                messages.success(request, 'Your application has been submitted. Your appointment is ' 
                                  + application.scheduled_datetime.strftime('%b %d, %Y at %I:%M %p'))
        else:
            messages.success(request, 'Your application has been submitted.')

        return redirect('sticker_portal:application_success', app_id=application.id)

    documents = application.documents.all()
    context = {
        'application': application,
        'documents': documents,
        'registration_open': is_registration_open(),
    }
    return render(request, 'sticker_portal/application_confirm.html', context)


@login_required
@user_passes_test(is_applicant, login_url='/accounts/login/applicant/')
def application_success(request, app_id):
    application = get_object_or_404(StickerApplication, id=app_id, applicant=request.user)
    return render(request, 'sticker_portal/application_success.html', {
        'application': application,
    })


@login_required
@user_passes_test(is_applicant, login_url='/accounts/login/applicant/')
def my_applications(request):
    applications = StickerApplication.objects.filter(applicant=request.user).order_by('-submitted_at')
    return render(request, 'sticker_portal/my_applications.html', {'applications': applications})


@require_POST
@login_required
def delete_draft(request, app_id):
    application = get_object_or_404(StickerApplication, id=app_id, applicant=request.user, status='draft')
    application.delete()
    messages.success(request, 'Draft application deleted.')
    return redirect('sticker_portal:my_applications')


# ----------------------------------------------------------------------
# RFID Registration (Sticker Admin only)
# ----------------------------------------------------------------------
@login_required
@user_passes_test(lambda u: u.user_type == 'sticker_admin', login_url='/accounts/login/')
def sticker_register_rfid(request):
    last_pending = PendingRFIDRegistration.objects.order_by('-created_at').first()
    pending_uid = last_pending.rfid_uid if last_pending else ''

    if request.method == 'POST':
        form = RFIDRegistrationForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data

            username = data['email']
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
                user.contact_number = data['contact_number']
                user.save()

            vehicle, _ = Vehicle.objects.get_or_create(
                plate_number=data['plate_number'],
                defaults={
                    'type_of_vehicle': data['vehicle_model'],
                    'color': data['vehicle_color'],
                    'owner': user,
                    'is_owner': data['is_owner'],
                }
            )

            application = StickerApplication.objects.create(
                applicant=user,
                vehicle=vehicle,
                status='approved',
                expiry_date=data['expiry_date'],
                approved_at=timezone.now(),
                approved_by=request.user,
                submitted_at=timezone.now()
            )

            if RFIDTag.objects.filter(tag_id=data['rfid_uid']).exists():
                messages.error(request, f'RFID UID {data["rfid_uid"]} already exists!')
            else:
                RFIDTag.objects.create(
                    tag_id=data['rfid_uid'],
                    sticker_application=application,
                    is_active=True
                )
                messages.success(request, f'RFID {data["rfid_uid"]} registered successfully for {data["driver_name"]} ({data["plate_number"]})')
                PendingRFIDRegistration.objects.all().delete()
                return redirect('sticker_portal:sticker_register_rfid')
    else:
        initial = {}
        if pending_uid:
            initial['rfid_uid'] = pending_uid
        form = RFIDRegistrationForm(initial=initial)

    return render(request, 'sticker_portal/register_rfid.html', {
        'form': form,
        'pending_uid': pending_uid,
    })


# ----------------------------------------------------------------------
# Sticker Station (Phase 2 – Document Verification & Sticker Issuance)
# ----------------------------------------------------------------------
@login_required
@user_passes_test(is_sticker_admin)
def sticker_station(request):
    search_query = request.GET.get('search', '').strip()
    application = None

    if search_query:
        application = StickerApplication.objects.filter(
            Q(full_name__icontains=search_query) |
            Q(applicant__first_name__icontains=search_query) |
            Q(applicant__last_name__icontains=search_query) |
            Q(student_id__icontains=search_query) |
            Q(vehicle__plate_number__icontains=search_query) |
            Q(id__icontains=search_query)
        ).select_related('applicant', 'vehicle').prefetch_related('documents').first()

    if request.method == 'POST':
        app_id = request.POST.get('application_id')
        rfid_uid = request.POST.get('rfid_uid', '').strip()
        application = get_object_or_404(StickerApplication, id=app_id, status__in=['pending', 'approved'])

        if not rfid_uid:
            messages.error(request, 'Please provide an RFID UID.')
        elif RFIDTag.objects.filter(tag_id=rfid_uid).exists():
            messages.error(request, f'RFID {rfid_uid} is already in use.')
        elif request.POST.get('docs_verified') != 'on':
            messages.error(request, 'You must confirm that physical documents have been verified.')
        else:
            application.status = 'issued'
            application.save()
            RFIDTag.objects.create(tag_id=rfid_uid, sticker_application=application, is_active=True)
            messages.success(request, f'Sticker issued to {application.full_name} with RFID {rfid_uid}.')
            return redirect('sticker_portal:sticker_station')

    context = {
        'search_query': search_query,
        'application': application,
    }
    return render(request, 'sticker_portal/sticker_station.html', context)

@login_required
@user_passes_test(is_sticker_admin)
def set_registration_period(request):
    if request.method == 'POST':
        start = request.POST.get('start_date')
        end = request.POST.get('end_date')
        try:
            start_date = datetime.strptime(start, '%Y-%m-%d').date()
            end_date = datetime.strptime(end, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            messages.error(request, 'Invalid dates.')
            return redirect('sticker_portal:dashboard')
        period = RegistrationPeriod.load()
        period.start_date = start_date
        period.end_date = end_date
        period.save()
        messages.success(request, f'Registration period set {start_date} to {end_date}.')
    return redirect('sticker_portal:dashboard')