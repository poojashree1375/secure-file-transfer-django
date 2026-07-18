from django.conf import settings
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

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        db_index=True,
        related_name="uploads",
    )

class DeletedUpload(models.Model):
    REASON_MANUAL = "manual"     # user clicked Delete
    REASON_EXPIRED = "expired"   # delete_expired sweeper
    REASON_QUOTA = "quota"       # download view after last allowed download

    REASON_CHOICES = [
        (REASON_MANUAL, "Deleted by you"),
        (REASON_EXPIRED, "Expired"),
        (REASON_QUOTA, "Download limit reached"),
    ]

    token = models.CharField(max_length=100, db_index=True)
    file_name = models.CharField(max_length=255)
    file_type = models.CharField(max_length=100)
    max_downloads = models.IntegerField()
    download_count = models.IntegerField()
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        db_index=True,
        related_name="deleted_uploads",
    )
    uploaded_at = models.DateTimeField()
    deleted_at = models.DateTimeField(auto_now_add=True)
    reason = models.CharField(
        max_length=20,
        choices=REASON_CHOICES,
    )

    def __str__(self):
        return f"DeletedUpload(token={self.token}, reason={self.reason})"
