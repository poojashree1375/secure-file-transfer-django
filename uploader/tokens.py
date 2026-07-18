import base64
import uuid


def generate_token() -> str:
    """Return a 22-character URL-safe token (base62-style, no padding).

    Generated from a UUID4 (128 random bits) and stripped of `=` padding so
    it can appear in a URL or a QR code without escaping. Same security as
    a raw UUID4, just denser encoding.
    """
    raw = uuid.uuid4().bytes  # 16 random bytes
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
