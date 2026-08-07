from datetime import timedelta

from django.db import models
from django.utils import timezone

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
        indexes = [
            # This is the table that grows fastest — one row per scan,
            # forever. The live gate view and log browser both sort by
            # -timestamp, and the scan endpoint looks up the most recent
            # entry per vehicle to decide entry vs exit.
            models.Index(fields=['-timestamp'], name='gatelog_timestamp_idx'),
            models.Index(fields=['application', '-timestamp'],
                         name='gatelog_app_timestamp_idx'),
        ]


class PendingRFID(models.Model):
    """
    When a new RFID tag is scanned at the registration scanner,
    the UID is stored here temporarily so the sticker station
    can pick it up and auto-fill the field.
    """

    # How long a scanned tag stays offerable to the issuing station.
    #
    # There was no window at all: the issue page auto-filled whatever the
    # newest unclaimed row was, however old, and nothing ever marked a row
    # claimed once a sticker had actually been issued from it. So staff
    # opening the page the next morning were silently handed yesterday's
    # UID, and anyone able to reach /api/register-uid/ could park a UID of
    # their choosing and wait for the next person to issue a sticker — the
    # tag would end up bound to a legitimate application, which is gate
    # access. Long enough to walk from the registration scanner to the
    # counter, short enough that a stale or planted scan has expired.
    OFFER_TTL = timedelta(minutes=5)

    uid = models.CharField(max_length=100)
    registered_at = models.DateTimeField(auto_now_add=True)
    claimed = models.BooleanField(default=False)

    @classmethod
    def latest_offerable(cls):
        """The newest scan still inside OFFER_TTL, or None."""
        return cls.objects.filter(
            claimed=False,
            registered_at__gte=timezone.now() - cls.OFFER_TTL,
        ).order_by('-registered_at').first()

    @classmethod
    def claim(cls, uid):
        """
        Retire every outstanding row for `uid`, once a sticker has really
        been issued from it. Without this the same UID kept being offered
        after it had already been bound to an application.
        """
        return cls.objects.filter(uid=uid, claimed=False).update(claimed=True)

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
        ('register_started', 'Registration Started'),
        ('email_verified', 'Email Verified'),
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
        ('slot_updated', 'Appointment Slot Updated'),
        ('slot_deleted', 'Appointment Slot Deleted'),
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
        indexes = [
            # Append-only and never pruned, so it only ever gets bigger.
            models.Index(fields=['-timestamp'], name='auditlog_timestamp_idx'),
        ]