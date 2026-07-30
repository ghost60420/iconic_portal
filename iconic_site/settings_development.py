import os
from pathlib import Path

os.environ.setdefault("DJANGO_DEBUG", "1")
os.environ.setdefault(
    "DJANGO_SECRET_KEY",
    "unsafe-development-only-kpi-integration",
)

from .settings import *  # noqa: F401,F403


DEVELOPMENT_DATABASE_PATH = Path(
    os.getenv(
        "DJANGO_DEVELOPMENT_DB_PATH",
        BASE_DIR / "kpi_development.sqlite3",
    )
).expanduser()
DATABASES["default"]["NAME"] = DEVELOPMENT_DATABASE_PATH
SITE_BASE_URL = os.getenv("SITE_BASE_URL", "http://127.0.0.1:8010")

# Development integration must not contact live providers or background workers.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
EMAIL_HOST = ""
EMAIL_HOST_USER = ""
EMAIL_HOST_PASSWORD = ""
DEFAULT_FROM_EMAIL = "development@example.invalid"
EMAIL_SYNC = {}
EMAIL_SYNC_PASSWORDS = {}
EMAIL_MONITOR = {}
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"
CELERY_TASK_ALWAYS_EAGER = False

# Keep the existing local dashboard available for regression testing. Every
# external capability and credential remains disabled below.
MARKETING_ENABLED = True
MARKETING_SEO_ENABLED = False
MARKETING_SOCIAL_ENABLED = False
MARKETING_OUTREACH_ENABLED = False
MARKETING_ADS_ENABLED = False
MARKETING_AI_ENABLED = False
MARKETING_GOOGLE_CLIENT_ID = ""
MARKETING_GOOGLE_CLIENT_SECRET = ""
MARKETING_META_APP_ID = ""
MARKETING_META_APP_SECRET = ""
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
WHATSAPP_SERVICE_URL = ""
WHATSAPP_SERVICE_SECRET = ""
WA_TOKEN = ""
WA_APP_SECRET = ""
WA_WEB_GATEWAY_URL = ""
WA_WEB_API_KEY = ""
WA_WEB_INGEST_TOKEN = ""

# The CRM records payments; this development profile does not connect a
# payment or payroll provider.
INVOICE_PAYPAL_EMAIL = ""
INVOICE_BD_PAYPAL_EMAIL = ""
INVOICE_CA_PAYPAL_EMAIL = ""
