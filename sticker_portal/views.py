from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.utils import timezone
from datetime import timedelta, date, datetime
from django.core.files.storage import default_storage
from django.views.decorators.http import require_POST
from django.db.models import Count
import calendar
import json
from django.http import JsonResponse
from .models import StickerApplication, Vehicle, Document, AvailableDate
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

    context = {
        'pending_count': pending_count,
        'approved_today': approved_today,
        'waiting_claim': waiting_claim,
        'rejected_incomplete': rejected_incomplete,
        'high_priority': high_priority,
        'processed_today': processed_today,
        'recent_activity': recent_activity,
    }
    return render(request, 'sticker_portal/dashboard.html', context)


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
        return redirect('sticker_portal:appointment_management')

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
# Appointment Management (replaces old Pending Approvals)
# ----------------------------------------------------------------------
@login_required
@user_passes_test(is_sticker_admin, login_url='/accounts/login/')
def appointment_management(request):
    selected_date = request.GET.get('date', date.today().isoformat())
    try:
        selected_date_obj = datetime.strptime(selected_date, '%Y-%m-%d').date()
    except ValueError:
        selected_date_obj = date.today()
        selected_date = selected_date_obj.isoformat()

    applications = StickerApplication.objects.filter(
        scheduled_datetime__date=selected_date_obj
    ).select_related('applicant', 'vehicle').order_by('scheduled_datetime')

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
# Applicant Views – three‑step wizard (draft‑confirm‑submit)
# ----------------------------------------------------------------------

@login_required
@user_passes_test(is_applicant, login_url='/accounts/login/applicant/')
def apply_personal(request, app_id=None):
    """Step 1: Personal details. Creates or updates a draft."""
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
    }
    return render(request, 'sticker_portal/application_form_personal.html', context)


@login_required
@user_passes_test(is_applicant, login_url='/accounts/login/applicant/')
def apply_vehicle(request, app_id):
    """Step 2: Vehicle info + documents. Draft must already exist."""
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

            # Remove old documents if re-uploading
            application.documents.all().delete()

            # OR/CR
            if 'or_cr' in request.FILES:
                application.documents.filter(document_type='or_cr').delete()
                Document.objects.create(
                    application=application,
                    document_type='or_cr',
                    file=request.FILES['or_cr']
                )
            # Driver's License
            if 'drivers_license' in request.FILES:
                application.documents.filter(document_type='drivers_license').delete()
                Document.objects.create(
                    application=application,
                    document_type='drivers_license',
                    file=request.FILES['drivers_license']
                )
            # COR (student only)
            if application.applicant.classification == 'student' and 'cor' in request.FILES:
                application.documents.filter(document_type='cor').delete()
                Document.objects.create(
                    application=application,
                    document_type='cor',
                    file=request.FILES['cor']
                )
            # Authorization letter (non-owner only)
            if not vehicle_form.cleaned_data.get('is_owner', True) and 'auth_letter' in request.FILES:
                application.documents.filter(document_type='auth_letter').delete()
                Document.objects.create(
                    application=application,
                    document_type='auth_letter',
                    file=request.FILES['auth_letter']
                )

            messages.success(request, 'Vehicle information saved. Please review your application.')
            return redirect('sticker_portal:confirm_application', app_id=application.id)

    context = {
        'application': application,
        'vehicle_form': vehicle_form,
        'doc_form': doc_form,
    }
    return render(request, 'sticker_portal/application_form_vehicle.html', context)


@login_required
@user_passes_test(is_applicant, login_url='/accounts/login/applicant/')
def confirm_application(request, app_id):
    application = get_object_or_404(
        StickerApplication, id=app_id, applicant=request.user, status='draft'
    )

    if request.method == 'POST':
        application.status = 'pending'
        application.submitted_at = timezone.now()
        application.save()
        messages.success(request, 'Your application has been submitted. You will receive an appointment date later.')
        return redirect('sticker_portal:application_success', app_id=application.id)

    documents = application.documents.all()
    return render(request, 'sticker_portal/application_confirm.html', {
        'application': application,
        'documents': documents,
    })


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
                    'type_of_vehicle': data['vehicle_model'],  # now a choice field (two_wheels, four_wheels, other)
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