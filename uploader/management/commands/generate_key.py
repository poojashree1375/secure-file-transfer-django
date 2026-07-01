"""
Management command to create the Fernet key used for encrypting uploads.

Run this once after cloning the project:
    python manage.py generate_key

Re-running is safe — it refuses to overwrite an existing key file.
"""

from django.core.management.base import BaseCommand

from cryptography.fernet import Fernet

from uploader.crypto_utils import KEY_FILE


class Command(BaseCommand):
    help = "Generate a new Fernet key for encrypting uploaded files."

    def handle(self, *args, **options):
        if KEY_FILE.exists():
            self.stdout.write(
                self.style.WARNING(
                    f"Key already exists at {KEY_FILE}. Refusing to overwrite."
                )
            )
            return

        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        KEY_FILE.write_bytes(Fernet.generate_key())

        self.stdout.write(
            self.style.SUCCESS(f"Generated new key at {KEY_FILE}")
        )