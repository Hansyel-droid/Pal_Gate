from django.contrib.auth.models import AbstractUser
from django.db import models

class User(AbstractUser):
    USER_TYPE_CHOICES = (
        ('security_officer', 'Security Officer'),
        ('sticker_admin', 'Sticker Administrator'),
        ('applicant', 'Applicant'),
    )
    CLASSIFICATION_CHOICES = (
        ('student', 'Student'),
        ('faculty', 'Faculty/Staff'),
    )

    user_type = models.CharField(max_length=20, choices=USER_TYPE_CHOICES, default='applicant')
    employee_id = models.CharField(max_length=50, blank=True, null=True, unique=True)
    student_id = models.CharField(max_length=50, blank=True, null=True, unique=True)
    college_department = models.CharField(max_length=100, blank=True)
    classification = models.CharField(max_length=10, choices=CLASSIFICATION_CHOICES, blank=True, null=True)
    contact_number = models.CharField(max_length=15, blank=True)

    def __str__(self):
        return f"{self.get_full_name()} ({self.user_type})"

    def is_security_officer(self):
        return self.user_type == 'security_officer'

    def is_sticker_admin(self):
        return self.user_type == 'sticker_admin'

    def is_applicant(self):
        return self.user_type == 'applicant'