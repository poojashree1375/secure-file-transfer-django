import os
import tempfile
from datetime import timedelta
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from cryptography.fernet import Fernet

from uploader.crypto_utils import (
    KEY_FILE,
    decrypt_file,
    encrypt_file,
    generate_key,
)
from uploader.cleanup import cleanup_file, delete_expired
from uploader.models import FileUpload


# Tests write media files to a temp directory so they don't pollute the
# real media/ folder.
TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="sft-test-media-")


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class EncryptionRoundTripTest(TestCase):
    def setUp(self):
        # Make sure a key exists for the test run.
        if not KEY_FILE.exists():
            generate_key()

    def test_round_trip(self):
        original = b"hello world, this is a small test payload"
        encrypted = encrypt_file(original)
        # Fernet ciphertext is not equal to plaintext.
        self.assertNotEqual(encrypted, original)
        self.assertEqual(decrypt_file(encrypted), original)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class UploadFormValidationTest(TestCase):
    def test_disallowed_extension_rejected(self):
        f = SimpleUploadedFile("malware.exe", b"MZ\x00\x00", content_type="application/octet-stream")
        response = self.client.post(reverse("upload"), {
            "file": f, "expiry_minutes": 5, "max_downloads": 1,
        })
        # Form invalid → response re-renders upload page (200), no DB row.
        self.assertEqual(response.status_code, 200)
        self.assertEqual(FileUpload.objects.count(), 0)

    def test_oversized_file_rejected(self):
        # 11 MB of zeros.
        big = b"\x00" * (11 * 1024 * 1024)
        f = SimpleUploadedFile("big.pdf", big, content_type="application/pdf")
        response = self.client.post(reverse("upload"), {
            "file": f, "expiry_minutes": 5, "max_downloads": 1,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(FileUpload.objects.count(), 0)

    def test_valid_pdf_accepted(self):
        # Minimal valid PDF header.
        pdf = b"%PDF-1.4\n%fake\n%%EOF\n"
        f = SimpleUploadedFile("hello.pdf", pdf, content_type="application/pdf")
        response = self.client.post(reverse("upload"), {
            "file": f, "expiry_minutes": 5, "max_downloads": 1,
        })
        # Successful upload redirects to show_link → 302.
        self.assertEqual(response.status_code, 302)
        self.assertEqual(FileUpload.objects.count(), 1)


def _make_upload(token="tok", max_downloads=1, expiry_minutes=5, filename="a.pdf"):
    """Helper: create a FileUpload row + encrypted blob on disk."""
    if not KEY_FILE.exists():
        generate_key()

    payload = b"%PDF-1.4\nhello\n%%EOF\n"
    encrypted = encrypt_file(payload)
    blob_path = os.path.join(TEST_MEDIA_ROOT, "uploads", f"{token}.enc")
    os.makedirs(os.path.dirname(blob_path), exist_ok=True)
    with open(blob_path, "wb") as fh:
        fh.write(encrypted)

    obj = FileUpload.objects.create(
        token=token,
        file_name=filename,
        file_path=blob_path,
        file_type="application/pdf",
        expiry_datetime=timezone.now() + timedelta(minutes=expiry_minutes),
        max_downloads=max_downloads,
    )
    return obj, payload


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class DownloadLimitTest(TestCase):
    def setUp(self):
        if not KEY_FILE.exists():
            generate_key()

    def test_download_decrements_quota_then_blocks(self):
        obj, _ = _make_upload(max_downloads=2)

        # First download works.
        r1 = self.client.get(reverse("download_file", args=[obj.token]))
        self.assertEqual(r1.status_code, 200)
        fresh = FileUpload.objects.get(pk=obj.pk)
        self.assertEqual(fresh.download_count, 1)

        # Second download works AND triggers cleanup because we hit max.
        r2 = self.client.get(reverse("download_file", args=[obj.token]))
        self.assertEqual(r2.status_code, 200)
        self.assertFalse(FileUpload.objects.filter(pk=obj.pk).exists())
        self.assertFalse(os.path.exists(obj.file_path))

        # Third download — already gone, returns expired.
        r3 = self.client.get(reverse("download_file", args=[obj.token]))
        self.assertEqual(r3.status_code, 200)
        self.assertTemplateUsed(r3, "expired.html")


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class ExpiryTest(TestCase):
    def setUp(self):
        if not KEY_FILE.exists():
            generate_key()

    def test_expired_download_returns_expired_and_cleans_up(self):
        obj, _ = _make_upload()
        # Backdate expiry.
        FileUpload.objects.filter(pk=obj.pk).update(
            expiry_datetime=timezone.now() - timedelta(minutes=1)
        )

        response = self.client.get(reverse("download_file", args=[obj.token]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "expired.html")
        self.assertFalse(FileUpload.objects.filter(pk=obj.pk).exists())

    def test_delete_expired_cleans_past_due_files(self):
        live, _ = _make_upload(token="live", expiry_minutes=5)
        dead, _ = _make_upload(token="dead", expiry_minutes=5)
        FileUpload.objects.filter(pk=dead.pk).update(
            expiry_datetime=timezone.now() - timedelta(minutes=1)
        )

        delete_expired()

        self.assertTrue(FileUpload.objects.filter(pk=live.pk).exists())
        self.assertFalse(FileUpload.objects.filter(pk=dead.pk).exists())
        self.assertFalse(os.path.exists(dead.file_path))


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class ShowLinkExpiredTest(TestCase):
    def setUp(self):
        if not KEY_FILE.exists():
            generate_key()

    def test_show_link_renders_expired_for_past_due(self):
        obj, _ = _make_upload()
        FileUpload.objects.filter(pk=obj.pk).update(
            expiry_datetime=timezone.now() - timedelta(minutes=1)
        )

        response = self.client.get(reverse("show_link", args=[obj.token]))
        self.assertTemplateUsed(response, "expired.html")
        self.assertFalse(FileUpload.objects.filter(pk=obj.pk).exists())

    def test_show_link_renders_expired_when_quota_spent(self):
        obj, _ = _make_upload(max_downloads=1)
        FileUpload.objects.filter(pk=obj.pk).update(download_count=1)

        response = self.client.get(reverse("show_link", args=[obj.token]))
        self.assertTemplateUsed(response, "expired.html")
        self.assertFalse(FileUpload.objects.filter(pk=obj.pk).exists())