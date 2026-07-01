"""
Helpers for at-rest encryption of uploaded files.

The Fernet key lives in `secret.key` next to the project root. It must
be created via the management command before the app can encrypt anything:

    python manage.py generate_key
"""

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


KEY_FILE = settings.BASE_DIR / "secret.key"


def generate_key() -> bytes:
    """Mint a new Fernet key and write it to KEY_FILE."""
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    KEY_FILE.write_bytes(key)
    return key


def load_key() -> bytes:
    """Read the existing Fernet key. Raises FileNotFoundError if missing."""
    return KEY_FILE.read_bytes()


def get_fernet() -> Fernet:
    """Return a Fernet instance backed by the key file.

    Raises FileNotFoundError if the key has not been generated yet — the
    caller is expected to handle this (the views return a 500 with a
    helpful message; tests can monkeypatch this).
    """
    return Fernet(load_key())


def encrypt_file(file_bytes: bytes) -> bytes:
    return get_fernet().encrypt(file_bytes)


def decrypt_file(encrypted_bytes: bytes) -> bytes:
    """Decrypt; raises InvalidToken if the bytes weren't encrypted with our key
    or were tampered with."""
    try:
        return get_fernet().decrypt(encrypted_bytes)
    except InvalidToken as exc:
        raise ValueError("Encrypted payload is invalid or was tampered with.") from exc