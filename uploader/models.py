from django.db import models
import uuid

class FileUpload(models.Model):
    token=models.CharField(max_length=100,unique=True,default=uuid.uuid4)
    expiry_datetime=models.DateTimeField(db_index=True)
    uploaded_at=models.DateTimeField(auto_now_add=True)
    file_name=models.CharField(max_length=255)
    file_type=models.CharField(max_length=100)
    file_path=models.CharField(max_length=255)
    max_downloads = models.IntegerField(default=1)
    download_count = models.IntegerField(default=0)
    qr_code = models.CharField(
        max_length=255,
        null=True,
        blank=True
    )


    def __str__(self):
        return f"EncryptedFile(token={self.token})"
    