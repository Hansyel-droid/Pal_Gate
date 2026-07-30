from django import forms
from .models import StickerApplication


class ApplicationStep1Form(forms.Form):
    """Step 1: Personal Details"""

    full_name = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )
    college_department = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )
    id_number = forms.CharField(
        max_length=50,
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )
    classification = forms.ChoiceField(
        choices=[('', '---------')] + StickerApplication.CLASSIFICATION_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'})
    )


class ApplicationStep2Form(forms.Form):
    """Step 2: Vehicle Details and Document Uploads"""

    ALLOWED_EXTENSIONS = ['pdf', 'jpg', 'jpeg', 'png']

    plate_number = forms.CharField(
        max_length=20,
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )
    vehicle_type = forms.ChoiceField(
        choices=[('', '---------')] + StickerApplication.VEHICLE_TYPE_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    vehicle_color = forms.ChoiceField(
        choices=[('', '---------')] + StickerApplication.COLOR_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    is_owner = forms.ChoiceField(
        choices=[('yes', 'Yes – I own this vehicle'), ('no', 'No – I am not the owner')],
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    # Documents
    or_cr = forms.FileField(
        label='OR/CR (Official Receipt / Certificate of Registration)',
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.pdf,.jpg,.jpeg,.png'})
    )
    drivers_license = forms.FileField(
        label="Driver's License",
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.pdf,.jpg,.jpeg,.png'})
    )
    cor = forms.FileField(
        label='Certificate of Registration (COR) – Required for Students',
        required=False,
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.pdf,.jpg,.jpeg,.png'})
    )
    authorization_letter = forms.FileField(
        label='Authorization Letter – Required if you are NOT the owner',
        required=False,
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.pdf,.jpg,.jpeg,.png'})
    )

    def validate_file_extension(self, file):
        """Check that the uploaded file is PDF, JPG, JPEG, or PNG."""
        if file:
            extension = file.name.split('.')[-1].lower()
            if extension not in self.ALLOWED_EXTENSIONS:
                raise forms.ValidationError(
                    f'Only PDF, JPG, JPEG, PNG files are allowed. You uploaded: .{extension}'
                )

    def clean(self):
        cleaned_data = super().clean()
        classification = self.data.get('step1_classification', '')
        is_owner = cleaned_data.get('is_owner')

        # Validate file types for all uploaded files
        for field_name in ['or_cr', 'drivers_license', 'cor', 'authorization_letter']:
            file = cleaned_data.get(field_name)
            if file:
                try:
                    self.validate_file_extension(file)
                except forms.ValidationError as e:
                    self.add_error(field_name, e)

        # COR is required ONLY for students
        if classification == 'student':
            if not cleaned_data.get('cor'):
                self.add_error('cor', 'COR is required for student applicants.')
        else:
            # Not a student — clear any COR that was somehow submitted
            cleaned_data['cor'] = None

        # Authorization letter required ONLY if not the owner
        if is_owner == 'no':
            if not cleaned_data.get('authorization_letter'):
                self.add_error(
                    'authorization_letter',
                    'Authorization letter is required if you are not the vehicle owner.'
                )
        else:
            # Is the owner — clear any auth letter that was somehow submitted
            cleaned_data['authorization_letter'] = None

        return cleaned_data