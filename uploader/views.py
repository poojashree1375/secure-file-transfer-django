import logging
import os
import uuid

import qrcode
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import F
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from datetime import timedelta

from .cleanup import cleanup_file
from .crypto_utils import encrypt_file, decrypt_file
from .forms import UploadForm
from .models import FileUpload, DeletedUpload
from .tokens import generate_token


logger = logging.getLogger(__name__)


def _expired_response(request):
    return render(request, "expired.html")


def _is_spent(file_obj: FileUpload) -> bool:
    #A file is spent if it has expired or hit its download cap
    return (
        timezone.now() > file_obj.expiry_datetime
        or file_obj.download_count >= file_obj.max_downloads
    )


def _spent_reason(file_obj: FileUpload) -> str:
    #Return the reason why a file is spent
    if timezone.now() > file_obj.expiry_datetime:
        return DeletedUpload.REASON_EXPIRED
    return DeletedUpload.REASON_QUOTA


def upload_file(request):
    if not request.user.is_authenticated:
        # Anonymous users must sign in before they can upload.
        return redirect("account_login")
    if request.method == "POST":
        form = UploadForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded = request.FILES['file']
            expiry_minutes = form.cleaned_data['expiry_minutes']
            max_downloads = form.cleaned_data['max_downloads']

            try:
                encrypted = encrypt_file(uploaded.read())
            except FileNotFoundError:
                # secret.key is missing — surface a clear error rather than silently corrupting uploads.
                logger.exception("Fernet key missing during upload")
                return HttpResponse(
                    "Server is missing its encryption key.",
                    status=500,
                )

            upload_dir = os.path.join(settings.MEDIA_ROOT, "uploads")
            os.makedirs(upload_dir, exist_ok=True)
            filename = f"{uuid.uuid4()}.enc"
            filepath = os.path.join(upload_dir, filename)

            with open(filepath, "wb") as f:
                f.write(encrypted)

            obj = FileUpload.objects.create(
                token=generate_token(),
                file_name=uploaded.name,
                file_path=filepath,
                file_type=uploaded.content_type,
                expiry_datetime=timezone.now() + timedelta(minutes=expiry_minutes),
                max_downloads=max_downloads,
                user=request.user,
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
        cleanup_file(file_obj, reason=_spent_reason(file_obj))
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

    with transaction.atomic():
        file = (
            FileUpload.objects
            .select_for_update()
            .get(pk=file.pk)
        )

        if timezone.now() > file.expiry_datetime:
            cleanup_file(file, reason=DeletedUpload.REASON_EXPIRED)
            return _expired_response(request)

        if file.download_count >= file.max_downloads:
            cleanup_file(file, reason=DeletedUpload.REASON_QUOTA)
            return _expired_response(request)

        FileUpload.objects.filter(pk=file.pk).update(
            download_count=F("download_count") + 1
        )
        file.refresh_from_db(fields=["download_count"])

    with open(file.file_path, "rb") as f:
        encrypted = f.read()

    try:
        decrypted_bytes = decrypt_file(encrypted)
    except (ValueError, FileNotFoundError):
        logger.exception("Failed to decrypt file %s", file.token)
        cleanup_file(file, reason=DeletedUpload.REASON_MANUAL)
        return _expired_response(request)

    response = HttpResponse(decrypted_bytes, content_type=file.file_type)
    response["Content-Disposition"] = f'attachment; filename="{file.file_name}"'

    # final allowed download -> clean up.
    if file.download_count >= file.max_downloads:
        cleanup_file(file, reason=DeletedUpload.REASON_QUOTA)

    return response


@login_required
def dashboard(request):
    #List the logged-in user's uploads and deleted uploads with status.
    uploads = (
        FileUpload.objects
        .filter(user=request.user)
        .order_by("-uploaded_at")
    )

    deleted_uploads = (
        DeletedUpload.objects
        .filter(user=request.user)
        .order_by("-deleted_at")
    )

    rows = []
    for upload in uploads:
        rows.append({
            "obj": upload,
            "spent": _is_spent(upload),
            "remaining": max(upload.max_downloads - upload.download_count, 0),
            "is_deleted": False,
        })

    for deleted_upload in deleted_uploads:
        rows.append({
            "obj": deleted_upload,
            "spent": True,  # Deleted uploads are always considered "spent"
            "remaining": 0,
            "is_deleted": True,
        })

    # Sort: active before deleted, then most-recent-touched first.
    rows.sort(key=lambda x: (
        x["is_deleted"],
        -x["obj"].uploaded_at.timestamp() if not x["is_deleted"] else -x["obj"].deleted_at.timestamp()
    ))

    return render(request, "dashboard.html", {"rows": rows})


@login_required
@require_POST
def delete_file(request, token):
    #Owner-only early delete. POST-only.
    try:
        file_obj = FileUpload.objects.get(token=token, user=request.user)
    except FileUpload.DoesNotExist:
        raise Http404("File not found.")

    cleanup_file(file_obj, reason=DeletedUpload.REASON_MANUAL)
    messages.success(request, f"Deleted “{file_obj.file_name}”.")
    return redirect("dashboard")
