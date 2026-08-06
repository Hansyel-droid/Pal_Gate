from django import forms
from django.conf import settings
from django.contrib.auth.forms import UserCreationForm
from .models import User


class RegisterForm(UserCreationForm):
    """
    The sign-up form for new applicants.
    """
    first_name = forms.CharField(max_length=50, required=True)
    last_name = forms.CharField(max_length=50, required=True)
    email = forms.EmailField(required=True)
    college_department = forms.CharField(max_length=100, required=False)
    id_number = forms.CharField(max_length=50, required=False)
    classification = forms.ChoiceField(
        choices=[('', '---------')] + User.CLASSIFICATION_CHOICES,
        required=False
    )
    contact_number = forms.CharField(max_length=20, required=False)

    class Meta:
        model = User
        fields = [
            'username', 'first_name', 'last_name', 'email',
            'college_department', 'id_number', 'classification',
            'contact_number', 'password1', 'password2'
        ]

    def clean_email(self):
        # Normalize first: addresses are matched case-insensitively
        # everywhere else, so store them in one consistent shape.
        email = self.cleaned_data['email'].strip().lower()
        domain = settings.REGISTRATION_EMAIL_DOMAIN

        # Self-registration is for the campus community only. The domain
        # check is what makes the emailed code meaningful — anyone can
        # receive mail at a personal address, but only students, faculty,
        # and staff have a PalSU mailbox.
        if not email.endswith('@' + domain):
            raise forms.ValidationError(
                f'Use your official PalSU email address '
                f'(it must end in @{domain}).'
            )

        local_part = email[: -(len(domain) + 1)]
        if not local_part:
            raise forms.ValidationError(
                'Enter the full email address, including the part before "@".'
            )

        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                'An account with this email address already exists.'
            )
        return email


class OTPVerifyForm(forms.Form):
    """
    The single-field form on the "check your email" step.
    """
    # Deliberately looser than OTP_LENGTH so a pasted "123 456" reaches
    # clean_code() and gets tidied up instead of being rejected outright.
    code = forms.CharField(
        label='Verification code',
        max_length=settings.OTP_LENGTH + 6,
    )

    def clean_code(self):
        # Tolerate the spaces and dashes people paste out of an email.
        code = self.cleaned_data['code'].strip().replace(' ', '').replace('-', '')
        if not code.isdigit() or len(code) != settings.OTP_LENGTH:
            raise forms.ValidationError(
                f'Enter the {settings.OTP_LENGTH}-digit code from your email.'
            )
        return code


class LoginForm(forms.Form):
    """
    A simple login form.
    """
    username = forms.CharField()
    password = forms.CharField(widget=forms.PasswordInput)
