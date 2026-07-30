import uuid
from datetime import timedelta

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.db.models import Q, Count

from accounts.mixins import role_required
from accounts.models import User
from applications.models import StickerApplication, RegistrationWindow
from appointments.models import AppointmentSlot, Appointment
from gate.audit import log_action
from gate.models import GateLog


@role_required('admin')
def admin_dashboard(request):
    today = timezone.now().date()
    three_days_ago = timezone.now() - timedelta(days=3)

    # Statistics
    stats = {
        'pending': StickerApplication.objects.filter(status='scheduled').count(),
        'approved_today': StickerApplication.objects.filter(
            status='approved',
            updated_at__date=today
        ).count(),
        'waiting_claim': StickerApplication.objects.filter(status='approved').count(),
        'rejected': StickerApplication.objects.filter(status='rejected').count(),
        'high_priority': StickerApplication.objects.filter(
            status='scheduled',
            submitted_at__lte=three_days_ago
        ).count(),
        'processed_today': StickerApplication.objects.filter(
            status__in=['approved', 'rejected', 'issued'],
            updated_at__date=today
        ).count(),
        'issued': StickerApplication.objects.filter(status='issued').count(),
    }

    # Recent activity — last 5 updated applications
    recent = StickerApplication.objects.order_by('-updated_at')[:5]

    return render(request, 'sticker_admin/dashboard.html', {
        'stats': stats,
        'recent': recent,
    })


@role_required('admin')
def registration_window(request):
    # Get the current active window if any
    current_window = RegistrationWindow.objects.filter(is_active=True).first()

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'save':
            start_date = request.POST.get('start_date')
            end_date = request.POST.get('end_date')

            if not start_date or not end_date:
                messages.error(request, 'Please provide both start and end dates.')
            elif start_date > end_date:
                messages.error(request, 'Start date must be before end date.')
            else:
                # Deactivate any existing windows
                RegistrationWindow.objects.filter(is_active=True).update(is_active=False)
                # Create the new window
                RegistrationWindow.objects.create(
                    start_date=start_date,
                    end_date=end_date,
                    is_active=True
                )
                log_action(
                    request,
                    'window_opened',
                    f'Registration window opened: {start_date} to {end_date}',
                    extra_data={'start': start_date, 'end': end_date}
                )
                messages.success(request, 'Registration window saved successfully.')
                return redirect('registration_window')

        elif action == 'close':
            RegistrationWindow.objects.filter(is_active=True).update(is_active=False)
            log_action(request, 'window_closed', 'Registration window closed.')
            messages.success(request, 'Registration window closed.')
            return redirect('registration_window')

    return render(request, 'sticker_admin/registration_window.html', {
        'current_window': current_window,
    })


@role_required('admin')
def appointment_dates(request):
    slots = AppointmentSlot.objects.order_by('date')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'activate_range':
            start = request.POST.get('range_start')
            end = request.POST.get('range_end')
            if not start or not end:
                messages.error(request, 'Please select both a start and end date.')
            elif start > end:
                messages.error(request, 'Start date must be before end date.')
            else:
                from datetime import date
                start_date = date.fromisoformat(start)
                end_date = date.fromisoformat(end)
                current = start_date
                created_count = 0
                while current <= end_date:
                    if current.weekday() < 5:
                        _, created = AppointmentSlot.objects.get_or_create(
                            date=current,
                            defaults={'is_active': True, 'capacity': 20}
                        )
                        if created:
                            created_count += 1
                    current += timedelta(days=1)
                messages.success(request, f'{created_count} new appointment date(s) activated.')
                return redirect('appointment_dates')

        elif action == 'toggle':
            slot_id = request.POST.get('slot_id')
            slot = get_object_or_404(AppointmentSlot, pk=slot_id)
            slot.is_active = not slot.is_active
            slot.save()
            status = 'activated' if slot.is_active else 'deactivated'
            messages.success(request, f'Slot on {slot.date} {status}.')
            return redirect('appointment_dates')

        elif action == 'delete':
            slot_id = request.POST.get('slot_id')
            slot = get_object_or_404(AppointmentSlot, pk=slot_id)
            if slot.appointments.exists():
                messages.error(request, f'Cannot delete {slot.date} — it has existing appointments.')
            else:
                slot.delete()
                messages.success(request, f'Slot on {slot.date} deleted.')
            return redirect('appointment_dates')

        elif action == 'delete_selected':
            # Bulk delete selected slots
            selected_ids = request.POST.getlist('selected_slots')
            if not selected_ids:
                messages.warning(request, 'No dates selected.')
                return redirect('appointment_dates')
            deleted = 0
            skipped = 0
            for slot_id in selected_ids:
                try:
                    slot = AppointmentSlot.objects.get(pk=slot_id)
                    if slot.appointments.exists():
                        skipped += 1
                    else:
                        slot.delete()
                        deleted += 1
                except AppointmentSlot.DoesNotExist:
                    pass
            msg = f'{deleted} date(s) deleted.'
            if skipped:
                msg += f' {skipped} skipped (have existing appointments).'
            messages.success(request, msg)
            return redirect('appointment_dates')

        elif action == 'delete_all_empty':
            # Delete all slots that have NO appointments booked
            empty_slots = AppointmentSlot.objects.filter(appointments__isnull=True)
            count = empty_slots.count()
            empty_slots.delete()
            messages.success(request, f'{count} empty slot(s) deleted.')
            return redirect('appointment_dates')

        elif action == 'deactivate_all':
            count = AppointmentSlot.objects.filter(is_active=True).update(is_active=False)
            messages.success(request, f'{count} slot(s) deactivated.')
            return redirect('appointment_dates')

        elif action == 'activate_all':
            count = AppointmentSlot.objects.filter(is_active=False).update(is_active=True)
            messages.success(request, f'{count} slot(s) activated.')
            return redirect('appointment_dates')

    # Stats for the summary bar
    total = slots.count()
    active = slots.filter(is_active=True).count()
    full = sum(1 for s in slots if s.is_full())
    booked = sum(s.appointments.count() for s in slots)

    return render(request, 'sticker_admin/appointment_dates.html', {
        'slots': slots,
        'total': total,
        'active': active,
        'full': full,
        'booked': booked,
    })


@role_required('admin')
def application_list(request):
    applications = StickerApplication.objects.all().order_by('-created_at')

    # Search
    query = request.GET.get('q', '')
    if query:
        applications = applications.filter(
            Q(full_name__icontains=query) |
            Q(id_number__icontains=query) |
            Q(plate_number__icontains=query) |
            Q(sticker_id__icontains=query)
        )

    # Filter by status
    status_filter = request.GET.get('status', '')
    if status_filter:
        applications = applications.filter(status=status_filter)

    return render(request, 'sticker_admin/application_list.html', {
        'applications': applications,
        'query': query,
        'status_filter': status_filter,
        'status_choices': StickerApplication.STATUS_CHOICES,
    })


@role_required('admin')
def application_detail(request, pk):
    application = get_object_or_404(StickerApplication, pk=pk)

    # Get appointment if exists
    appointment = getattr(application, 'appointment', None)

    # Get all available slots for manual reassignment
    available_slots = AppointmentSlot.objects.filter(
        is_active=True
    ).order_by('date')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'set_appointment':
            slot_id = request.POST.get('slot_id')
            time = request.POST.get('time')

            if not slot_id or not time:
                messages.error(request, 'Please select both a date and time.')
            else:
                slot = get_object_or_404(AppointmentSlot, pk=slot_id)
                if appointment:
                    appointment.slot = slot
                    appointment.time = time
                    appointment.save()
                else:
                    Appointment.objects.create(
                        application=application,
                        slot=slot,
                        time=time
                    )
                application.status = 'scheduled'
                application.save()
                messages.success(request, 'Appointment updated successfully.')
                return redirect('application_detail', pk=pk)

    return render(request, 'sticker_admin/application_detail.html', {
        'application': application,
        'appointment': appointment,
        'available_slots': available_slots,
        'time_choices': Appointment.TIME_CHOICES,
    })


@role_required('admin')
def approve_application(request, pk):
    application = get_object_or_404(StickerApplication, pk=pk)
    if request.method == 'POST':
        application.status = 'approved'
        application.save()
        log_action(
            request,
            'app_approved',
            f'Approved application for {application.full_name} '
            f'(Plate: {application.plate_number})',
            target_user=application.applicant.username,
            extra_data={'application_id': pk}
        )
        messages.success(
            request,
            f'Application for {application.full_name} approved.'
        )
    return redirect('application_detail', pk=pk)


@role_required('admin')
def reject_application(request, pk):
    application = get_object_or_404(StickerApplication, pk=pk)
    if request.method == 'POST':
        reason = request.POST.get('rejection_reason', '')
        application.status = 'rejected'
        application.rejection_reason = reason
        application.save()
        log_action(
            request,
            'app_rejected',
            f'Rejected application for {application.full_name}. Reason: {reason}',
            target_user=application.applicant.username,
            extra_data={'application_id': pk, 'reason': reason}
        )
        messages.warning(
            request,
            f'Application for {application.full_name} rejected.'
        )
    return redirect('application_detail', pk=pk)


@role_required('admin')
def sticker_station(request):
    query = request.GET.get('q', '')
    results = []

    if query:
        results = StickerApplication.objects.filter(
            Q(full_name__icontains=query) |
            Q(id_number__icontains=query) |
            Q(plate_number__icontains=query) |
            Q(sticker_id__icontains=query),
            status='approved'
        )

    return render(request, 'sticker_admin/sticker_station.html', {
        'query': query,
        'results': results,
    })


@role_required('admin')
def issue_sticker(request, pk):
    application = get_object_or_404(StickerApplication, pk=pk)

    if request.method == 'POST':
        rfid_uid = request.POST.get('rfid_uid', '').strip()

        if not rfid_uid:
            messages.error(request, 'Please enter or scan an RFID UID.')
            return redirect('issue_sticker', pk=pk)

        # Check if RFID is already used by another application
        if StickerApplication.objects.filter(
            rfid_uid=rfid_uid
        ).exclude(pk=pk).exists():
            messages.error(
                request,
                f'RFID tag {rfid_uid} is already assigned to another application.'
            )
            return redirect('issue_sticker', pk=pk)

        # Generate a unique sticker ID
        sticker_id = f'PalSU-{uuid.uuid4().hex[:8].upper()}'

        # Issue the sticker
        application.rfid_uid = rfid_uid
        application.sticker_id = sticker_id
        application.status = 'issued'
        application.save()
        log_action(
            request,
            'sticker_issued',
            f'Sticker issued to {application.full_name}. '
            f'Sticker ID: {sticker_id}, RFID: {rfid_uid}',
            target_user=application.applicant.username,
            extra_data={
                'application_id': pk,
                'sticker_id': sticker_id,
                'rfid_uid': rfid_uid,
            }
        )
        messages.success(
            request,
            f'Sticker issued! Sticker ID: {sticker_id}, RFID: {rfid_uid}'
        )
        return redirect('application_detail', pk=application.pk)

    return render(request, 'sticker_admin/issue_sticker.html', {
        'application': application,
    })


def build_username(id_number, full_name):
    """
    Build a unique username for a walk-in registrant.
    Falls back to the person's name if they have no usable ID number.
    """
    base = ''.join(c for c in id_number.lower() if c.isalnum())
    if not base:
        base = ''.join(c for c in full_name.lower() if c.isalnum()) or 'walkin'
    base = base[:140]

    username = base
    counter = 1
    while User.objects.filter(username=username).exists():
        counter += 1
        username = f'{base}{counter}'
    return username


@role_required('admin')
def quick_register(request):
    """
    Walk-in / emergency registration (spec 5.5).
    Creates the applicant account and issues the sticker in one step,
    bypassing the 3-step wizard and the appointment queue.
    """
    if request.method == 'POST':
        data = {
            'full_name': request.POST.get('full_name', '').strip(),
            'id_number': request.POST.get('id_number', '').strip(),
            'college_department': request.POST.get('college_department', '').strip(),
            'classification': request.POST.get('classification', '').strip(),
            'plate_number': request.POST.get('plate_number', '').strip().upper(),
            'vehicle_type': request.POST.get('vehicle_type', '').strip(),
            'vehicle_color': request.POST.get('vehicle_color', '').strip(),
            'rfid_uid': request.POST.get('rfid_uid', '').strip(),
        }

        errors = []

        # All fields are mandatory for a quick registration
        labels = {
            'full_name': 'Full name',
            'id_number': 'ID number',
            'college_department': 'College / Department',
            'classification': 'Classification',
            'plate_number': 'Plate number',
            'vehicle_type': 'Vehicle type',
            'vehicle_color': 'Vehicle color',
            'rfid_uid': 'RFID tag UID',
        }
        for field, label in labels.items():
            if not data[field]:
                errors.append(f'{label} is required.')

        # Only accept values that exist in the model's choice lists
        valid_choices = {
            'classification': StickerApplication.CLASSIFICATION_CHOICES,
            'vehicle_type': StickerApplication.VEHICLE_TYPE_CHOICES,
            'vehicle_color': StickerApplication.COLOR_CHOICES,
        }
        for field, choices in valid_choices.items():
            if data[field] and data[field] not in [c[0] for c in choices]:
                errors.append(f'{labels[field]} is not a valid choice.')

        # Uniqueness checks
        if data['plate_number'] and StickerApplication.objects.filter(
            plate_number__iexact=data['plate_number']
        ).exists():
            errors.append(
                f'Plate number {data["plate_number"]} is already registered.'
            )

        if data['rfid_uid'] and StickerApplication.objects.filter(
            rfid_uid=data['rfid_uid']
        ).exists():
            errors.append(
                f'RFID tag {data["rfid_uid"]} is already assigned to another vehicle.'
            )

        if errors:
            for error in errors:
                messages.error(request, error)
            return render(request, 'sticker_admin/quick_register.html', {
                'form_data': data,
                'classification_choices': StickerApplication.CLASSIFICATION_CHOICES,
                'vehicle_type_choices': StickerApplication.VEHICLE_TYPE_CHOICES,
                'color_choices': StickerApplication.COLOR_CHOICES,
            })

        # Create the applicant account that owns this record.
        # No password is set — this is a walk-in record, not a portal login.
        name_parts = data['full_name'].split()
        applicant = User(
            username=build_username(data['id_number'], data['full_name']),
            first_name=name_parts[0],
            last_name=' '.join(name_parts[1:]),
            role='applicant',
            college_department=data['college_department'],
            id_number=data['id_number'],
            classification=data['classification'],
        )
        applicant.set_unusable_password()
        applicant.save()

        sticker_id = f'PalSU-{uuid.uuid4().hex[:8].upper()}'

        application = StickerApplication.objects.create(
            applicant=applicant,
            status='issued',
            full_name=data['full_name'],
            college_department=data['college_department'],
            id_number=data['id_number'],
            classification=data['classification'],
            plate_number=data['plate_number'],
            vehicle_type=data['vehicle_type'],
            vehicle_color=data['vehicle_color'],
            is_owner=True,
            rfid_uid=data['rfid_uid'],
            sticker_id=sticker_id,
            submitted_at=timezone.now(),
        )

        log_action(
            request,
            'sticker_issued',
            f'Quick registration — sticker issued to {application.full_name}. '
            f'Sticker ID: {sticker_id}, Plate: {application.plate_number}, '
            f'RFID: {application.rfid_uid}',
            target_user=applicant.username,
            extra_data={
                'application_id': application.pk,
                'sticker_id': sticker_id,
                'rfid_uid': application.rfid_uid,
                'quick_register': True,
            }
        )

        messages.success(
            request,
            f'{application.full_name} registered and sticker issued. '
            f'Sticker ID: {sticker_id}'
        )
        return redirect('application_detail', pk=application.pk)

    return render(request, 'sticker_admin/quick_register.html', {
        'form_data': {},
        'classification_choices': StickerApplication.CLASSIFICATION_CHOICES,
        'vehicle_type_choices': StickerApplication.VEHICLE_TYPE_CHOICES,
        'color_choices': StickerApplication.COLOR_CHOICES,
    })