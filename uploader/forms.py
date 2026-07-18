from django import forms
import os

MAX_FILE_SIZE = 10 * 1024 * 1024

ALLOWED_EXTENSIONS = [
    'pdf', 'txt', 'png', 'jpg', 'jpeg', 'docx', 'zip'
]

ALLOWED_TYPES = [
    'application/pdf',
    'image/png',
    'image/jpeg',
    'text/plain',
    'application/zip',
    'application/x-zip-compressed',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
]

class UploadForm(forms.Form):

    file = forms.FileField(
        label="Choose File",
        widget=forms.ClearableFileInput(
            attrs={
                "class": "form-control"
            }
        )
    )

    expiry_minutes = forms.IntegerField(
        min_value=1,
        label="Expiry (minutes)",
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "placeholder": "Enter expiry time"
            }
        )
    )

    max_downloads = forms.IntegerField(
        min_value=1,
        label="Maximum Downloads",
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "placeholder": "Enter max downloads"
            }
        )
    )

    def clean_file(self):

        file = self.cleaned_data['file']

        ext = os.path.splitext(file.name)[1].lower().replace(".", "")

        if ext not in ALLOWED_EXTENSIONS:
            raise forms.ValidationError(
                "Only PDF, TXT, PNG, JPG, JPEG, DOCX and ZIP files are allowed."
            )
        
        if file.content_type not in ALLOWED_TYPES:
            raise forms.ValidationError(
                "File content does not match allowed file type."
            )

        # TODO(pre-deployment): the content_type check above relies on a value
        # supplied by the client. Before deploying, swap it for a magic-byte
        # so a renamed .exe can't pass as a .pdf.

        if file.size > MAX_FILE_SIZE:
            raise forms.ValidationError(
                "File size must be below 10 MB."
            )

        return file