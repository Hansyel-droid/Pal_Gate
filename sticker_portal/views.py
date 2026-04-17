from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.utils import timezone
from datetime import timedelta
from .models import StickerApplication
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth import update_session_auth_hash
from .forms import StickerAdminProfileForm
from .models import StickerApplication, Vehicle, Document
from .forms import VehicleForm, StickerApplicationForm, DocumentUploadForm
from django.core.files.storage import default_storage

def is_sticker_admin(user):
    return user.is_authenticated and user.user_type == 'sticker_admin'

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
def pending_approvals(request):
    applications = StickerApplication.objects.filter(status='pending').select_related(
        'applicant', 'vehicle'
    ).order_by('-submitted_at')

    three_days_ago = timezone.now() - timedelta(days=3)
    high_priority_count = applications.filter(submitted_at__lte=three_days_ago).count()

    processed_today = StickerApplication.objects.filter(
        approved_at__date=timezone.now().date()
    ).count()

    context = {
        'applications': applications,
        'high_priority_count': high_priority_count,
        'processed_today': processed_today,
    }
    return render(request, 'sticker_portal/pending_approvals.html', context)


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
        return redirect('sticker_portal:pending_approvals')

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
    else:
        profile_form = StickerAdminProfileForm(instance=request.user)
        password_form = PasswordChangeForm(user=request.user)

    context = {
        'profile_form': profile_form,
        'password_form': password_form,
    }
    return render(request, 'sticker_portal/settings.html', context)

@login_required
@user_passes_test(lambda u: u.user_type == 'applicant', login_url='/accounts/login/applicant/')
def apply(request):
    if request.method == 'POST':
        vehicle_form = VehicleForm(request.POST)
        app_form = StickerApplicationForm(request.POST)
        if vehicle_form.is_valid() and app_form.is_valid():
            vehicle = vehicle_form.save(commit=False)
            vehicle.owner = request.user
            vehicle.save()
            application = app_form.save(commit=False)
            application.applicant = request.user
            application.vehicle = vehicle
            application.save()
            # Handle file uploads (separate logic)
            # Redirect to success or my-applications
            return redirect('sticker_portal:my_applications')
    else:
        # Pre-populate user info
        initial = {
            'full_name': request.user.get_full_name(),
            'college_department': request.user.college_department,
            'student_id': request.user.student_id or request.user.employee_id,
        }
        vehicle_form = VehicleForm()
        app_form = StickerApplicationForm(initial=initial)
    
    context = {
        'vehicle_form': vehicle_form,
        'app_form': app_form,
    }
    return render(request, 'sticker_portal/application_form.html', context)


def is_applicant(user):
    return user.is_authenticated and user.user_type == 'applicant'


@login_required
@user_passes_test(is_applicant, login_url='/accounts/login/applicant/')
def apply(request):
    if request.method == 'POST':
        vehicle_form = VehicleForm(request.POST)
        app_form = StickerApplicationForm(request.POST, user=request.user)
        doc_form = DocumentUploadForm(request.POST, request.FILES)

        if vehicle_form.is_valid() and app_form.is_valid() and doc_form.is_valid():
            # Save Vehicle
            vehicle = vehicle_form.save(commit=False)
            vehicle.owner = request.user
            vehicle.save()

            # Save StickerApplication
            application = app_form.save(commit=False)
            application.applicant = request.user
            application.vehicle = vehicle
            application.status = 'pending'
            application.submitted_at = timezone.now()
            application.expiry_date = timezone.now().date() + timedelta(days=365)
            application.save()

            # Save uploaded documents
            if doc_form.cleaned_data.get('or_cr'):
                Document.objects.create(
                    application=application,
                    document_type='or_cr',
                    file=doc_form.cleaned_data['or_cr']
                )
            if doc_form.cleaned_data.get('drivers_license'):
                Document.objects.create(
                    application=application,
                    document_type='drivers_license',
                    file=doc_form.cleaned_data['drivers_license']
                )
            # COR only if student
            if doc_form.cleaned_data.get('cor') and request.POST.get('classification') == 'student':
                Document.objects.create(
                    application=application,
                    document_type='cor',
                    file=doc_form.cleaned_data['cor']
                )
            # Auth letter only if not owner
            if doc_form.cleaned_data.get('auth_letter') and request.POST.get('is_owner') == 'False':
                Document.objects.create(
                    application=application,
                    document_type='auth_letter',
                    file=doc_form.cleaned_data['auth_letter']
                )

            messages.success(request, 'Your application has been submitted successfully!')
            return redirect('sticker_portal:my_applications')
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        vehicle_form = VehicleForm()
        app_form = StickerApplicationForm(user=request.user)
        doc_form = DocumentUploadForm()

    context = {
        'vehicle_form': vehicle_form,
        'app_form': app_form,
        'doc_form': doc_form,
    }
    return render(request, 'sticker_portal/application_form.html', context)


@login_required
@user_passes_test(is_applicant, login_url='/accounts/login/applicant/')
def my_applications(request):
    applications = StickerApplication.objects.filter(applicant=request.user).order_by('-submitted_at')
    return render(request, 'sticker_portal/my_applications.html', {'applications': applications})