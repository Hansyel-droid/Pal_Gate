import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import FileResponse, Http404
from django.urls import reverse
from django.utils import timezone
from django.core.files.storage import default_storage
from django.core.files.base import File
from django.core.paginator import Paginator

from accounts.mixins import role_required
from .forms import ApplicationStep1Form, ApplicationStep2Form
from .models import StickerApplication, RegistrationWindow
from appointments.models import AppointmentSlot, Appointment
from appointments.services import (
    get_bookable_dates, get_bookable_slot, get_time_options, book_appointment,
)
from gate.audit import log_action

logger = logging.getLogger('django')

DOCUMENT_FIELDS = {'or_cr', 'drivers_license', 'cor', 'authorization_letter'}


class AppointmentNoLongerAvailable(Exception):
    """
    Raised inside apply_step4's submit transaction when the applicant's
    chosen (date, time) filled up or was deactivated between picking it
    and hitting Submit. Caught separately from generic errors so the
    applicant is sent back to re-pick a time instead of seeing a vague
    failure message.
    """
    pass


def get_active_window():
    today = timezone.localdate()
    try:
        return RegistrationWindow.objects.filter(
            is_active=True,
            start_date__lte=today,
            end_date__gte=today
        ).latest('created_at')
    except RegistrationWindow.DoesNotExist:
        return None


def temp_path_for(user, field_name):
    """
    Where a given applicant's in-progress upload for `field_name` lives.

    Keyed on the user's primary key, never their username: Django's
    UnicodeUsernameValidator accepts '.' (so '..' is a legal username) and
    RegisterForm doesn't narrow that, which made a legal account able to
    push a traversing path into storage. Django itself blocks the traversal,
    but it does so by raising SuspiciousFileOperation, which nothing here
    caught — a 500 on Step 2 for that applicant, every time.

    The name deliberately carries NO extension. It used to, which meant
    re-uploading or_cr as a .png after a .pdf wrote a second file instead of
    replacing the first, orphaning the original until cleanup ran. The real
    extension is preserved in the session's `original_name` and restored
    when the file is copied onto the model at submit.
    """
    return f'temp_uploads/{user.pk}/{field_name}'


def save_temp_file(uploaded_file, user, field_name):
    """
    Saves an uploaded file to a temporary folder.
    Returns the temp file path so we can retrieve it later.
    """
    if not uploaded_file:
        return None
    temp_path = temp_path_for(user, field_name)
    # If a temp file already exists for this field, delete it first
    if default_storage.exists(temp_path):
        default_storage.delete(temp_path)
    # Hand storage the upload itself rather than ContentFile(f.read()).
    # Reading it defeated Django's own handling, which already spools
    # anything over 2.5MB to disk: at a 10MB cap across four document
    # fields, buffering pulled the whole submission back into RAM.
    # Trust storage's returned name rather than assuming it took ours.
    return default_storage.save(temp_path, uploaded_file)


def delete_temp_file(file_data):
    """
    Removes a temp file recorded in session, if it's still there. Takes the
    session's {'path', 'original_name'} entry (or None) so callers don't
    have to unpack it themselves.
    """
    if file_data and default_storage.exists(file_data['path']):
        default_storage.delete(file_data['path'])


def copy_document_to_temp(file_field, user, field_name):
    """
    Copies an already-uploaded document from a previous (expired)
    application into temp storage for a new one — used by apply_renew so a
    renewing applicant doesn't have to re-upload documents that haven't
    changed. Produces the exact same {'path', 'original_name'} shape
    apply_step2 leaves in session, so apply_step3 can't tell the
    difference between a fresh upload and a reused one.
    """
    if not file_field:
        return None
    temp_path = temp_path_for(user, field_name)
    if default_storage.exists(temp_path):
        default_storage.delete(temp_path)
    # Streamed, not read into memory — storage copies it in chunks.
    with file_field.open('rb') as source:
        saved_path = default_storage.save(temp_path, source)
    original_name = file_field.name.rsplit('/', 1)[-1]
    return {'path': saved_path, 'original_name': original_name}


@role_required('applicant')
def applicant_home(request):
    window = get_active_window()
    # The table on this page renders app.appointment.slot.date for each row.
    my_applications = StickerApplication.objects.filter(
        applicant=request.user
    ).select_related('appointment', 'appointment__slot').order_by('-created_at')
    return render(request, 'applications/home.html', {
        'window': window,
        'my_applications': my_applications,
    })


@role_required('applicant')
def apply_renew(request, pk):
    """
    Lets an applicant with an EXPIRED sticker start a new application
    pre-filled from the expired one, instead of the blank wizard — the
    "lighter renewal reusing prior details" called for in PRODUCT.md.

    This view's only job is to seed the same session keys apply_step1 and
    apply_step2 would have left behind (including copies of the previous
    documents in temp storage), then hand off to apply_step3 so the
    applicant still picks a fresh inspection date/time (their old
    appointment was for the now-expired sticker). Neither apply_step3 nor
    apply_step4 are touched — they can't tell a renewal from a normal
    submission, which keeps this feature from having to duplicate or fork
    the submit logic.
    """
    old_application = get_object_or_404(
        StickerApplication, pk=pk, applicant=request.user
    )

    if old_application.status != 'expired':
        messages.error(
            request,
            'Only an expired application can be renewed. '
            'If this one is still active, no renewal is needed yet.'
        )
        return redirect('my_applications')

    window = get_active_window()
    if not window:
        messages.error(
            request,
            'Registration is currently closed, so renewal isn\'t available '
            'yet. Please check back once the next registration window opens.'
        )
        return redirect('my_applications')

    request.session['app_step1'] = {
        'full_name': old_application.full_name,
        'college_department': old_application.college_department,
        'id_number': old_application.id_number,
        'classification': old_application.classification,
    }
    request.session['app_step2'] = {
        'plate_number': old_application.plate_number,
        'vehicle_type': old_application.vehicle_type,
        'vehicle_color': old_application.vehicle_color,
        'is_owner': 'yes' if old_application.is_owner else 'no',
    }

    user = request.user
    request.session['app_temp_files'] = {
        'or_cr': copy_document_to_temp(old_application.or_cr, user, 'or_cr'),
        'drivers_license': copy_document_to_temp(
            old_application.drivers_license, user, 'drivers_license'
        ),
        'cor': copy_document_to_temp(old_application.cor, user, 'cor'),
        'authorization_letter': copy_document_to_temp(
            old_application.authorization_letter, user, 'authorization_letter'
        ),
    }
    # Read by apply_step4 only to enrich the audit log description below —
    # not required for the submission itself to work.
    request.session['app_renewal_of'] = old_application.pk
    request.session.modified = True

    messages.info(
        request,
        'We\'ve pre-filled this renewal from your previous application. '
        'Review everything below — go back to Step 2 if any document needs '
        'to be updated.'
    )
    return redirect('apply_step3')


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

    classification = request.session['app_step1'].get('classification', '')

    # Documents the applicant has already given us on an earlier pass
    # through this step: a renewal seeded by apply_renew, or a return trip
    # via the "Change" links on Step 3 and Step 4.
    existing_files = {
        name: data
        for name, data in (request.session.get('app_temp_files') or {}).items()
        if data
    }

    if request.method == 'POST':
        form = ApplicationStep2Form(
            request.POST, request.FILES, existing_files=existing_files
        )
        form.data = form.data.copy()
        form.data['step1_classification'] = classification

        if form.is_valid():
            is_owner = form.cleaned_data.get('is_owner')

            # Save vehicle data to session
            request.session['app_step2'] = {
                'plate_number': form.cleaned_data['plate_number'],
                'vehicle_type': form.cleaned_data['vehicle_type'],
                'vehicle_color': form.cleaned_data['vehicle_color'],
                'is_owner': form.cleaned_data['is_owner'],
            }

            # Which documents this application actually needs. Server-side
            # authority over the template's JS, and it also decides whether
            # a document held from an earlier pass is still wanted.
            applies = {
                'or_cr': True,
                'drivers_license': True,
                'cor': classification == 'student',
                'authorization_letter': is_owner == 'no',
            }

            # Save uploaded files to temp storage so the applicant does NOT
            # need to re-upload on a later step. A field left blank keeps
            # whatever is already on file rather than dropping it.
            temp_paths = {}
            for field_name in ApplicationStep2Form.DOCUMENT_FIELDS:
                uploaded = request.FILES.get(field_name)
                previous = existing_files.get(field_name)

                if not applies[field_name]:
                    delete_temp_file(previous)
                    temp_paths[field_name] = None
                elif uploaded:
                    path = save_temp_file(uploaded, request.user, field_name)
                    temp_paths[field_name] = {
                        'path': path,
                        'original_name': uploaded.name,
                    }
                else:
                    temp_paths[field_name] = previous

            # Store the temp paths in the session
            request.session['app_temp_files'] = temp_paths
            request.session.modified = True
            return redirect('apply_step3')
    else:
        # Seed from session so returning to this step shows what was already
        # entered instead of a blank form that silently discards it.
        form = ApplicationStep2Form(
            initial=request.session.get('app_step2'),
            existing_files=existing_files,
        )

    return render(request, 'applications/step2.html', {
        'form': form,
        'step': 2,
        'step1_data': request.session.get('app_step1', {}),
        'existing_file_names': {
            name: data['original_name'] for name, data in existing_files.items()
        },
    })


@role_required('applicant')
def apply_step3(request):
    """
    Lets the applicant choose their own inspection date and time, instead
    of one being auto-assigned. A plain GET shows the list of open days;
    GET with ?date=YYYY-MM-DD shows that day's time options with live
    remaining-seat counts (each time holds up to AppointmentSlot.capacity
    applicants); POSTing a chosen slot_id + time stores the choice in
    session and hands off to apply_step4 for final review.
    """
    if 'app_step1' not in request.session or 'app_step2' not in request.session:
        messages.warning(request, 'Please complete the previous steps first.')
        return redirect('apply_step1')

    if request.method == 'POST':
        slot_id = request.POST.get('slot_id')
        time = request.POST.get('time')
        slot = get_bookable_slot(slot_id) if slot_id else None

        if not slot or not time or time not in dict(Appointment.TIME_CHOICES):
            messages.error(request, 'Please choose a date and an available time.')
            return redirect('apply_step3')

        # Soft check for a friendly message — the authoritative check (with
        # row locking) happens again in book_appointment at final submit,
        # since a slot can still fill up between this pick and that click.
        if slot.is_time_full(time):
            messages.error(
                request,
                f'{dict(Appointment.TIME_CHOICES).get(time, time)} on '
                f'{slot.date.strftime("%B %d, %Y")} just filled up. '
                'Please choose another time.'
            )
            return redirect(f"{reverse('apply_step3')}?date={slot.date.isoformat()}")

        request.session['app_appointment'] = {'slot_id': slot.pk, 'time': time}
        request.session.modified = True
        return redirect('apply_step4')

    dates = list(get_bookable_dates())

    selected_slot = None
    time_options = None
    selected_date = request.GET.get('date')
    if selected_date:
        selected_slot = next(
            (s for s in dates if s.date.isoformat() == selected_date), None
        )
        if selected_slot:
            time_options = get_time_options(selected_slot)
        else:
            messages.warning(request, 'That date is not open for booking.')

    return render(request, 'applications/step3.html', {
        'step': 3,
        'dates': dates,
        'selected_slot': selected_slot,
        'time_options': time_options,
    })


@role_required('applicant')
def apply_step4(request):
    has_prior_steps = (
        'app_step1' in request.session and 'app_step2' in request.session
    )
    if not has_prior_steps:
        messages.warning(request, 'Please complete the previous steps first.')
        return redirect('apply_step1')
    if 'app_appointment' not in request.session:
        messages.warning(request, 'Please choose an appointment date and time first.')
        return redirect('apply_step3')

    step1 = request.session['app_step1']
    step2 = request.session['app_step2']
    temp_files = request.session.get('app_temp_files', {})
    appointment_choice = request.session['app_appointment']

    appointment_slot = AppointmentSlot.objects.filter(
        pk=appointment_choice['slot_id']
    ).first()
    if not appointment_slot:
        request.session.pop('app_appointment', None)
        messages.error(
            request,
            'Your chosen appointment date is no longer available. '
            'Please pick another.'
        )
        return redirect('apply_step3')
    appointment_time_label = dict(Appointment.TIME_CHOICES).get(
        appointment_choice['time'], appointment_choice['time']
    )

    # Build a display-friendly version of the file names
    files_info = {}
    for field_name, file_data in temp_files.items():
        if file_data:
            files_info[field_name] = file_data['original_name']
        else:
            files_info[field_name] = None

    if request.method == 'POST':
        if 'go_back' in request.POST:
            return redirect('apply_step3')

        # User clicked "Submit" — move temp files to real locations
        def temp_file_available(field_name):
            file_data = temp_files.get(field_name)
            return bool(file_data) and default_storage.exists(file_data['path'])

        def attach_document(field_file, field_name):
            """
            Streams a temp file onto the model's file field, under its
            original name.

            The handle is open only for the copy itself — FieldFile.save()
            writes through to storage synchronously — and storage copies in
            chunks. It used to be read into a bytes object and then into a
            ContentFile: at the form's 10MB-per-field cap across four
            document fields that's ~40MB resident per submission, held twice
            over for the length of the transaction and multiplied by every
            concurrent submitter. It also defeated Django's own >2.5MB
            spill-to-disk, which had already kept the upload off the heap.
            """
            file_data = temp_files.get(field_name)
            if not file_data or not default_storage.exists(file_data['path']):
                return
            with default_storage.open(file_data['path'], 'rb') as handle:
                field_file.save(
                    file_data['original_name'], File(handle), save=False
                )

        # or_cr and drivers_license are always required. If a temp file
        # went missing (e.g. cleaned up by the scheduled task while the
        # applicant was mid-wizard), don't silently save a broken record —
        # send them back to re-upload.
        if not temp_file_available('or_cr') or not temp_file_available('drivers_license'):
            messages.error(
                request,
                'One or more of your required documents could not be found '
                '(they may have expired). Please re-upload them.'
            )
            return redirect('apply_step2')

        try:
            with transaction.atomic():
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

                attach_document(application.or_cr, 'or_cr')
                attach_document(application.drivers_license, 'drivers_license')
                attach_document(application.cor, 'cor')
                attach_document(
                    application.authorization_letter, 'authorization_letter'
                )

                application.save()

                # Book the appointment the applicant chose in step 3 —
                # inside the same transaction, so if it's no longer
                # available the application (and its unique plate number)
                # is never committed either, and the applicant can just
                # pick a new time instead of being stuck with a half-done
                # submission.
                appointment = book_appointment(
                    application,
                    appointment_choice['slot_id'],
                    appointment_choice['time'],
                )
                if appointment is None:
                    raise AppointmentNoLongerAvailable()

                renewed_from = request.session.get('app_renewal_of')
                description = (
                    f'{request.user.get_full_name()} submitted application '
                    f'for plate {step2["plate_number"]}'
                )
                extra_data = {'plate': step2['plate_number']}
                if renewed_from:
                    description += f' (renewal of application #{renewed_from})'
                    extra_data['renewed_from'] = renewed_from

                log_action(
                    request,
                    'app_submitted',
                    description,
                    extra_data=extra_data,
                )
        except AppointmentNoLongerAvailable:
            request.session.pop('app_appointment', None)
            messages.error(
                request,
                'Sorry — that appointment time just filled up. '
                'Please choose another date or time.'
            )
            return redirect('apply_step3')
        except Exception:
            logger.exception('Error submitting application for user %s', request.user.username)
            messages.error(
                request,
                'Something went wrong submitting your application. '
                'Please try again, or contact the office if this keeps happening.'
            )
            return render(request, 'applications/step4.html', {
                'step': 4,
                'step1': step1,
                'step2': step2,
                'files_info': files_info,
                'appointment_slot': appointment_slot,
                'appointment_time_label': appointment_time_label,
            })

        # Clean up temp files from storage (only after a successful commit)
        for file_data in temp_files.values():
            delete_temp_file(file_data)

        # Clear session
        for key in [
            'app_step1', 'app_step2', 'app_temp_files',
            'app_renewal_of', 'app_appointment',
        ]:
            request.session.pop(key, None)

        messages.success(
            request,
            f'Application submitted! Your appointment is on '
            f'{appointment_slot.date.strftime("%B %d, %Y")} at {appointment_time_label}.'
        )
        return redirect('my_applications')

    return render(request, 'applications/step4.html', {
        'step': 4,
        'step1': step1,
        'step2': step2,
        'files_info': files_info,
        'appointment_slot': appointment_slot,
        'appointment_time_label': appointment_time_label,
    })


@role_required('applicant')
def my_applications(request):
    applications = StickerApplication.objects.filter(
        applicant=request.user
    ).select_related('appointment', 'appointment__slot').order_by('-created_at')
    paginator = Paginator(applications, 10)
    page_obj = paginator.get_page(request.GET.get('page', 1))
    return render(request, 'applications/my_applications.html', {
        'page_obj': page_obj,
    })


@login_required
def serve_document(request, pk, field_name):
    """
    Streams an applicant's uploaded ID/vehicle document.
    These are government IDs, OR/CR, etc. — only the owning applicant or an
    admin staff member may view them. Never served as a raw static file.
    """
    if field_name not in DOCUMENT_FIELDS:
        raise Http404('Unknown document field.')

    application = get_object_or_404(StickerApplication, pk=pk)

    if request.user.role != 'admin' and request.user != application.applicant:
        raise PermissionDenied('You do not have permission to view this document.')

    file_field = getattr(application, field_name)
    if not file_field:
        raise Http404('No document uploaded for this field.')

    # A row can outlive its file — media wiped, restored from a DB-only
    # backup, storage remounted. That's a missing document, not a server
    # fault, so it gets a 404 instead of an uncaught FileNotFoundError 500.
    try:
        handle = file_field.open('rb')
    except FileNotFoundError:
        logger.warning(
            'serve_document: application %s field %s points at missing file %s',
            pk, field_name, file_field.name,
        )
        raise Http404('This document is no longer available.')

    return FileResponse(
        handle,
        filename=file_field.name.rsplit('/', 1)[-1],
    )


