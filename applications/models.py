from datetime import timedelta
from django.db import models
from django.db.models import Q
from accounts.models import User
from .utils import upload_or_cr, upload_license, upload_cor, upload_auth

# A sticker is valid for one academic year from the day it was handed out.
# Mirrors the constant in applications/management/commands/expire_stickers.py.
STICKER_VALIDITY_DAYS = 365


class RegistrationWindow(models.Model):
    """
    The administrator sets a start and end date.
    Applicants can only submit applications within this window.
    """
    start_date = models.DateField()
    end_date = models.DateField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Registration Window: {self.start_date} to {self.end_date}"

    class Meta:
        ordering = ['-created_at']


class StickerApplication(models.Model):
    """
    The main application model. One per vehicle per applicant.
    """
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('scheduled', 'Scheduled'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('issued', 'Sticker Issued'),
        ('expired', 'Expired'),
    ]

    VEHICLE_TYPE_CHOICES = [
        ('two_wheels', 'Two Wheels'),
        ('four_wheels', 'Four Wheels'),
        ('other', 'Other'),
    ]

    COLOR_CHOICES = [
        ('red', 'Red'),
        ('blue', 'Blue'),
        ('green', 'Green'),
        ('yellow', 'Yellow'),
        ('orange', 'Orange'),
        ('white', 'White'),
        ('black', 'Black'),
        ('silver', 'Silver'),
        ('gray', 'Gray'),
        ('brown', 'Brown'),
        ('other', 'Other'),
    ]

    CLASSIFICATION_CHOICES = [
        ('student', 'Student'),
        ('faculty', 'Faculty'),
        ('parent', 'Parent'),
    ]

    # Statuses that no longer "hold" a plate number — a rejected or expired
    # application shouldn't permanently block that plate from being reused.
    INACTIVE_STATUSES = ['rejected', 'expired']

    # Who submitted this application
    applicant = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='applications'
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='draft'
    )

    # --- Step 1: Personal Details ---
    full_name = models.CharField(max_length=100)
    college_department = models.CharField(max_length=100)
    id_number = models.CharField(max_length=50)
    classification = models.CharField(
        max_length=20,
        choices=CLASSIFICATION_CHOICES
    )

    # --- Step 2: Vehicle Details ---
    # Not unique=True at the field level — uniqueness is only enforced among
    # "active" applications (see Meta.constraints below), so a rejected or
    # expired application doesn't permanently squat its plate number.
    plate_number = models.CharField(max_length=20)
    vehicle_type = models.CharField(
        max_length=20,
        choices=VEHICLE_TYPE_CHOICES
    )
    vehicle_color = models.CharField(
        max_length=30,
        choices=COLOR_CHOICES
    )
    is_owner = models.BooleanField(default=True)

    # --- Step 2: Document Uploads ---
    # OR/CR is always required
    or_cr = models.FileField(upload_to=upload_or_cr)
    # Driver's license is always required
    drivers_license = models.FileField(upload_to=upload_license)
    # COR is required for students only
    cor = models.FileField(
        upload_to=upload_cor,
        blank=True,
        null=True
    )
    # Authorization letter required if applicant is not the owner
    authorization_letter = models.FileField(
        upload_to=upload_auth,
        blank=True,
        null=True
    )

    # --- Sticker & RFID ---
    # These are filled in when the sticker is issued
    rfid_uid = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        unique=True
    )
    sticker_id = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        unique=True
    )

    # --- Admin actions ---
    rejection_reason = models.TextField(blank=True)

    # --- Timestamps ---
    submitted_at = models.DateTimeField(null=True, blank=True)
    # Set when status becomes 'issued' — the 365-day validity period is
    # measured from here, not from submission (an applicant may wait weeks
    # between submitting and actually picking up the sticker).
    issued_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def expiry_date(self):
        """The date the sticker stops being valid, or None if never issued."""
        if not self.issued_at:
            return None
        return self.issued_at + timedelta(days=STICKER_VALIDITY_DAYS)

    def __str__(self):
        return f"{self.full_name} – {self.plate_number} ({self.get_status_display()})"

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['plate_number'],
                condition=~Q(status__in=['rejected', 'expired']),
                name='unique_active_plate_number',
            ),
        ]
        indexes = [
            # Almost every admin screen filters on status ('approved' at the
            # sticker station, 'scheduled' on the dashboard, etc.), and the
            # default ordering is -created_at, so this covers both the filter
            # and the sort in one index instead of scanning every row.
            models.Index(fields=['status', '-created_at'],
                         name='app_status_created_idx'),
            # The applicant's own list, which is their most-visited page.
            models.Index(fields=['applicant', '-created_at'],
                         name='app_applicant_created_idx'),
        ]