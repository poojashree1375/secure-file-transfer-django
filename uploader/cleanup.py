import logging
import os

from django.conf import settings
from django.utils import timezone

from .models import FileUpload


logger = logging.getLogger(__name__)


def cleanup_file(file_obj: FileUpload) -> None:
    """
    Delete the encrypted blob on disk, the QR PNG, and the DB row.

    Safe to call on a file where any of these are already missing —
    we only remove what exists.
    """
    try:
        if os.path.exists(file_obj.file_path):
            os.remove(file_obj.file_path)
            logger.info("Deleted encrypted blob for %s", file_obj.token)
    except OSError:
        logger.exception("Failed to delete blob for %s", file_obj.token)

    if file_obj.qr_code:
        qr_path = os.path.join(settings.MEDIA_ROOT, file_obj.qr_code)
        try:
            if os.path.exists(qr_path):
                os.remove(qr_path)
                logger.info("Deleted QR code for %s", file_obj.token)
        except OSError:
            logger.exception("Failed to delete QR for %s", file_obj.token)

    file_obj.delete()


def delete_expired() -> None:
    """Scheduled task: clean up files whose expiry has passed."""
    expired_files = FileUpload.objects.filter(
        expiry_datetime__lt=timezone.now()
    )

    for file in expired_files:
        logger.info("Deleting expired file: %s", file.file_name)
        cleanup_file(file)