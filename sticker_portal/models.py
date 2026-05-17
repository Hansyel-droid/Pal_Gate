from django.db import models
from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.utils import timezone
from datetime import date, timedelta   # <-- added


class Vehicle(models.Model):
    """Vehicle information linked to a sticker application."""
    TYPE_CHOICES = (
        ('two_wheels', 'Two Wheels'),
        ('four_wheels', 'Four Wheels'),
        ('other', 'Other (specify)'),
    )
    COLOR_CHOICES = (
        ('red', 'Red'),
        ('blue', 'Blue'),
        ('green', 'Green'),
        ('yellow', 'Yellow'),
        ('black', 'Black'),
        ('white', 'White'),
        ('silver', 'Silver'),
        ('gray', 'Gray'),
        ('other', 'Other (specify)'),
    )

    plate_number = models.CharField(max_length=20, unique=True)
    type_of_vehicle = models.CharField(max_length=20, choices=TYPE_CHOICES, default='four_wheels')
    color = models.CharField(max_length=20, choices=COLOR_CHOICES, default='silver')
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='vehicles')
    is_owner = models.BooleanField(default=True, help_text="Is the vehicle registered in applicant's name?")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.plate_number} - {self.get_type_of_vehicle_display()}"


class StickerApplication(models.Model):
    """Main sticker application record."""
    STATUS_CHOICES = (
        ('draft', 'Draft'),
        ('pending', 'Scheduled'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('issued', 'Sticker Issued'),
        ('expired', 'Expired'),
    )

    full_name = models.CharField(max_length=150, blank=True)
    college_department = models.CharField(max_length=100, blank=True)
    student_id = models.CharField(max_length=50, blank=True)
    scheduled_datetime = models.DateTimeField(null=True, blank=True)
    applicant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='applications')
    vehicle = models.OneToOneField(Vehicle, on_delete=models.CASCADE, related_name='application', null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    expiry_date = models.DateField()
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_applications'
    )
    rejection_reason = models.TextField(blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-submitted_at']

    def __str__(self):
        return f"{self.applicant.get_full_name()} - {self.vehicle.plate_number}"

    def is_valid(self):
        """Check if the sticker is currently valid."""
        return self.status in ['approved', 'issued'] and self.expiry_date >= timezone.now().date()


class Document(models.Model):
    """Uploaded documents for a sticker application."""
    DOCUMENT_TYPES = (
        ('or_cr', 'OR/CR'),
        ('drivers_license', "Driver's License"),
        ('cor', 'Certificate of Registration'),
        ('auth_letter', 'Authorization Letter'),
    )

    application = models.ForeignKey(StickerApplication, on_delete=models.CASCADE, related_name='documents')
    document_type = models.CharField(max_length=20, choices=DOCUMENT_TYPES)
    file = models.FileField(
        upload_to='documents/%Y/%m/',
        validators=[FileExtensionValidator(allowed_extensions=['pdf', 'jpg', 'jpeg', 'png'])]
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.get_document_type_display()} - {self.application.id}"


class AvailableDate(models.Model):
    date = models.DateField(unique=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.date} {'Active' if self.is_active else 'Inactive'}"


class RegistrationPeriod(models.Model):
    """Stores the registration window dates. Only one row is used (singleton)."""
    start_date = models.DateField()
    end_date = models.DateField()

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, created = cls.objects.get_or_create(
            pk=1,
            defaults={
                'start_date': date.today(),
                'end_date': date.today() + timedelta(days=30)
            }
        )
        return obj

    @classmethod
    def is_open(cls):
        """Return True if today is within the registration window."""
        period = cls.load()
        today = date.today()
        return period.start_date <= today <= period.end_date