from django import forms
from accounts.models import User
from .models import Vehicle, StickerApplication


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
        fields = ['plate_number', 'type_of_vehicle', 'color', 'is_owner']
        widgets = {
            'plate_number': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. ABC-1234'}),
            'type_of_vehicle': forms.Select(attrs={'class': 'form-input'}),
            'color': forms.Select(attrs={'class': 'form-input'}),
            'is_owner': forms.RadioSelect(choices=[(True, 'Yes'), (False, 'No')]),
        }


class StickerApplicationForm(forms.ModelForm):
    full_name = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-input'})
    )
    college_department = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-input'})
    )
    student_id = forms.CharField(
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-input'})
    )
    classification = forms.ChoiceField(
        choices=[
            ('student', 'Student'),
            ('faculty', 'Faculty/Staff'),
            ('parent', 'Parent')
        ],
        widget=forms.RadioSelect(attrs={'class': 'radio-group'})
    )

    class Meta:
        model = StickerApplication
        fields = ['classification']

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if user:
            self.fields['full_name'].initial = user.get_full_name()
            self.fields['college_department'].initial = getattr(user, 'college_department', '')
            self.fields['student_id'].initial = getattr(user, 'student_id', getattr(user, 'employee_id', ''))
            self.fields['classification'].initial = getattr(user, 'classification', 'student')


class DocumentUploadForm(forms.Form):
    or_cr = forms.FileField(label="OR/CR", required=True)
    drivers_license = forms.FileField(label="Driver's License", required=True)
    cor = forms.FileField(label="Certificate of Registration (COR)", required=False)
    auth_letter = forms.FileField(label="Authorization Letter", required=False)