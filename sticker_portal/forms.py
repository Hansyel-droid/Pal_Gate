from django import forms
from accounts.models import User
from .models import Vehicle, StickerApplication, Document


class StickerAdminProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'employee_id', 'contact_number']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'settings-input'}),
            'last_name': forms.TextInput(attrs={'class': 'settings-input'}),
            'employee_id': forms.TextInput(attrs={'class': 'settings-input', 'readonly': 'readonly'}),
            'contact_number': forms.TextInput(attrs={'class': 'settings-input'}),
        }


class VehicleForm(forms.ModelForm):
    class Meta:
        model = Vehicle
        fields = ['plate_number', 'model', 'color', 'is_owner']
        widgets = {
            'plate_number': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. ABC-1234'}),
            'model': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Toyota Vios'}),
            'color': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Silver'}),
            'is_owner': forms.RadioSelect(choices=[(True, 'Yes'), (False, 'No')]),
        }


class StickerApplicationForm(forms.ModelForm):
    full_name = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-input', 'readonly': 'readonly'})
    )
    college_department = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-input', 'readonly': 'readonly'})
    )
    student_id = forms.CharField(
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-input', 'readonly': 'readonly'})
    )
    classification = forms.ChoiceField(
        choices=[('student', 'Student'), ('faculty', 'Faculty/Staff')],
        widget=forms.RadioSelect(attrs={'class': 'radio-group'})
    )

    class Meta:
        model = StickerApplication
        fields = ['expiry_date']
        widgets = {
            'expiry_date': forms.DateInput(attrs={
                'type': 'date',
                'class': 'form-input',
                'min': '2025-01-01',
                'max': '2026-12-31'
            }),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if user:
            self.fields['full_name'].initial = user.get_full_name()
            self.fields['college_department'].initial = user.college_department
            self.fields['student_id'].initial = user.student_id or user.employee_id
            self.fields['classification'].initial = user.classification


class DocumentUploadForm(forms.Form):
    or_cr = forms.FileField(required=True)
    drivers_license = forms.FileField(required=True)
    cor = forms.FileField(required=False)
    auth_letter = forms.FileField(required=False)