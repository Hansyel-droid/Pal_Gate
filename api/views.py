import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from gate_guard.models import RFIDTag, GateLog
from django.views.decorators.http import require_POST

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