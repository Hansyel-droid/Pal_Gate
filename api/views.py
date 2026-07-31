import json
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from django_ratelimit.decorators import ratelimit
from .authentication import require_api_key

from accounts.mixins import role_required
from applications.models import StickerApplication
from gate.audit import log_action
from gate.models import GateLog, PendingRFID


@csrf_exempt
@require_api_key
@ratelimit(key='ip', rate='60/m', method='POST', block=True)
@require_http_methods(['POST'])
def scan_tag(request):
    """
    Called by the ESP32 gate scanner when a tag is scanned.

    Expected JSON body:
    {
        "uid": "AB12CD34",
        "gate": "Main Gate"
    }

    Returns:
    {
        "allowed": true/false,
        "action": "entry"/"exit"/"denied",
        "name": "Juan dela Cruz",
        "plate": "ABC 123",
        "sticker_id": "PalSU-XXXX",
        "reason": "..."   (only if denied)
    }
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse(
            {'error': 'Invalid JSON body.'},
            status=400
        )

    uid = data.get('uid', '').strip()
    gate = data.get('gate', 'Main Gate').strip()

    if not uid:
        return JsonResponse(
            {'error': 'UID is required.'},
            status=400
        )

    # Look up the RFID tag in our database
    try:
        application = StickerApplication.objects.get(rfid_uid=uid)
    except StickerApplication.DoesNotExist:
        # Tag not registered — deny access
        GateLog.objects.create(
            rfid_uid=uid,
            application=None,
            action='denied',
            gate_location=gate,
            denial_reason='RFID tag not registered in system.'
        )
        log_action(
            request, 'gate_denied',
            f'Denied at {gate}: RFID {uid} not registered.',
        )
        return JsonResponse({
            'allowed': False,
            'action': 'denied',
            'reason': 'RFID tag not registered.',
        })

    # Check if the sticker is still valid (must be 'issued' status)
    if application.status != 'issued':
        GateLog.objects.create(
            rfid_uid=uid,
            application=application,
            action='denied',
            gate_location=gate,
            denial_reason=f'Sticker status is "{application.get_status_display()}", not issued.'
        )
        log_action(
            request, 'gate_denied',
            f'Denied at {gate}: {application.full_name} '
            f'(Plate: {application.plate_number}) — status is '
            f'"{application.get_status_display()}", not issued.',
            extra_data={'application_id': application.pk},
        )
        return JsonResponse({
            'allowed': False,
            'action': 'denied',
            'name': application.full_name,
            'plate': application.plate_number,
            'reason': f'Sticker not active. Status: {application.get_status_display()}',
        })

    # Determine entry or exit based on the last gate log for this vehicle
    last_log = GateLog.objects.filter(
        application=application
    ).order_by('-timestamp').first()

    if last_log and last_log.action == 'entry':
        # Last action was entry, so this is an exit
        action = 'exit'
    else:
        # Last action was exit (or no logs at all), so this is an entry
        action = 'entry'

    # Create the gate log
    GateLog.objects.create(
        rfid_uid=uid,
        application=application,
        action=action,
        gate_location=gate,
    )
    log_action(
        request, f'gate_{action}',
        f'{application.full_name} (Plate: {application.plate_number}) '
        f'{action} at {gate}.',
        extra_data={'application_id': application.pk},
    )

    return JsonResponse({
        'allowed': True,
        'action': action,
        'name': application.full_name,
        'plate': application.plate_number,
        'sticker_id': application.sticker_id,
    })


@csrf_exempt
@require_api_key
@ratelimit(key='ip', rate='30/m', method='POST', block=True)
@require_http_methods(['POST'])
def register_uid(request):
    """
    Called by the ESP32 registration scanner when a new tag is presented.
    Stores the UID temporarily so the sticker station can pick it up.

    Expected JSON body:
    {
        "uid": "AB12CD34"
    }

    Returns:
    {
        "success": true,
        "uid": "AB12CD34"
    }
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse(
            {'error': 'Invalid JSON body.'},
            status=400
        )

    uid = data.get('uid', '').strip()

    if not uid:
        return JsonResponse(
            {'error': 'UID is required.'},
            status=400
        )

    # Mark all previous pending UIDs as claimed
    # so only the latest one shows up
    PendingRFID.objects.filter(claimed=False).update(claimed=True)

    # Store the new UID
    PendingRFID.objects.create(uid=uid, claimed=False)

    return JsonResponse({
        'success': True,
        'uid': uid,
    })


@login_required
@role_required('admin')
@ratelimit(key='ip', rate='60/m', method='GET', block=True)
@require_http_methods(['GET'])
def latest_pending_uid(request):
    """
    Called by the sticker station / issue-sticker pages every 3 seconds via
    JavaScript, from an authenticated admin session (not ESP32 hardware) —
    so this is protected by login + role instead of the API key.
    Returns the most recently scanned unclaimed UID.

    Returns:
    {
        "uid": "AB12CD34"   (or null if none pending)
    }
    """
    pending = PendingRFID.objects.filter(
        claimed=False
    ).order_by('-registered_at').first()

    if pending:
        return JsonResponse({'uid': pending.uid})
    else:
        return JsonResponse({'uid': None})


@require_api_key
@ratelimit(key='ip', rate='60/m', method='GET', block=True)
@require_http_methods(['GET'])
def gate_status(request):
    """
    Called by the ESP32 hardware. Returns the current gate status.
    Kept simple for now — always returns open: true.
    Can be extended later to support manual gate control.

    Returns:
    {
        "open": true
    }
    """
    return JsonResponse({'open': True})