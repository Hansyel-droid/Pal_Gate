from django.db import models
from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.utils import timezone

class Vehicle(models.Model):
    """Vehicle information linked to a sticker application."""
    plate_number = models.CharField(max_length=20, unique=True)
    model = models.CharField(max_length=100)
    color = models.CharField(max_length=50)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='vehicles')
    is_owner = models.BooleanField(default=True, help_text="Is the vehicle registered in applicant's name?")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.plate_number} - {self.model}"


class StickerApplication(models.Model):
    """Main sticker application record."""
    STATUS_CHOICES = (
        ('pending', 'Pending Review'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('issued', 'Sticker Issued'),
        ('expired', 'Expired'),
    )

    applicant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='applications')
    vehicle = models.OneToOneField(Vehicle, on_delete=models.CASCADE, related_name='application')
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
        return self.status == 'approved' and self.expiry_date >= timezone.now().date()


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