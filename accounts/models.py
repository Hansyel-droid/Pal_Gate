from django.contrib.auth.models import AbstractUser
from django.db import models


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

    def __str__(self):
        return f"{self.get_full_name()} ({self.username})"

    def is_applicant(self):
        return self.role == 'applicant'

    def is_sticker_admin(self):
        return self.role == 'admin'

    def is_security(self):
        return self.role == 'security'