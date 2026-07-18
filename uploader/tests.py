import os
import tempfile
from datetime import timedelta
from io import BytesIO

from django.contrib.auth import get_user_model
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
from uploader.models import FileUpload, DeletedUpload

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="sft-test-media-")

User = get_user_model()


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
    def setUp(self):
        self.user = User.objects.create_user(
            username="tester",
            email="tester@example.com",
            password="x",
        )
        self.client.force_login(self.user)

    def test_disallowed_extension_rejected(self):
        f = SimpleUploadedFile("malware.exe", b"MZ\x00\x00", content_type="application/octet-stream")
        response = self.client.post(reverse("upload"), {
            "file": f, "expiry_minutes": 5, "max_downloads": 1,
        })
        # Form invalid -> response re-renders upload page (200), no DB row.
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
        # Successful upload redirects to show_link -> 302.
        self.assertEqual(response.status_code, 302)
        self.assertEqual(FileUpload.objects.count(), 1)


def _make_upload(token="tok", max_downloads=1, expiry_minutes=5, filename="a.pdf", user=None):
    #Helper: create a FileUpload row + encrypted blob on disk.
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
        user=user,
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

        # Second download works AND triggers cleanup because it hit max.
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


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class DashboardTest(TestCase):
    def setUp(self):
        if not KEY_FILE.exists():
            generate_key()
        # Two distinct users to can verify isolation.
        self.alice = User.objects.create_user(
            username="alice", email="alice@example.com", password="x",
        )
        self.bob = User.objects.create_user(
            username="bob", email="bob@example.com", password="x",
        )

    def test_anonymous_redirects_to_login(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("account_login"), response.url)

    def test_shows_only_my_uploads(self):
        mine, _ = _make_upload(token="mine", user=self.alice)
        _make_upload(token="theirs", user=self.bob)

        self.client.force_login(self.alice)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dashboard.html")
        body = response.content.decode()
        self.assertIn("mine", body)
        self.assertNotIn("theirs", body)

    def test_status_and_remaining_rendered(self):
        # Live file, 1 of 3 downloads used.
        live, _ = _make_upload(
            token="live", user=self.alice, max_downloads=3, filename="live.pdf",
        )
        FileUpload.objects.filter(pk=live.pk).update(download_count=1)

        # Spent file — quota hit.
        quota, _ = _make_upload(
            token="quota", user=self.alice, max_downloads=1, filename="quota.pdf",
        )
        FileUpload.objects.filter(pk=quota.pk).update(download_count=1)

        # Spent file — expired.
        expired, _ = _make_upload(
            token="expired", user=self.alice, filename="expired.pdf",
        )
        FileUpload.objects.filter(pk=expired.pk).update(
            expiry_datetime=timezone.now() - timedelta(minutes=1)
        )

        self.client.force_login(self.alice)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)

        # Live: 1 / 3 + "Live" badge + "Copy link" button.
        body = response.content.decode()
        self.assertIn("1 / 3", body)
        self.assertIn("Live", body)
        self.assertIn("Copy link", body)

        # Quota: 1 / 1 + "Quota spent" badge, no Copy link.
        self.assertIn("Quota spent", body)

        # Expired: "Expired" badge, no Copy link.
        self.assertIn("Expired", body)

    def test_delete_removes_row_and_blob(self):
        obj, _ = _make_upload(token="del", user=self.alice)
        blob_path = obj.file_path
        self.assertTrue(os.path.exists(blob_path))

        self.client.force_login(self.alice)
        response = self.client.post(
            reverse("delete_file", args=[obj.token])
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard"))
        self.assertFalse(FileUpload.objects.filter(pk=obj.pk).exists())
        self.assertFalse(os.path.exists(blob_path))

    def test_delete_other_users_file_returns_404(self):
        obj, _ = _make_upload(token="not-mine", user=self.bob)

        self.client.force_login(self.alice)
        response = self.client.post(
            reverse("delete_file", args=[obj.token])
        )
        self.assertEqual(response.status_code, 404)
        # Bob's file must still exist, Alice must not be able to delete it.
        self.assertTrue(FileUpload.objects.filter(pk=obj.pk).exists())

    def test_delete_get_request_is_rejected(self):
        # require_POST should bounce GETs.
        obj, _ = _make_upload(token="getonly", user=self.alice)
        self.client.force_login(self.alice)
        response = self.client.get(
            reverse("delete_file", args=[obj.token])
        )
        self.assertEqual(response.status_code, 405)
        self.assertTrue(FileUpload.objects.filter(pk=obj.pk).exists())

    def test_upload_sets_user(self):
        # Hit the real upload view as alice and verify the FK is set.
        self.client.force_login(self.alice)
        pdf = b"%PDF-1.4\nhello\n%%EOF\n"
        f = SimpleUploadedFile("hello.pdf", pdf, content_type="application/pdf")
        response = self.client.post(reverse("upload"), {
            "file": f, "expiry_minutes": 5, "max_downloads": 1,
        })
        self.assertEqual(response.status_code, 302)

        uploaded = FileUpload.objects.get()
        self.assertEqual(uploaded.user, self.alice)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class DeletedUploadTest(TestCase):
    def setUp(self):
        if not KEY_FILE.exists():
            generate_key()
        self.user = User.objects.create_user(
            username="tester", email="tester@example.com", password="x",
        )
        self.client.force_login(self.user)

    def test_manual_creates_deleted_upload(self):
        #Test that manual deletion creates a DeletedUpload with reason=manual.
        # Create a file to delete
        payload = b"%PDF-1.4\nhello\n%%EOF\n"
        f = SimpleUploadedFile("test.pdf", payload, content_type="application/pdf")
        response = self.client.post(reverse("upload"), {
            "file": f, "expiry_minutes": 5, "max_downloads": 1,
        })
        self.assertEqual(response.status_code, 302)
        upload = FileUpload.objects.get()

        # Delete the file
        response = self.client.post(reverse("delete_file", args=[upload.token]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard"))

        # Check that FileUpload is gone
        self.assertFalse(FileUpload.objects.filter(pk=upload.pk).exists())

        # Check that DeletedUpload was created with correct reason
        deleted = DeletedUpload.objects.get(token=upload.token)
        self.assertEqual(deleted.reason, DeletedUpload.REASON_MANUAL)
        self.assertEqual(deleted.file_name, "test.pdf")
        self.assertEqual(deleted.user, self.user)
        self.assertEqual(deleted.max_downloads, 1)
        self.assertEqual(deleted.download_count, 0)

    def test_expired_creates_deleted_upload(self):
        #Test that expiry deletion creates a DeletedUpload with reason=expired.
        # Create a file that's already expired
        payload = b"%PDF-1.4\nhello\n%%EOF\n"
        f = SimpleUploadedFile("test.pdf", payload, content_type="application/pdf")
        response = self.client.post(reverse("upload"), {
            "file": f, "expiry_minutes": 5, "max_downloads": 1,
        })
        self.assertEqual(response.status_code, 302)
        upload = FileUpload.objects.get()

        # Manually make it expired
        FileUpload.objects.filter(pk=upload.pk).update(
            expiry_datetime=timezone.now() - timedelta(minutes=1)
        )

        # Try to download it -> should trigger cleanup due to expiry
        response = self.client.get(reverse("download_file", args=[upload.token]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "expired.html")

        # Check that FileUpload is gone
        self.assertFalse(FileUpload.objects.filter(pk=upload.pk).exists())

        # Check that DeletedUpload was created with correct reason
        deleted = DeletedUpload.objects.get(token=upload.token)
        self.assertEqual(deleted.reason, DeletedUpload.REASON_EXPIRED)
        self.assertEqual(deleted.file_name, "test.pdf")
        self.assertEqual(deleted.user, self.user)

    def test_quota_creates_deleted_upload(self):
        #Test that quota exhaustion creates a DeletedUpload with reason=quota.
        # Create a file with max_downloads=1
        payload = b"%PDF-1.4\nhello\n%%EOF\n"
        f = SimpleUploadedFile("test.pdf", payload, content_type="application/pdf")
        response = self.client.post(reverse("upload"), {
            "file": f, "expiry_minutes": 5, "max_downloads": 1,
        })
        self.assertEqual(response.status_code, 302)
        upload = FileUpload.objects.get()

        # First download -> should succeed
        response1 = self.client.get(reverse("download_file", args=[upload.token]))
        self.assertEqual(response1.status_code, 200)

        # Second download -> should trigger cleanup due to quota
        response2 = self.client.get(reverse("download_file", args=[upload.token]))
        self.assertEqual(response2.status_code, 200)
        self.assertTemplateUsed(response2, "expired.html")

        # Check that FileUpload is gone
        self.assertFalse(FileUpload.objects.filter(pk=upload.pk).exists())

        # Check that DeletedUpload was created with correct reason
        deleted = DeletedUpload.objects.get(token=upload.token)
        self.assertEqual(deleted.reason, DeletedUpload.REASON_QUOTA)
        self.assertEqual(deleted.file_name, "test.pdf")
        self.assertEqual(deleted.user, self.user)
        self.assertEqual(deleted.download_count, 1)

    def test_decryption_failure_creates_deleted_upload(self):
        #Test that decryption failure creates a DeletedUpload with reason=manual.
        # Create a file
        payload = b"%PDF-1.4\nhello\n%%EOF\n"
        f = SimpleUploadedFile("test.pdf", payload, content_type="application/pdf")
        response = self.client.post(reverse("upload"), {
            "file": f, "expiry_minutes": 5, "max_downloads": 1,
        })
        self.assertEqual(response.status_code, 302)
        upload = FileUpload.objects.get()

        # Corrupt the encrypted file to cause decryption failure
        with open(upload.file_path, "r+b") as fh:
            fh.write(b"corrupted data")

        # Try to download it -> should trigger cleanup due to decryption failure
        response = self.client.get(reverse("download_file", args=[upload.token]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "expired.html")

        # Check that FileUpload is gone
        self.assertFalse(FileUpload.objects.filter(pk=upload.pk).exists())

        # Check that DeletedUpload was created with correct reason
        deleted = DeletedUpload.objects.get(token=upload.token)
        self.assertEqual(deleted.reason, DeletedUpload.REASON_MANUAL)
        self.assertEqual(deleted.file_name, "test.pdf")
        self.assertEqual(deleted.user, self.user)

    def test_dashboard_shows_deleted_uploads(self):
        #Test that dashboard shows both active and deleted uploads.
        # Create an active file
        payload = b"%PDF-1.4\nhello\n%%EOF\n"
        f = SimpleUploadedFile("active.pdf", payload, content_type="application/pdf")
        response = self.client.post(reverse("upload"), {
            "file": f, "expiry_minutes": 5, "max_downloads": 1,
        })
        self.assertEqual(response.status_code, 302)
        active_upload = FileUpload.objects.get()

        # Create a deleted file by manually creating a DeletedUpload
        deleted_upload = DeletedUpload.objects.create(
            token="deleted-token-123",
            file_name="deleted.pdf",
            file_type="application/pdf",
            max_downloads=1,
            download_count=0,
            user=self.user,
            uploaded_at=timezone.now() - timedelta(days=1),
            reason=DeletedUpload.REASON_MANUAL,
        )

        # Access dashboard
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dashboard.html")

        # Check that both files appear in the dashboard
        body = response.content.decode()
        self.assertIn("active.pdf", body)
        self.assertIn("deleted.pdf", body)

        # Check that the deleted file shows as deleted
        self.assertIn("Deleted by you", body)  # Reason display for manual
