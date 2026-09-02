import re
import unicodedata

from django import forms
from django.conf import settings
from django.contrib.auth.forms import PasswordResetForm, UserCreationForm
from django.db.models import Q
from .models import COLLEGE_CHOICES, User


def _ci_equal(a, b):
    """
    Case-insensitive comparison that also folds the Unicode lookalikes a
    database `__iexact` misses (the same NFKC + casefold Django applies
    before it will email a password reset link to an address).
    """
    return (
        unicodedata.normalize('NFKC', a).casefold()
        == unicodedata.normalize('NFKC', b).casefold()
    )


class RegisterForm(UserCreationForm):
    """
    The sign-up form for new applicants.
    """
    first_name = forms.CharField(max_length=50, required=True)
    last_name = forms.CharField(max_length=50, required=True)
    email = forms.EmailField(required=True)
    college_department = forms.ChoiceField(
        choices=[('', '---------')] + COLLEGE_CHOICES,
        required=False
    )
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
        email = self.cleaned_data['email'].strip().lower()
        domain = settings.REGISTRATION_EMAIL_DOMAIN

        if not email.endswith('@' + domain):
            raise forms.ValidationError(
                f'Use your official PalawanSU email address '
                f'(it must end in @{domain}).'
            )

        local_part = email[: -(len(domain) + 1)]
        if not local_part:
            raise forms.ValidationError(
                'Enter the full email address, including the part before "@".'
            )

        # Ignore unverified accounts here since _release_abandoned_signups handles them
        if User.objects.filter(email__iexact=email, is_active=True).exists():
            raise forms.ValidationError(
                'An account with this email address already exists.'
            )
        return email

    def clean_id_number(self):
        id_number = self.cleaned_data.get('id_number', '').strip()
        if id_number:
            pattern = r'^\d{4}-\d{1,2}-\d{4}$'
            if not re.match(pattern, id_number):
                raise forms.ValidationError(
                    'Enter a valid ID number format (e.g., 2023-8-0158).'
                )
        return id_number


class OTPVerifyForm(forms.Form):
    """
    The form on the "check your email" step.

    `identifier` is normally invisible and unused: the pending account is
    found through the session set when registration started. It only
    matters when that session didn't make it back — a different browser,
    a different device, a cleared cookie jar, or anything else that breaks
    the assumption that "the person typing the code" and "the person who
    submitted the sign-up form" share one session. Without it, the entire
    verification step depends on session continuity holding across however
    long it takes someone to go read an email, which real users doing this
    on a phone regularly break. Optional here; the view enforces it only
    when the session lookup comes back empty.
    """
    identifier = forms.CharField(
        label='Username or email',
        required=False,
        max_length=254,
    )
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


class CampusPasswordResetForm(PasswordResetForm):
    """
    "Forgot password" for every kind of account in the system.

    Django's stock form asks for an email address, which only ever fit
    applicants — they sign up with one and it is verified. Sticker
    administrators and security officers are made in the Django admin and
    sign in with a username; asked for "your email address" on the reset
    page, the honest answer for some of them is that they don't know which
    one, if any, is on the account. They would type something, be told a
    link was on its way, and wait for mail that was never sent.

    So the field takes whichever of the two the person actually remembers.
    The lookup widens; delivery does not. PasswordResetForm.save() mails
    `user.email` for each user get_users() yields, never the string that
    was typed, so entering somebody else's username sends the link to that
    person's own inbox and tells the sender nothing.
    """

    email = forms.CharField(
        label='Username or campus email',
        max_length=254,
        strip=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'autocomplete': 'username',
            'autofocus': True,
            'aria-describedby': 'reset_help',
            'placeholder': f'you@{settings.REGISTRATION_EMAIL_DOMAIN}',
        }),
    )

    def get_users(self, identifier):
        """
        The accounts a reset link may be sent to for this identifier.

        Three filters, each of which would otherwise produce a link that
        cannot work:

        - `is_active=True` — an applicant who never typed back their
          sign-up code is inactive and cannot log in, so a new password
          buys them nothing. They need to finish verifying instead, which
          is what the reset page tells them.
        - a non-empty email — the link has nowhere to go. Legacy walk-in
          records and command-line accounts fall in here.
        - a usable password — set_unusable_password() accounts are not
          password logins at all.
        """
        candidates = User.objects.filter(
            Q(email__iexact=identifier) | Q(username__iexact=identifier),
            is_active=True,
        ).exclude(email='')

        # __iexact only case-folds ASCII on SQLite, so re-check the match
        # the way Django's own form does before mailing anyone — this is
        # what stops a lookalike Unicode address from matching.
        return [
            user for user in candidates
            if user.has_usable_password()
            and (
                _ci_equal(identifier, user.email)
                or _ci_equal(identifier, user.username)
            )
        ]