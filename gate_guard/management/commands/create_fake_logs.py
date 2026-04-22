from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
import random
from accounts.models import User
from sticker_portal.models import Vehicle, StickerApplication
from gate_guard.models import RFIDTag, GateLog

class Command(BaseCommand):
    help = 'Create fake gate logs for testing'

    def add_arguments(self, parser):
        parser.add_argument('--count', type=int, default=50, help='Number of logs to create')

    def handle(self, *args, **options):
        count = options['count']

        # Ensure we have a valid RFID tag
        applicant, _ = User.objects.get_or_create(
            username='teststudent',
            defaults={
                'user_type': 'applicant',
                'first_name': 'John',
                'last_name': 'Doe',
                'student_id': '2021-00123',
                'college_department': 'College of Engineering',
                'classification': 'student',
                'contact_number': '09123456789',
            }
        )
        applicant.set_password('testpass')
        applicant.save()

        vehicle, _ = Vehicle.objects.get_or_create(
            plate_number='ABC-1234',
            defaults={
                'model': 'Toyota Vios',
                'color': 'Silver',
                'owner': applicant,
                'is_owner': True,
            }
        )

        app, _ = StickerApplication.objects.get_or_create(
            applicant=applicant,
            vehicle=vehicle,
            defaults={
                'status': 'approved',
                'expiry_date': timezone.now().date() + timedelta(days=365),
                'approved_at': timezone.now(),
            }
        )

        rfid, _ = RFIDTag.objects.get_or_create(
            tag_id='RFID-ABC-1234',
            defaults={
                'sticker_application': app,
                'is_active': True,
            }
        )

        gates = ['main_gate', 'back_gate']
        actions = ['entry', 'exit', 'denied']
        models = ['Toyota Vios', 'Honda Civic', 'Ford Ranger', 'Mitsubishi Mirage', 'Nissan Navara']
        colors = ['Silver', 'White', 'Black', 'Red', 'Blue']
        names = ['Juan Dela Cruz', 'Maria Santos', 'Pedro Penduko', 'Ana Reyes', 'Jose Rizal']

        for i in range(count):
            days_ago = random.randint(0, 7)
            hours_ago = random.randint(0, 23)
            minutes_ago = random.randint(0, 59)
            timestamp = timezone.now() - timedelta(days=days_ago, hours=hours_ago, minutes=minutes_ago)

            if random.random() > 0.3:
                tag = rfid
                action = random.choice(['entry', 'exit'])
                plate = vehicle.plate_number
                model = vehicle.model
                color = vehicle.color
                driver = applicant.get_full_name()
                reason = ''
            else:
                tag = None
                action = 'denied'
                plate = f'UNK-{random.randint(100, 999)}'
                model = random.choice(models)
                color = random.choice(colors)
                driver = random.choice(names)
                reason = random.choice(['RFID not recognized', 'Sticker expired', 'No sticker found'])

            GateLog.objects.create(
                rfid_tag=tag,
                action=action,
                gate=random.choice(gates),
                timestamp=timestamp,
                driver_name=driver,
                vehicle_model=model,
                vehicle_color=color,
                plate_number=plate,
                reason_denied=reason,
            )

        self.stdout.write(self.style.SUCCESS(f'✅ Created {count} fake gate logs'))