from django import forms
from accounts.models import User

class OfficerProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'employee_id', 'contact_number']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'settings-input'}),
            'last_name': forms.TextInput(attrs={'class': 'settings-input'}),
            'employee_id': forms.TextInput(attrs={'class': 'settings-input', 'readonly': 'readonly'}),
            'contact_number': forms.TextInput(attrs={'class': 'settings-input'}),
        }