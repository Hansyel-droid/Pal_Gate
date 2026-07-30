import os
import shutil
from django.shortcuts import render, redirect
from django.contrib import messages
from django.utils import timezone
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.core.paginator import Paginator

from accounts.mixins import role_required
from .forms import ApplicationStep1Form, ApplicationStep2Form
from .models import StickerApplication, RegistrationWindow
from appointments.services import assign_appointment
from gate.audit import log_action


def get_active_window():
    today = timezone.now().date()
    try:
        return RegistrationWindow.objects.filter(
            is_active=True,
            start_date__lte=today,
            end_date__gte=today
        ).latest('created_at')
    except RegistrationWindow.DoesNotExist:
        return None


def save_temp_file(uploaded_file, username, field_name):
    """
    Saves an uploaded file to a temporary folder.
    Returns the temp file path so we can retrieve it later.
    """
    if not uploaded_file:
        return None
    # e.g. temp_uploads/hans/or_cr_myfile.pdf
    ext = uploaded_file.name.split('.')[-1]
    temp_path = f'temp_uploads/{username}/{field_name}.{ext}'
    # If a temp file already exists for this field, delete it first
    if default_storage.exists(temp_path):
        default_storage.delete(temp_path)
    # Save the new file
    default_storage.save(temp_path, ContentFile(uploaded_file.read()))
    return temp_path


@role_required('applicant')
def applicant_home(request):
    window = get_active_window()
    my_applications = StickerApplication.objects.filter(
        applicant=request.user
    ).order_by('-created_at')
    return render(request, 'applications/home.html', {
        'window': window,
        'my_applications': my_applications,
    })


@role_required('applicant')
def apply_step1(request):
    window = get_active_window()
    if not window:
        messages.error(request, 'Registration is currently closed. Please check back later.')
        return redirect('applicant_home')

    if request.method == 'POST':
        form = ApplicationStep1Form(request.POST)
        if form.is_valid():
            request.session['app_step1'] = form.cleaned_data
            return redirect('apply_step2')
    else:
        initial_data = {
            'full_name': request.user.get_full_name(),
            'college_department': request.user.college_department,
            'id_number': request.user.id_number,
            'classification': request.user.classification,
        }
        form = ApplicationStep1Form(initial=initial_data)

    return render(request, 'applications/step1.html', {'form': form, 'step': 1})


@role_required('applicant')
def apply_step2(request):
    if 'app_step1' not in request.session:
        messages.warning(request, 'Please complete Step 1 first.')
        return redirect('apply_step1')

    if request.method == 'POST':
        form = ApplicationStep2Form(request.POST, request.FILES)
        form.data = form.data.copy()
        form.data['step1_classification'] = request.session['app_step1'].get('classification', '')

        if form.is_valid():
            # Extra server-side safety: clear fields that shouldn't be submitted
            classification = request.session['app_step1'].get('classification', '')
            is_owner = form.cleaned_data.get('is_owner')

            if classification != 'student':
                form.cleaned_data['cor'] = None
            if is_owner == 'yes':
                form.cleaned_data['authorization_letter'] = None

            # Save vehicle data to session
            request.session['app_step2'] = {
                'plate_number': form.cleaned_data['plate_number'],
                'vehicle_type': form.cleaned_data['vehicle_type'],
                'vehicle_color': form.cleaned_data['vehicle_color'],
                'is_owner': form.cleaned_data['is_owner'],
            }

            # Save uploaded files to temp storage
            # so user does NOT need to re-upload in step 3
            username = request.user.username
            temp_paths = {}

            for field_name in ['or_cr', 'drivers_license', 'cor', 'authorization_letter']:
                uploaded = request.FILES.get(field_name)
                if uploaded:
                    path = save_temp_file(uploaded, username, field_name)
                    temp_paths[field_name] = {
                        'path': path,
                        'original_name': uploaded.name,
                    }
                else:
                    temp_paths[field_name] = None

            # Store the temp paths in the session
            request.session['app_temp_files'] = temp_paths
            request.session.modified = True
            return redirect('apply_step3')
    else:
        form = ApplicationStep2Form()

    return render(request, 'applications/step2.html', {
        'form': form,
        'step': 2,
        'step1_data': request.session.get('app_step1', {}),
    })


@role_required('applicant')
def apply_step3(request):
    if 'app_step1' not in request.session or 'app_step2' not in request.session:
        messages.warning(request, 'Please complete the previous steps first.')
        return redirect('apply_step1')

    step1 = request.session['app_step1']
    step2 = request.session['app_step2']
    temp_files = request.session.get('app_temp_files', {})

    # Build a display-friendly version of the file names
    files_info = {}
    for field_name, file_data in temp_files.items():
        if file_data:
            files_info[field_name] = file_data['original_name']
        else:
            files_info[field_name] = None

    if request.method == 'POST':
        if 'go_back' in request.POST:
            return redirect('apply_step2')

        # User clicked "Submit" — move temp files to real locations
        try:
            def get_temp_file(field_name):
                """
                Reads a temp file from storage and returns
                a ContentFile ready to save to the model.
                """
                file_data = temp_files.get(field_name)
                if not file_data:
                    return None
                path = file_data['path']
                original_name = file_data['original_name']
                if default_storage.exists(path):
                    with default_storage.open(path) as f:
                        content = f.read()
                    return ContentFile(content, name=original_name)
                return None

            application = StickerApplication(
                applicant=request.user,
                full_name=step1['full_name'],
                college_department=step1['college_department'],
                id_number=step1['id_number'],
                classification=step1['classification'],
                plate_number=step2['plate_number'],
                vehicle_type=step2['vehicle_type'],
                vehicle_color=step2['vehicle_color'],
                is_owner=(step2['is_owner'] == 'yes'),
                status='draft',
                submitted_at=timezone.now(),
            )

            # Attach the files from temp storage
            or_cr_file = get_temp_file('or_cr')
            license_file = get_temp_file('drivers_license')
            cor_file = get_temp_file('cor')
            auth_file = get_temp_file('authorization_letter')

            if or_cr_file:
                application.or_cr.save(or_cr_file.name, or_cr_file, save=False)
            if license_file:
                application.drivers_license.save(license_file.name, license_file, save=False)
            if cor_file:
                application.cor.save(cor_file.name, cor_file, save=False)
            if auth_file:
                application.authorization_letter.save(auth_file.name, auth_file, save=False)

            application.save()
            log_action(
                request,
                'app_submitted',
                f'{request.user.get_full_name()} submitted application '
                f'for plate {step2["plate_number"]}',
                extra_data={'plate': step2['plate_number']}
            )

            # Auto-assign appointment
            appointment = assign_appointment(application)

            # Clean up temp files from storage
            username = request.user.username
            for field_name, file_data in temp_files.items():
                if file_data and default_storage.exists(file_data['path']):
                    default_storage.delete(file_data['path'])

            # Clear session
            for key in ['app_step1', 'app_step2', 'app_temp_files']:
                request.session.pop(key, None)

            if appointment:
                messages.success(
                    request,
                    f'Application submitted! Your appointment is on '
                    f'{appointment.slot.date.strftime("%B %d, %Y")} at {appointment.time}.'
                )
            else:
                messages.warning(
                    request,
                    'Application submitted! No appointment slots are available yet. '
                    'The administrator will assign one soon.'
                )

            return redirect('my_applications')

        except Exception as e:
            messages.error(request, f'Error submitting application: {str(e)}')

    return render(request, 'applications/step3.html', {
        'step': 3,
        'step1': step1,
        'step2': step2,
        'files_info': files_info,
    })


@role_required('applicant')
def my_applications(request):
    applications = StickerApplication.objects.filter(
        applicant=request.user
    ).order_by('-created_at')
    paginator = Paginator(applications, 10)
    page_obj = paginator.get_page(request.GET.get('page', 1))
    return render(request, 'applications/my_applications.html', {
        'page_obj': page_obj,
    })


