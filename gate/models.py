from django.db import models
from applications.models import StickerApplication


class GateLog(models.Model):
    """
    Every time an RFID tag is scanned at the gate,
    a GateLog entry is created.
    """
    ACTION_CHOICES = [
        ('entry', 'Entry'),
        ('exit', 'Exit'),
        ('denied', 'Denied'),
    ]

    # The raw RFID tag UID that was scanned
    rfid_uid = models.CharField(max_length=100)

    # If the UID matches a sticker, link to that application
    application = models.ForeignKey(
        StickerApplication,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='gate_logs'
    )

    action = models.CharField(max_length=10, choices=ACTION_CHOICES)
    gate_location = models.CharField(max_length=50, default='Main Gate')
    denial_reason = models.CharField(max_length=200, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.rfid_uid} – {self.action} at {self.timestamp:%Y-%m-%d %H:%M}"

    class Meta:
        ordering = ['-timestamp']


class PendingRFID(models.Model):
    """
    When a new RFID tag is scanned at the registration scanner,
    the UID is stored here temporarily so the sticker station
    can pick it up and auto-fill the field.
    """
    uid = models.CharField(max_length=100)
    registered_at = models.DateTimeField(auto_now_add=True)
    claimed = models.BooleanField(default=False)

    def __str__(self):
        return f"Pending UID: {self.uid} (claimed: {self.claimed})"

    class Meta:
        ordering = ['-registered_at']


class AuditLog(models.Model):
    """
    Records every significant administrative action in the system.
    Immutable — never edited or deleted.
    """
    ACTION_TYPES = [
        # Auth events
        ('login', 'User Login'),
        ('logout', 'User Logout'),
        ('login_failed', 'Failed Login Attempt'),
        ('lockout', 'Account Locked Out'),
        # Application events
        ('app_submitted', 'Application Submitted'),
        ('app_approved', 'Application Approved'),
        ('app_rejected', 'Application Rejected'),
        ('sticker_issued', 'Sticker Issued'),
        ('app_expired', 'Sticker Expired'),
        ('appointment_set', 'Appointment Set'),
        # Gate events
        ('gate_entry', 'Gate Entry'),
        ('gate_exit', 'Gate Exit'),
        ('gate_denied', 'Gate Access Denied'),
        # Admin events
        ('window_opened', 'Registration Window Opened'),
        ('window_closed', 'Registration Window Closed'),
        ('slot_created', 'Appointment Slot Created'),
    ]

    actor = models.ForeignKey(
        'accounts.User',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='audit_logs'
    )
    action = models.CharField(max_length=30, choices=ACTION_TYPES)
    description = models.TextField()
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    target_user = models.CharField(max_length=150, blank=True)
    extra_data = models.JSONField(default=dict, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.timestamp:%Y-%m-%d %H:%M} — {self.action} by {self.actor}'

    class Meta:
        ordering = ['-timestamp']