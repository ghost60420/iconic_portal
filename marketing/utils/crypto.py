import base64
import hashlib
import os

try:
    from cryptography.fernet import Fernet, InvalidToken
except Exception:  # pragma: no cover - fallback for missing dependency
    Fernet = None
    InvalidToken = Exception


def _derive_key(raw: str) -> bytes:
    raw = (raw or "").encode("utf-8")
    digest = hashlib.sha256(raw).digest()
    return base64.urlsafe_b64encode(digest)


def _get_key() -> bytes:
    key = os.getenv("MARKETING_ENCRYPTION_KEY") or os.getenv("DJANGO_SECRET_KEY") or ""
    return _derive_key(key)


def _get_fernet():
    if Fernet is None:
        return None
    return Fernet(_get_key())


def encrypt_value(value: str) -> str:
    value = value or ""
    f = _get_fernet()
    if not f:
        return value
    token = f.encrypt(value.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_value(token: str) -> str:
    token = token or ""
    f = _get_fernet()
    if not f:
        return token
    try:
        return f.decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return ""
