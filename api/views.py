import json
from datetime import timedelta
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from gate_guard.models import RFIDTag, GateLog, PendingRFIDRegistration, SystemConfig
from django.views.decorators.http import require_POST


@csrf_exempt
@require_POST
def scan(request):
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'allowed': False, 'reason': 'Invalid JSON'}, status=400)

    required_fields = ['entrypoint', 'rfid_tag']
    if not all(field in data for field in required_fields):
        return JsonResponse({'allowed': False, 'reason': 'Missing fields'}, status=400)

    timestamp_str = data.get('timestamp', '')
    if not timestamp_str:
        timestamp = timezone.now()
    else:
        try:
            timestamp = timezone.datetime.fromisoformat(timestamp_str)
        except (ValueError, TypeError):
            timestamp = timezone.now()

    rfid_tag = data['rfid_tag']
    try:
        tag = RFIDTag.objects.select_related(
            'sticker_application', 'sticker_application__vehicle'
        ).get(tag_id=rfid_tag, is_active=True)
    except RFIDTag.DoesNotExist:
        denied_log = GateLog.objects.create(
            rfid_tag=None,
            action='denied',
            gate=data['entrypoint'],
            timestamp=timestamp,
            driver_name=data.get('driver_name', ''),
            vehicle_model=data.get('vehicle_model', ''),
            vehicle_color=data.get('vehicle_color', ''),
            plate_number=data.get('plate_number', ''),
            reason_denied='RFID not found or inactive'
        )
        return JsonResponse({
            'allowed': False,
            'reason': 'RFID not recognized',
            'log_id': denied_log.id
        })

    application = tag.sticker_application
    if not application.is_valid():
        denied_log = GateLog.objects.create(
            rfid_tag=tag,
            action='denied',
            gate=data['entrypoint'],
            timestamp=timestamp,
            driver_name=data.get('driver_name', ''),
            vehicle_model=data.get('vehicle_model', ''),
            vehicle_color=data.get('vehicle_color', ''),
            plate_number=data.get('plate_number', ''),
            reason_denied='Sticker expired or not approved'
        )
        return JsonResponse({
            'allowed': False,
            'reason': 'Sticker invalid',
            'log_id': denied_log.id
        })

    # Enrich empty fields from database
    if tag.sticker_application:
        vehicle = tag.sticker_application.vehicle
        if not data.get('driver_name'):
            data['driver_name'] = tag.sticker_application.applicant.get_full_name()
        if not data.get('plate_number'):
            data['plate_number'] = vehicle.plate_number
        if not data.get('vehicle_model'):
            data['vehicle_model'] = vehicle.get_type_of_vehicle_display()  # ✅ fixed
        if not data.get('vehicle_color'):
            data['vehicle_color'] = vehicle.get_color_display()              # ✅ consistent

    last_log = GateLog.objects.filter(rfid_tag=tag).order_by('-timestamp').first()
    action = 'exit' if last_log and last_log.action == 'entry' else 'entry'

    entry_log = GateLog.objects.create(
        rfid_tag=tag,
        action=action,
        gate=data['entrypoint'],
        timestamp=timestamp,
        driver_name=data.get('driver_name', ''),
        vehicle_model=data.get('vehicle_model', ''),
        vehicle_color=data.get('vehicle_color', ''),
        plate_number=data.get('plate_number', ''),
    )

    tag.last_used = timezone.now()
    tag.save(update_fields=['last_used'])

    return JsonResponse({
        'allowed': True,
        'action': action,
        'log_id': entry_log.id
    })


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

    PendingRFIDRegistration.objects.filter(rfid_uid=uid).delete()
    PendingRFIDRegistration.objects.create(rfid_uid=uid)

    PendingRFIDRegistration.objects.filter(
        created_at__lt=timezone.now() - timedelta(hours=1)
    ).delete()

    return JsonResponse({'status': 'UID stored', 'uid': uid})


def admin_status(request):
    config = SystemConfig.load()
    return JsonResponse({'admin_mode': config.admin_mode})


def gate_status(request):
    config = SystemConfig.load()
    return JsonResponse({'gate_open': config.gate_open})


@csrf_exempt
@require_POST
def upload_photo(request):
    log_id = request.POST.get('log_id')
    image = request.FILES.get('image')
    if not log_id or not image:
        return JsonResponse({'error': 'Missing log_id or image'}, status=400)

    try:
        log = GateLog.objects.get(id=log_id)
    except GateLog.DoesNotExist:
        return JsonResponse({'error': 'Invalid log_id'}, status=404)

    GatePhoto.objects.create(gate_log=log, image=image)
    return JsonResponse({'status': 'Photo uploaded'})

from gate_guard.models import PendingRFIDRegistration

def get_latest_pending_uid(request):
    last_pending = PendingRFIDRegistration.objects.order_by('-created_at').first()
    if last_pending:
        return JsonResponse({'uid': last_pending.rfid_uid})
    return JsonResponse({'uid': ''})

def hourly_traffic_data(request):
    from gate_guard.views import get_hourly_traffic_data
    data = get_hourly_traffic_data()
    return JsonResponse(data)