from django import forms
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


class LoginForm(forms.Form):
    """
    A simple login form.
    """
    username = forms.CharField()
    password = forms.CharField(widget=forms.PasswordInput)