from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


# The colleges of Palawan State University.
#
# It lives here, above both models, because the same list has to drive the
# sign-up form and the application wizard — an applicant picks a college once
# at registration and again on Step 1, and the two lists drifting apart is how
# "CCIS" and "College of Sciences" end up in the same column.
#
# Stored value == display label. The column is free-text CharField(100) on both
# models and already holds whatever people typed before this list existed, so
# storing short codes would have split the data into two vocabularies; storing
# the full name means every screen that renders the field — the reviewer's
# detail page, the sticker station, the incident-report PDF — keeps printing a
# name a person can read, with no display lookup to add.
COLLEGE_CHOICES = [
    (name, name) for name in [
        'College of Arts and Humanities',
        'College of Business and Accountancy',
        'College of Criminal Justice Education',
        'College of Engineering',
        'College of Architecture',
        'College of Hospitality Management and Tourism',
        'College of Nursing and Health Sciences',
        'College of Sciences',
        'College of Teacher Education',
    ]
]


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
    college_department = models.CharField(
        max_length=100,
        choices=COLLEGE_CHOICES,
        blank=True
    )
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


class PolicyAcceptance(models.Model):
    """
    A record that one user read and accepted one version of the Campus
    Access Policy.

    Rows are never updated or deleted — a new acceptance is a new row. The
    point of this table is to be able to answer "had this person agreed to
    the rules that were in force at the time?" months later, which an
    overwritten boolean on User could not do.

    `version` is matched against settings-level CAMPUS_POLICY_VERSION (see
    accounts.policy). Bumping that constant is what forces the whole
    applicant population to read and accept a revised memorandum, rather
    than silently swapping the text under people who agreed to something
    else. The memorandum itself says it is "without prejudice to subsequent
    amendment or revocation", so revisions are expected.
    """

    user = models.ForeignKey(
        'accounts.User',
        on_delete=models.CASCADE,
        related_name='policy_acceptances'
    )
    version = models.CharField(max_length=20)
    accepted_at = models.DateTimeField(auto_now_add=True)

    # Captured for the same reason the gate logs capture it: if an
    # acceptance is ever disputed, "who clicked it and from where" is the
    # only evidence there is. Nullable because a request behind a proxy we
    # don't trust yet (see TRUST_X_FORWARDED_FOR) may not have a usable one.
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ['-accepted_at']
        indexes = [
            models.Index(fields=['user', 'version']),
        ]
        # One acceptance per user per version. Without this, a double-submit
        # (impatient click, browser retry) writes two identical rows and the
        # audit trail starts lying about how many times someone agreed.
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'version'],
                name='unique_policy_acceptance_per_version'
            )
        ]

    def __str__(self):
        return f'{self.user.username} accepted policy {self.version}'


class Notification(models.Model):
    """
    An in-app notification for one user — the red-dot inbox in the topbar.

    Deliberately separate from applications.notifications (the email
    sender): that module's whole job is "does an email leave the server",
    and it already has its own reasons to fail silently (no address on
    file, mail server down) without breaking the action that triggered it.
    A Notification row here is a plain, transactional database write with
    none of that — it either commits with the rest of the request or it
    doesn't, same as any other model. The two are wired up at the same
    call sites (see accounts.notifications) so the in-app and email trails
    agree, but neither depends on the other succeeding.
    """
    recipient = models.ForeignKey(
        'accounts.User',
        on_delete=models.CASCADE,
        related_name='notifications',
    )
    message = models.CharField(max_length=255)
    # Where clicking the notification should go — a relative path such as
    # `/sticker-admin/applications/7/`. Blank is valid (falls back to the
    # notifications list itself) rather than required, so a future
    # notification type that has nowhere specific to send someone isn't
    # forced to invent a link.
    link = models.CharField(max_length=255, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['recipient', 'is_read']),
        ]

    def __str__(self):
        return f'{self.recipient.username}: {self.message[:50]}'


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