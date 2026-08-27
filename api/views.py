import json
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from django_ratelimit.decorators import ratelimit
from .authentication import require_api_key
from .rfid_auth import derive_tag_credentials_hex

from accounts.mixins import role_required
from applications.models import StickerApplication
from gate.audit import log_action
from gate.models import GateLog, PendingRFID

# Mirror the model fields these values land in (GateLog.rfid_uid,
# GateLog.gate_location, PendingRFID.uid). Nothing else was holding them to
# those lengths: SQLite doesn't enforce varchar length and
# Model.objects.create() skips full_clean(), so an oversized value was
# stored verbatim. gate_location matters most — the live dashboard builds
# its gate scope control from the distinct values actually logged, so
# anything written here is echoed back onto that screen.
MAX_UID_LENGTH = 100
MAX_GATE_LENGTH = 50


def bounded_text(data, key, max_length, default=''):
    """
    Pull one string field out of a decoded JSON body.

    Raises ValueError with a message that is safe to hand back to the
    caller. The isinstance check is not redundant — a JSON body of
    {"uid": 12345} used to reach .strip() on an int and 500.
    """
    value = data.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f'{key} must be a string.')
    value = value.strip()
    if len(value) > max_length:
        raise ValueError(f'{key} must be at most {max_length} characters.')
    return value


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
        "sticker_id": "PalawanSU-XXXX",
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

    try:
        uid = bounded_text(data, 'uid', MAX_UID_LENGTH)
        gate = bounded_text(data, 'gate', MAX_GATE_LENGTH, default='Main Gate')
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)

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
    Stores the UID temporarily so the sticker station can pick it up, and
    hands back the PWD_AUTH credentials the device writes to the tag.

    Expected JSON body:
    {
        "uid": "AB12CD34"
    }

    Returns:
    {
        "success": true,
        "uid": "AB12CD34",
        "pwd": "1A2B3C4D",     (4 bytes, hex)
        "pack": "5E6F"         (2 bytes, hex)
    }

    The pwd/pack are derived from the UID under settings.RFID_MASTER_KEY —
    they are not stored anywhere, because they never need to be: anything
    holding the master key can recompute them from the UID at any time.
    That is the point of deriving rather than generating them. See
    api/rfid_auth.py for the derivation and for what this does and does not
    protect against.

    They are returned in the same response as the registration so the device
    can write them to the tag while it is still in the RF field — a second
    round trip would mean a second tap, and a tag that walked away between
    the two would be registered but left unprotected.

    This endpoint is the only place these credentials are exposed, and it
    sits behind require_api_key. Note what that means: anyone holding a gate
    device's API key can derive the password for any UID they can name. That
    is inherent — a reader has to be able to authenticate tags — but it is
    the reason API keys are per-device and revocable while the master key
    never leaves the server.
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse(
            {'error': 'Invalid JSON body.'},
            status=400
        )

    try:
        uid = bounded_text(data, 'uid', MAX_UID_LENGTH)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)

    if not uid:
        return JsonResponse(
            {'error': 'UID is required.'},
            status=400
        )

    # Derived before the UID is stored: if the derivation is going to fail
    # (a blanked master key), fail without leaving a pending registration
    # behind that the sticker station would happily offer up for a tag the
    # device was never told how to lock.
    try:
        credentials = derive_tag_credentials_hex(uid)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=500)

    # Mark all previous pending UIDs as claimed
    # so only the latest one shows up
    PendingRFID.objects.filter(claimed=False).update(claimed=True)

    # Store the new UID
    PendingRFID.objects.create(uid=uid, claimed=False)

    return JsonResponse({
        'success': True,
        'uid': uid,
        'pwd': credentials['pwd'],
        'pack': credentials['pack'],
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

    Only scans from the last PendingRFID.OFFER_TTL are offered — see the
    comment on that constant for why a scan going stale matters.

    Returns:
    {
        "uid": "AB12CD34"   (or null if none pending)
    }
    """
    pending = PendingRFID.latest_offerable()

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