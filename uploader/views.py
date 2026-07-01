import logging
import os
import uuid

import qrcode
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from datetime import timedelta

from .cleanup import cleanup_file
from .crypto_utils import encrypt_file, decrypt_file
from .forms import UploadForm
from .models import FileUpload


logger = logging.getLogger(__name__)


def _expired_response(request):
    return render(request, "expired.html")


def _is_spent(file_obj: FileUpload) -> bool:
    """A file is spent if it has expired or hit its download cap."""
    return (
        timezone.now() > file_obj.expiry_datetime
        or file_obj.download_count >= file_obj.max_downloads
    )


def upload_file(request):
    if request.method == "POST":
        form = UploadForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded = request.FILES['file']
            expiry_minutes = form.cleaned_data['expiry_minutes']
            max_downloads = form.cleaned_data['max_downloads']

            try:
                encrypted = encrypt_file(uploaded.read())
            except FileNotFoundError:
                # secret.key is missing — surface a clear error rather than
                # silently corrupting uploads.
                logger.exception("Fernet key missing during upload")
                return HttpResponse(
                    "Server is missing its encryption key. Run "
                    "`python manage.py generate_key`.",
                    status=500,
                )

            upload_dir = os.path.join(settings.MEDIA_ROOT, "uploads")
            os.makedirs(upload_dir, exist_ok=True)
            filename = f"{uuid.uuid4()}.enc"
            filepath = os.path.join(upload_dir, filename)

            with open(filepath, "wb") as f:
                f.write(encrypted)

            obj = FileUpload.objects.create(
                token=str(uuid.uuid4()),
                file_name=uploaded.name,
                file_path=filepath,
                file_type=uploaded.content_type,
                expiry_datetime=timezone.now() + timedelta(minutes=expiry_minutes),
                max_downloads=max_downloads,
            )

            download_url = request.build_absolute_uri(f"/download/{obj.token}/")
            qr = qrcode.make(download_url)

            qr_folder = os.path.join(settings.MEDIA_ROOT, "qrcodes")
            os.makedirs(qr_folder, exist_ok=True)
            qr_filename = f"{obj.token}.png"
            qr_path = os.path.join(qr_folder, qr_filename)
            qr.save(qr_path)

            obj.qr_code = f"qrcodes/{qr_filename}"
            obj.save()

            return redirect("show_link", token=obj.token)
    else:
        form = UploadForm()

    return render(request, "upload.html", {"form": form})


def show_link(request, token):
    try:
        file_obj = FileUpload.objects.get(token=token)
    except FileUpload.DoesNotExist:
        return _expired_response(request)

    if _is_spent(file_obj):
        cleanup_file(file_obj)
        return _expired_response(request)

    download_url = request.build_absolute_uri(f"/download/{token}/")
    remaining = file_obj.max_downloads - file_obj.download_count

    return render(request, "link.html", {
        "file": file_obj,
        "download_url": download_url,
        "remaining": remaining,
    })


def download_file(request, token):
    try:
        file = FileUpload.objects.get(token=token)
    except FileUpload.DoesNotExist:
        return _expired_response(request)

    # Lock the row, check both expiry and capacity, and increment the
    # counter in a single atomic block so concurrent downloads can't
    # both win the last slot, and an expiry between checks can't leak.
    with transaction.atomic():
        file = (
            FileUpload.objects
            .select_for_update()
            .get(pk=file.pk)
        )

        if timezone.now() > file.expiry_datetime:
            cleanup_file(file)
            return _expired_response(request)

        if file.download_count >= file.max_downloads:
            cleanup_file(file)
            return _expired_response(request)

        FileUpload.objects.filter(pk=file.pk).update(
            download_count=F("download_count") + 1
        )
        file.refresh_from_db(fields=["download_count"])

    # We won the race — read and decrypt the blob.
    with open(file.file_path, "rb") as f:
        encrypted = f.read()

    try:
        decrypted_bytes = decrypt_file(encrypted)
    except (ValueError, FileNotFoundError):
        logger.exception("Failed to decrypt file %s", file.token)
        cleanup_file(file)
        return _expired_response(request)

    response = HttpResponse(decrypted_bytes, content_type=file.file_type)
    response["Content-Disposition"] = f'attachment; filename="{file.file_name}"'

    # If this was the final allowed download, clean up now.
    if file.download_count >= file.max_downloads:
        cleanup_file(file)

    return response