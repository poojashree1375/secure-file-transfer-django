import logging
import os
from django.conf import settings
from django.utils import timezone

from .models import DeletedUpload, FileUpload


logger = logging.getLogger(__name__)


def cleanup_file(file_obj: FileUpload, reason: str = DeletedUpload.REASON_MANUAL) -> None:
    #Delete the encrypted blob on disk, the QR PNG, and the DB row.

    DeletedUpload.objects.create(
        token=file_obj.token,
        file_name=file_obj.file_name,
        file_type=file_obj.file_type,
        max_downloads=file_obj.max_downloads,
        download_count=file_obj.download_count,
        user=file_obj.user,
        uploaded_at=file_obj.uploaded_at,
        reason=reason,
    )

    try:
        if os.path.exists(file_obj.file_path):
            os.remove(file_obj.file_path)
    except OSError:
        logger.exception("Failed to remove encrypted blob for %s", file_obj.token)

    try:
        if file_obj.qr_code:
            qr_path = os.path.join(settings.MEDIA_ROOT, file_obj.qr_code)
            if os.path.exists(qr_path):
                os.remove(qr_path)
    except OSError:
        logger.exception("Failed to remove QR for %s", file_obj.token)

    file_obj.delete()


def delete_expired() -> None:
    #Scheduled task: clean up files whose expiry has passed.
    expired_files = FileUpload.objects.filter(
        expiry_datetime__lt=timezone.now()
    )

    for file in expired_files:
        logger.info("Deleting expired file: %s", file.file_name)
        cleanup_file(file, reason=DeletedUpload.REASON_EXPIRED)
