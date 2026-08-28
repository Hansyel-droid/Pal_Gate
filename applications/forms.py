from django import forms
from accounts.models import COLLEGE_CHOICES
from .models import StickerApplication


class ApplicationStep1Form(forms.Form):
    """Step 1: Personal Details"""

    full_name = forms.CharField(
        max_length=100,
        # autocomplete is a render attribute, like the class beside it.
        # WCAG 2.1 AA 1.3.5 (Identify Input Purpose) requires it on fields
        # collecting the user's own name, and a widget attr is the only place
        # a template can't reach.
        widget=forms.TextInput(attrs={'class': 'form-control',
                                      'autocomplete': 'name'})
    )
    # A fixed list rather than free text: reviewers filter and report on this
    # column, and typing it left "CCIS", "CS", and "College of Sciences" as
    # three different colleges.
    college_department = forms.ChoiceField(
        choices=[('', '---------')] + COLLEGE_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'})
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

    DOCUMENT_FIELDS = [
        'official_receipt', 'vehicle_registration',
        'drivers_license', 'cor', 'authorization_letter',
    ]

    ALLOWED_EXTENSIONS = ['pdf', 'jpg', 'jpeg', 'png']
    MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

    # Magic-byte signatures — checked against the file's actual bytes so a
    # renamed .exe with a .pdf extension can't slip through.
    FILE_SIGNATURES = {
        'pdf': [b'%PDF'],
        'jpg': [b'\xff\xd8\xff'],
        'jpeg': [b'\xff\xd8\xff'],
        'png': [b'\x89PNG\r\n\x1a\n'],
    }

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
    # Shown/required only when vehicle_color == 'other' — see clean() below,
    # which folds this into vehicle_color itself so the model keeps a single
    # column and every existing get_vehicle_color_display() call site (the
    # incident report PDF, the admin detail page) needs no changes at all.
    vehicle_color_other = forms.CharField(
        required=False,
        max_length=30,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter the vehicle\'s color',
        })
    )
    is_owner = forms.ChoiceField(
        choices=[('yes', 'Yes – I own this vehicle'), ('no', 'No – I am not the owner')],
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    # Documents. The OR and the CR are two separate LTO papers, so they are
    # two separate uploads — see the note on StickerApplication for why the
    # vehicle CR is not called `cr` next to the students' `cor`.
    official_receipt = forms.FileField(
        label='Official Receipt (OR)',
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.pdf,.jpg,.jpeg,.png'})
    )
    vehicle_registration = forms.FileField(
        label='Certificate of Registration (CR)',
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

    def __init__(self, *args, existing_files=None, **kwargs):
        """
        `existing_files` maps document field names to the {'path',
        'original_name'} entry already sitting in temp storage from an
        earlier pass through this step — a renewal seeded by apply_renew, or
        the applicant arriving via the "Change" links on Step 3 and Step 4.

        A document we already hold stops being required. Without this,
        revisiting Step 2 to correct a typo in the plate number forced a
        re-upload of every required document, and the renewal flow — which
        explicitly tells applicants to come back here — could not be
        completed at all without re-uploading what apply_renew just copied.
        """
        super().__init__(*args, **kwargs)
        self.existing_files = {
            name: data
            for name, data in (existing_files or {}).items()
            if data and name in self.DOCUMENT_FIELDS
        }
        for field_name in self.existing_files:
            self.fields[field_name].required = False

        # Returning to this step (renewal, or a "Change" link from Step 3/4)
        # seeds `initial['vehicle_color']` with whatever was saved before.
        # If that's custom text from a previous "Other" entry rather than
        # one of the fixed choice keys, the <select> can't show it directly
        # — so re-point the dropdown at 'other' and hand the actual text to
        # the textbox, instead of it silently rendering as unselected.
        color_keys = {key for key, _ in StickerApplication.COLOR_CHOICES}
        current_color = self.initial.get('vehicle_color')
        if current_color and current_color not in color_keys:
            self.initial['vehicle_color'] = 'other'
            self.initial['vehicle_color_other'] = current_color

    def validate_file_extension(self, file):
        """Check that the uploaded file is PDF, JPG, JPEG, or PNG — by both
        extension and actual file content, and enforce a size cap."""
        if not file:
            return

        extension = file.name.split('.')[-1].lower()
        if extension not in self.ALLOWED_EXTENSIONS:
            raise forms.ValidationError(
                f'Only PDF, JPG, JPEG, PNG files are allowed. You uploaded: .{extension}'
            )

        if file.size > self.MAX_FILE_SIZE:
            raise forms.ValidationError(
                f'File is too large ({file.size // (1024 * 1024)}MB). '
                f'Maximum allowed size is {self.MAX_FILE_SIZE // (1024 * 1024)}MB.'
            )

        signatures = self.FILE_SIGNATURES[extension]
        header = file.read(16)
        file.seek(0)
        if not any(header.startswith(sig) for sig in signatures):
            raise forms.ValidationError(
                f'The file you uploaded does not look like a real .{extension} file. '
                'Please upload an unmodified PDF, JPG, JPEG, or PNG.'
            )

    def clean_plate_number(self):
        # Normalize so "abc 123", "ABC123", and "ABC 123 " are all treated
        # as the same plate for the uniqueness check.
        return self.cleaned_data['plate_number'].strip().upper()

    def clean(self):
        cleaned_data = super().clean()
        classification = self.data.get('step1_classification', '')
        is_owner = cleaned_data.get('is_owner')

        # "Other" needs the typed-in color, and folds it straight into
        # vehicle_color so the rest of the app — session storage, the
        # model's single vehicle_color column, get_vehicle_color_display()
        # on the admin detail page and the incident report — never has to
        # know a second field exists.
        if cleaned_data.get('vehicle_color') == 'other':
            custom_color = (cleaned_data.get('vehicle_color_other') or '').strip()
            if not custom_color:
                self.add_error(
                    'vehicle_color_other',
                    'Please type the vehicle\'s color.'
                )
            else:
                cleaned_data['vehicle_color'] = custom_color

        # Validate file types for all uploaded files
        for field_name in self.DOCUMENT_FIELDS:
            file = cleaned_data.get(field_name)
            if file:
                try:
                    self.validate_file_extension(file)
                except forms.ValidationError as e:
                    self.add_error(field_name, e)

        # COR is required ONLY for students — and only if we don't already
        # hold one from an earlier pass through this step.
        if classification == 'student':
            if not cleaned_data.get('cor') and 'cor' not in self.existing_files:
                self.add_error('cor', 'COR is required for student applicants.')
        else:
            # Not a student — clear any COR that was somehow submitted
            cleaned_data['cor'] = None

        # Authorization letter required ONLY if not the owner
        if is_owner == 'no':
            if (not cleaned_data.get('authorization_letter')
                    and 'authorization_letter' not in self.existing_files):
                self.add_error(
                    'authorization_letter',
                    'Authorization letter is required if you are not the vehicle owner.'
                )
        else:
            # Is the owner — clear any auth letter that was somehow submitted
            cleaned_data['authorization_letter'] = None

        return cleaned_data