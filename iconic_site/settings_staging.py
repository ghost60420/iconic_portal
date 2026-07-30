import os
from pathlib import Path

from .settings import *  # noqa: F401,F403


KPI_STAGING = True
DEBUG = False

_staging_host = os.environ["KPI_STAGING_HOST"].strip()
_staging_origin = f"https://{_staging_host}"
_staging_database = Path(os.environ["KPI_STAGING_DATABASE_PATH"]).expanduser()
_staging_static_root = Path(os.environ["KPI_STAGING_STATIC_ROOT"]).expanduser()
_staging_media_root = Path(os.environ["KPI_STAGING_MEDIA_ROOT"]).expanduser()
_staging_log_root = Path(os.environ["KPI_STAGING_LOG_ROOT"]).expanduser()

if not _staging_host or _staging_host in {"femline.ca", "www.femline.ca"}:
    raise RuntimeError("KPI_STAGING_HOST must identify the isolated staging host.")
if _staging_database.resolve() == (BASE_DIR / "db.sqlite3").resolve():
    raise RuntimeError("Staging cannot use the default CRM database.")
if len(SECRET_KEY) < 50:
    raise RuntimeError("Staging requires a strong DJANGO_SECRET_KEY.")

ALLOWED_HOSTS = [_staging_host]
CSRF_TRUSTED_ORIGINS = [_staging_origin]
SITE_BASE_URL = _staging_origin

DATABASES["default"]["NAME"] = _staging_database
STATIC_ROOT = _staging_static_root
MEDIA_ROOT = _staging_media_root

SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_HSTS_SECONDS = 3600
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# Staging must never contact an email server or a background task broker.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
EMAIL_HOST = ""
EMAIL_HOST_USER = ""
EMAIL_HOST_PASSWORD = ""
DEFAULT_FROM_EMAIL = "staging@example.invalid"
EMAIL_SYNC = {}
EMAIL_SYNC_PASSWORDS = {}
EMAIL_MONITOR = {}
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"
CELERY_TASK_ALWAYS_EAGER = False

# External marketing, AI, messaging, payment, and payroll capabilities are off.
MARKETING_ENABLED = True
MARKETING_SEO_ENABLED = False
MARKETING_SOCIAL_ENABLED = False
MARKETING_OUTREACH_ENABLED = False
MARKETING_ADS_ENABLED = False
MARKETING_AI_ENABLED = False
MARKETING_GOOGLE_CLIENT_ID = ""
MARKETING_GOOGLE_CLIENT_SECRET = ""
GOOGLE_CLIENT_ID = ""
GOOGLE_CLIENT_SECRET = ""
MARKETING_META_APP_ID = ""
MARKETING_META_APP_SECRET = ""
MARKETING_INSTAGRAM_APP_ID = ""
MARKETING_INSTAGRAM_APP_SECRET = ""
MARKETING_LINKEDIN_CLIENT_ID = ""
MARKETING_LINKEDIN_CLIENT_SECRET = ""
MARKETING_TIKTOK_CLIENT_KEY = ""
MARKETING_TIKTOK_CLIENT_SECRET = ""
OPENAI_API_KEY = ""

WHATSAPP_ENABLED = False
WHATSAPP_AUTOMATION_ENABLED = False
WHATSAPP_OUTBOUND_ENABLED = False
WA_AUTO_REPLY_ENABLED = False
WHATSAPP_BASE_URL = ""
WHATSAPP_API_KEY = ""
WHATSAPP_SENDER_NUMBER = ""
WHATSAPP_INFOBIP_WEBHOOK_TOKEN = ""
WHATSAPP_SERVICE_URL = ""
WHATSAPP_SERVICE_SECRET = ""
WHATSAPP_WEBHOOK_SECRET = ""
WA_TOKEN = ""
WA_PHONE_NUMBER_ID = ""
WA_VERIFY_TOKEN = ""
WA_APP_SECRET = ""
WA_WEB_GATEWAY_URL = ""
WA_WEB_API_KEY = ""
WA_WEB_INGEST_TOKEN = ""

INVOICE_PAYPAL_EMAIL = ""
INVOICE_BD_PAYPAL_EMAIL = ""
INVOICE_CA_PAYPAL_EMAIL = ""
INVOICE_BD_BANK_NAME = ""
INVOICE_BD_BANK_ACCOUNT_NAME = ""
INVOICE_BD_BANK_ACCOUNT_NUMBER = ""
INVOICE_BD_BANK_BRANCH = ""
INVOICE_BD_BANK_ROUTING = ""
INVOICE_BD_BANK_SWIFT = ""
INVOICE_CA_ETRANSFER_EMAIL = ""
INVOICE_CA_ETRANSFER_NAME = ""
INVOICE_CA_BANK_NAME = ""
INVOICE_CA_BANK_ACCOUNT_NAME = ""
INVOICE_CA_BANK_ACCOUNT_NUMBER = ""
INVOICE_CA_BANK_INSTITUTION = ""
INVOICE_CA_BANK_TRANSIT = ""
INVOICE_CA_BANK_SWIFT = ""
PAYROLL_INTEGRATION_ENABLED = False
PAYMENT_PROVIDER_ENABLED = False
SMS_ENABLED = False

KPI_AUTOMATION_MANUAL_ONLY = True
KPI_STAGING_AUTOMATION_AUDIT_LOG = (
    _staging_log_root / "kpi_automation_manual.jsonl"
)

_staging_log_root.mkdir(parents=True, exist_ok=True)
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "staging_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(_staging_log_root / "django.log"),
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 5,
            "formatter": "standard",
        },
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
        },
    },
    "root": {
        "handlers": ["staging_file", "console"],
        "level": "INFO",
    },
}
