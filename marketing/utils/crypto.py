import base64
import hashlib
import os
import shutil
import subprocess

try:
    from cryptography.fernet import Fernet, InvalidToken
except Exception:  # pragma: no cover - fallback for missing dependency
    Fernet = None
    InvalidToken = Exception


OPENSSL_BIN = shutil.which("openssl")
OPENSSL_ENV_KEY = "MARKETING_CRYPTO_PASSPHRASE"


def _derive_key(raw: str) -> bytes:
    raw = (raw or "").encode("utf-8")
    digest = hashlib.sha256(raw).digest()
    return base64.urlsafe_b64encode(digest)


def _get_key() -> bytes:
    key = os.getenv("MARKETING_ENCRYPTION_KEY") or os.getenv("DJANGO_SECRET_KEY") or ""
    return _derive_key(key)


def _get_passphrase() -> str:
    raw = os.getenv("MARKETING_ENCRYPTION_KEY") or os.getenv("DJANGO_SECRET_KEY") or ""
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()


def _get_fernet():
    if Fernet is None:
        return None
    return Fernet(_get_key())


def _run_openssl(args: list[str], value: str) -> str:
    if not OPENSSL_BIN:
        raise RuntimeError("OpenSSL is not available for token encryption.")
    env = os.environ.copy()
    env[OPENSSL_ENV_KEY] = _get_passphrase()
    result = subprocess.run(
        [OPENSSL_BIN, *args, "-pass", f"env:{OPENSSL_ENV_KEY}"],
        input=value.encode("utf-8"),
        capture_output=True,
        check=True,
        env=env,
    )
    return result.stdout.decode("utf-8").strip()


def encrypt_value(value: str) -> str:
    value = value or ""
    if not value:
        return ""
    f = _get_fernet()
    if not f:
        return "openssl::" + _run_openssl(["enc", "-aes-256-cbc", "-pbkdf2", "-salt", "-a", "-A"], value)
    token = f.encrypt(value.encode("utf-8")).decode("utf-8")
    return "fernet::" + token


def decrypt_value(token: str) -> str:
    token = token or ""
    if not token:
        return ""
    if token.startswith("fernet::"):
        raw_token = token.split("::", 1)[1]
        f = _get_fernet()
        if not f:
            return ""
        try:
            return f.decrypt(raw_token.encode("utf-8")).decode("utf-8")
        except InvalidToken:
            return ""
    if token.startswith("openssl::"):
        raw_token = token.split("::", 1)[1]
        try:
            return _run_openssl(["enc", "-aes-256-cbc", "-pbkdf2", "-salt", "-a", "-A", "-d"], raw_token)
        except Exception:
            return ""

    f = _get_fernet()
    if f:
        try:
            return f.decrypt(token.encode("utf-8")).decode("utf-8")
        except InvalidToken:
            pass
    try:
        return _run_openssl(["enc", "-aes-256-cbc", "-pbkdf2", "-salt", "-a", "-A", "-d"], token)
    except Exception:
        return token
