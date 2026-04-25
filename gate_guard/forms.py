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


class RFIDRegistrationForm(forms.Form):
    rfid_uid = forms.CharField(
        max_length=100,
        label='RFID UID',
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. 04A1B2C3D4'})
    )
    driver_name = forms.CharField(
        max_length=150,
        label='Driver Full Name',
        widget=forms.TextInput(attrs={'class': 'form-input'})
    )
    email = forms.EmailField(
        required=True,
        label='Driver Email (Login)',
        widget=forms.EmailInput(attrs={'class': 'form-input'})
    )
    classification = forms.ChoiceField(
        choices=[('student', 'Student'), ('faculty', 'Faculty/Staff')],
        label='Classification',
        widget=forms.RadioSelect(attrs={'class': 'radio-group'})
    )
    college_department = forms.CharField(
        max_length=100,
        required=False,
        label='College / Department',
        widget=forms.TextInput(attrs={'class': 'form-input'})
    )
    contact_number = forms.CharField(
        max_length=15,
        required=False,
        label='Contact Number',
        widget=forms.TextInput(attrs={'class': 'form-input'})
    )
    plate_number = forms.CharField(
        max_length=20,
        label='Plate Number',
        widget=forms.TextInput(attrs={'class': 'form-input'})
    )
    vehicle_model = forms.CharField(
        max_length=100,
        label='Vehicle Model',
        widget=forms.TextInput(attrs={'class': 'form-input'})
    )
    vehicle_color = forms.CharField(
        max_length=50,
        label='Vehicle Color',
        widget=forms.TextInput(attrs={'class': 'form-input'})
    )
    is_owner = forms.BooleanField(
        required=False,
        initial=True,
        label="Vehicle registered in driver's name?",
        widget=forms.CheckboxInput(attrs={'class': 'checkbox'})
    )
    expiry_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-input'}),
        label='Sticker Expiry Date'
    )