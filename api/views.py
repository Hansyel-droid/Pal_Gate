import json
from datetime import timedelta
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from gate_guard.models import RFIDTag, GateLog, PendingRFIDRegistration
from django.views.decorators.http import require_POST
from gate_guard.models import SystemConfig

@csrf_exempt
@require_POST
def scan(request):
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'allowed': False, 'reason': 'Invalid JSON'}, status=400)

    required_fields = ['timestamp', 'entrypoint', 'driver_name', 'vehicle_model', 'vehicle_color', 'plate_number', 'rfid_tag']
    if not all(field in data for field in required_fields):
        return JsonResponse({'allowed': False, 'reason': 'Missing fields'}, status=400)

    rfid_tag = data['rfid_tag']
    try:
        tag = RFIDTag.objects.select_related('sticker_application', 'sticker_application__vehicle').get(tag_id=rfid_tag, is_active=True)
    except RFIDTag.DoesNotExist:
        GateLog.objects.create(
            rfid_tag=None,
            action='denied',
            gate=data['entrypoint'],
            timestamp=data['timestamp'],
            driver_name=data['driver_name'],
            vehicle_model=data['vehicle_model'],
            vehicle_color=data['vehicle_color'],
            plate_number=data['plate_number'],
            reason_denied='RFID not found or inactive'
        )
        return JsonResponse({'allowed': False, 'reason': 'RFID not recognized'})

    application = tag.sticker_application
    if not application.is_valid():
        GateLog.objects.create(
            rfid_tag=tag,
            action='denied',
            gate=data['entrypoint'],
            timestamp=data['timestamp'],
            driver_name=data['driver_name'],
            vehicle_model=data['vehicle_model'],
            vehicle_color=data['vehicle_color'],
            plate_number=data['plate_number'],
            reason_denied='Sticker expired or not approved'
        )
        return JsonResponse({'allowed': False, 'reason': 'Sticker invalid'})

    # Determine action based on last log? For simplicity, we'll treat every scan as entry/exit toggle
    # You can implement more sophisticated logic here.
    last_log = GateLog.objects.filter(rfid_tag=tag).order_by('-timestamp').first()
    action = 'exit' if last_log and last_log.action == 'entry' else 'entry'

    # If the ESP32 sent empty strings, use the database values
    vehicle = tag.sticker_application.vehicle
    if not data.get('driver_name'):
        data['driver_name'] = tag.sticker_application.applicant.get_full_name()
    if not data.get('plate_number'):
        data['plate_number'] = vehicle.plate_number
    if not data.get('vehicle_model'):
        data['vehicle_model'] = vehicle.model
    if not data.get('vehicle_color'):
        data['vehicle_color'] = vehicle.color

    GateLog.objects.create(
        rfid_tag=tag,
        action=action,
        gate=data['entrypoint'],
        timestamp=data['timestamp'],
        driver_name=data['driver_name'],
        vehicle_model=data['vehicle_model'],
        vehicle_color=data['vehicle_color'],
        plate_number=data['plate_number']
    )

    # Update last used timestamp
    tag.last_used = timezone.now()
    tag.save(update_fields=['last_used'])

    return JsonResponse({'allowed': True})


@csrf_exempt
@require_POST
def register_uid(request):
    try:
        data = json.loads(request.body)
        uid = data.get('rfid_uid', '').strip()
    except (json.JSONDecodeError, KeyError):
        return JsonResponse({'error': 'Invalid payload'}, status=400)

    if not uid:
        return JsonResponse({'error': 'Empty UID'}, status=400)

    # Save / update the UID (keep only the latest per UID, or delete old)
    PendingRFIDRegistration.objects.filter(rfid_uid=uid).delete()  # optional refresh
    PendingRFIDRegistration.objects.create(rfid_uid=uid)

    # Optionally, delete really old entries to keep the table small
    PendingRFIDRegistration.objects.filter(
        created_at__lt=timezone.now() - timedelta(hours=1)
    ).delete()

    return JsonResponse({'status': 'UID stored', 'uid': uid})


def admin_status(request):
    config = SystemConfig.load()
    return JsonResponse({'admin_mode': config.admin_mode})