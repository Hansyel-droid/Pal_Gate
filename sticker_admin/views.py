import logging
import uuid
from datetime import timedelta

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.db.models import Q, Count

from accounts.mixins import role_required
from applications.models import StickerApplication, RegistrationWindow
from applications.notifications import (
    notify_appointment_assigned,
    notify_approved,
    notify_rejected,
    notify_sticker_issued,
)
from appointments.models import AppointmentSlot, Appointment
from gate.audit import log_action
from gate.models import GateLog, PendingRFID

logger = logging.getLogger('django')


@role_required('admin')
def admin_dashboard(request):
    today = timezone.localdate()
    three_days_ago = timezone.localtime() - timedelta(days=3)

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
                if created_count:
                    log_action(
                        request, 'slot_created',
                        f'{created_count} appointment date(s) activated '
                        f'from {start} to {end}.',
                        extra_data={'start': start, 'end': end, 'count': created_count}
                    )
                messages.success(request, f'{created_count} new appointment date(s) activated.')
                return redirect('appointment_dates')

        elif action == 'toggle':
            slot_id = request.POST.get('slot_id')
            slot = get_object_or_404(AppointmentSlot, pk=slot_id)
            slot.is_active = not slot.is_active
            slot.save()
            status = 'activated' if slot.is_active else 'deactivated'
            log_action(
                request, 'slot_updated', f'Slot on {slot.date} {status}.',
                extra_data={'slot_id': slot.pk, 'is_active': slot.is_active}
            )
            messages.success(request, f'Slot on {slot.date} {status}.')
            return redirect('appointment_dates')

        elif action == 'delete':
            slot_id = request.POST.get('slot_id')
            slot = get_object_or_404(AppointmentSlot, pk=slot_id)
            if slot.appointments.exists():
                messages.error(request, f'Cannot delete {slot.date} — it has existing appointments.')
            else:
                slot_date = slot.date
                slot.delete()
                log_action(
                    request, 'slot_deleted', f'Slot on {slot_date} deleted.',
                    extra_data={'date': str(slot_date)}
                )
                messages.success(request, f'Slot on {slot_date} deleted.')
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
            if deleted:
                log_action(
                    request, 'slot_deleted', f'{deleted} appointment date(s) bulk deleted.',
                    extra_data={'deleted': deleted, 'skipped': skipped}
                )
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
            if count:
                log_action(
                    request, 'slot_deleted', f'{count} empty appointment slot(s) deleted.',
                    extra_data={'deleted': count}
                )
            messages.success(request, f'{count} empty slot(s) deleted.')
            return redirect('appointment_dates')

        elif action == 'deactivate_all':
            count = AppointmentSlot.objects.filter(is_active=True).update(is_active=False)
            if count:
                log_action(
                    request, 'slot_updated', f'{count} appointment slot(s) deactivated (bulk).',
                    extra_data={'count': count, 'is_active': False}
                )
            messages.success(request, f'{count} slot(s) deactivated.')
            return redirect('appointment_dates')

        elif action == 'activate_all':
            count = AppointmentSlot.objects.filter(is_active=False).update(is_active=True)
            if count:
                log_action(
                    request, 'slot_updated', f'{count} appointment slot(s) activated (bulk).',
                    extra_data={'count': count, 'is_active': True}
                )
            messages.success(request, f'{count} slot(s) activated.')
            return redirect('appointment_dates')

    # Stats for the summary bar. The booked_count annotation is what keeps
    # the template's per-row .total_booked / .is_full / .has_bookings calls
    # from each firing their own COUNT — see
    # AppointmentSlotQuerySet.with_booked_count(). capacity is PER TIME SLOT
    # now, so a day's real capacity is capacity * number of bookable times.
    slots = list(slots.with_booked_count())
    per_day_capacity = len(Appointment.TIME_CHOICES)
    total = len(slots)
    active = sum(1 for s in slots if s.is_active)
    full = sum(
        1 for s in slots
        if s.booked_count >= s.capacity * per_day_capacity
    )
    booked = sum(s.booked_count for s in slots)
    # What "delete all empty" would actually remove, so the confirmation can
    # say the number instead of asking about an unnamed quantity. Counted off
    # the same annotated list as the stats above, so it costs no extra query.
    empty = sum(1 for s in slots if not s.has_bookings())

    return render(request, 'sticker_admin/appointment_dates.html', {
        'slots': slots,
        'total': total,
        'active': active,
        'full': full,
        'booked': booked,
        'empty': empty,
    })


@role_required('admin')
def application_list(request):
    applications = StickerApplication.objects.select_related(
        'applicant', 'appointment', 'appointment__slot'
    ).order_by('-created_at')

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

    paginator = Paginator(applications, 20)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'sticker_admin/application_list.html', {
        'applications': page_obj,
        'page_obj': page_obj,
        'query': query,
        'status_filter': status_filter,
        'status_choices': StickerApplication.STATUS_CHOICES,
    })


@role_required('admin')
def application_detail(request, pk):
    # select_related('applicant') because the template reads
    # application.applicant.username.
    application = get_object_or_404(
        StickerApplication.objects.select_related('applicant'), pk=pk
    )

    # Get appointment if exists
    appointment = getattr(application, 'appointment', None)

    # Get all available slots for manual reassignment. with_booked_count()
    # because the reassignment dropdown renders slot.slots_remaining for
    # every row — without the annotation that's one COUNT per open date,
    # growing linearly with however many dates the admin has opened.
    available_slots = AppointmentSlot.objects.with_booked_count().filter(
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
                    appointment = Appointment.objects.create(
                        application=application,
                        slot=slot,
                        time=time
                    )
                application.status = 'scheduled'
                application.save()
                log_action(
                    request, 'appointment_set',
                    f'Appointment for {application.full_name} set to '
                    f'{slot.date} at {time}.',
                    target_user=application.applicant.username,
                    extra_data={'application_id': pk, 'slot_id': slot.pk, 'time': time}
                )
                transaction.on_commit(
                    lambda: notify_appointment_assigned(application, appointment)
                )
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
        if application.status != 'scheduled':
            messages.error(
                request,
                f'Cannot approve — application status is '
                f'"{application.get_status_display()}", not Scheduled.'
            )
            return redirect('application_detail', pk=pk)

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
        transaction.on_commit(lambda: notify_approved(application))
        messages.success(
            request,
            f'Application for {application.full_name} approved.'
        )
    return redirect('application_detail', pk=pk)


@role_required('admin')
def reject_application(request, pk):
    application = get_object_or_404(StickerApplication, pk=pk)
    if request.method == 'POST':
        if application.status != 'scheduled':
            messages.error(
                request,
                f'Cannot reject — application status is '
                f'"{application.get_status_display()}", not Scheduled.'
            )
            return redirect('application_detail', pk=pk)

        reason = request.POST.get('rejection_reason', '').strip()
        if not reason:
            messages.error(
                request,
                'Please explain why this application is being rejected — '
                'the applicant only sees this reason, so it cannot be blank.'
            )
            return redirect('application_detail', pk=pk)

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
        transaction.on_commit(lambda: notify_rejected(application))
        messages.warning(
            request,
            f'Application for {application.full_name} rejected.'
        )
    return redirect('application_detail', pk=pk)


@role_required('admin')
def sticker_station(request):
    """
    The issuing counter. Shows everyone currently approved and waiting for
    a sticker by default — staff shouldn't have to already know a name to
    find out who's in the queue. Search narrows that same list.
    """
    query = request.GET.get('q', '')

    approved = StickerApplication.objects.filter(
        status='approved'
    ).select_related('appointment', 'appointment__slot')

    if query:
        approved = approved.filter(
            Q(full_name__icontains=query) |
            Q(id_number__icontains=query) |
            Q(plate_number__icontains=query) |
            Q(sticker_id__icontains=query)
        )

    # Longest-waiting first: whoever was approved earliest should be served
    # first, which is also the order people physically queue in.
    approved = approved.order_by('updated_at')

    total_waiting = StickerApplication.objects.filter(status='approved').count()

    paginator = Paginator(approved, 20)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'sticker_admin/sticker_station.html', {
        'query': query,
        'results': page_obj,
        'page_obj': page_obj,
        'total_waiting': total_waiting,
    })


@role_required('admin')
def issue_sticker(request, pk):
    application = get_object_or_404(StickerApplication, pk=pk)

    if request.method == 'POST':
        rfid_uid = request.POST.get('rfid_uid', '').strip()

        if not rfid_uid:
            messages.error(request, 'Please enter or scan an RFID UID.')
            return redirect('issue_sticker', pk=pk)

        try:
            with transaction.atomic():
                # Re-fetch and lock the row so two staff terminals can't both
                # pass the checks below for the same application at once.
                application = StickerApplication.objects.select_for_update().get(pk=pk)

                if application.status != 'approved':
                    messages.error(
                        request,
                        f'Cannot issue a sticker — application status is '
                        f'"{application.get_status_display()}", not Approved.'
                    )
                    return redirect('application_detail', pk=pk)

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
                sticker_id = f'PalawanSU-{uuid.uuid4().hex[:8].upper()}'

                # Issue the sticker
                application.rfid_uid = rfid_uid
                application.sticker_id = sticker_id
                application.status = 'issued'
                application.issued_at = timezone.now()
                application.save()

                # This tag is spoken for now. Inside the transaction so it
                # can't be retired by an issue that then rolls back, and
                # so the issuing station stops offering a UID the moment
                # it has actually been bound to an application.
                PendingRFID.claim(rfid_uid)
        except IntegrityError:
            messages.error(
                request,
                f'RFID tag {rfid_uid} was just claimed by another request. '
                'Please scan a different tag.'
            )
            return redirect('issue_sticker', pk=pk)

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
        transaction.on_commit(lambda: notify_sticker_issued(application))
        messages.success(
            request,
            f'Sticker issued! Sticker ID: {sticker_id}, RFID: {rfid_uid}'
        )
        return redirect('application_detail', pk=application.pk)

    return render(request, 'sticker_admin/issue_sticker.html', {
        'application': application,
    })


# NOTE: the walk-in "Quick Register" flow (spec 5.5) was removed — it
# created an applicant account and issued a sticker in one step, bypassing
# document upload, the appointment queue, and admin review entirely. Every
# sticker now goes through the normal apply → inspect → approve → issue
# path, so there is exactly one way a vehicle can end up registered.
# Records created by the old flow are ordinary issued applications and are
# unaffected.