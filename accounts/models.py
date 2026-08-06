from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class User(AbstractUser):
    """
    This is our custom User model. We extend Django's built-in user
    so we can add extra fields like role, college, and ID number.
    """

    ROLE_CHOICES = [
        ('applicant', 'Applicant'),
        ('admin', 'Sticker Administrator'),
        ('security', 'Security Officer'),
    ]

    CLASSIFICATION_CHOICES = [
        ('student', 'Student'),
        ('faculty', 'Faculty'),
        ('parent', 'Parent'),
    ]

    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default='applicant'
    )
    college_department = models.CharField(max_length=100, blank=True)
    id_number = models.CharField(max_length=50, blank=True)
    classification = models.CharField(
        max_length=20,
        choices=CLASSIFICATION_CHOICES,
        blank=True
    )
    contact_number = models.CharField(max_length=20, blank=True)

    # Set to True once the applicant has confirmed the one-time code we
    # emailed them at sign-up. Accounts that exist but were never confirmed
    # sit at is_active=False and cannot log in, and are cleaned up when the
    # address is signed up for again.
    #
    # Accounts that never go through the portal sign-up flow — superusers
    # created on the command line, and legacy walk-in records from the
    # removed quick-register flow, which have no email at all — are simply
    # not covered by this flag; nothing gates them on it.
    email_verified = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.get_full_name()} ({self.username})"

    def is_applicant(self):
        return self.role == 'applicant'

    def is_sticker_admin(self):
        return self.role == 'admin'

    def is_security(self):
        return self.role == 'security'


class EmailOTP(models.Model):
    """
    A one-time code emailed to an applicant to prove they own the campus
    address they signed up with.

    The code itself is never stored — only a hash of it — so a database
    dump doesn't hand out working codes. A row stops being usable once
    `used_at` is set (either because it was redeemed, or because a newer
    code superseded it), once it passes `expires_at`, or once `attempts`
    reaches the configured maximum.
    """

    PURPOSE_REGISTER = 'register'
    PURPOSE_CHOICES = [
        (PURPOSE_REGISTER, 'Account Registration'),
    ]

    user = models.ForeignKey(
        'accounts.User',
        on_delete=models.CASCADE,
        related_name='email_otps'
    )
    purpose = models.CharField(
        max_length=20,
        choices=PURPOSE_CHOICES,
        default=PURPOSE_REGISTER
    )
    code_hash = models.CharField(max_length=128)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'purpose', 'used_at']),
        ]

    def __str__(self):
        return f'{self.purpose} code for {self.user.username}'

    def is_expired(self):
        return timezone.now() >= self.expires_at