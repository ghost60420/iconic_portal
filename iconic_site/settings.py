# iconic_site/settings.py
import os
import json
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# ======================
# Core Django settings
# ======================

DEBUG = os.getenv("DJANGO_DEBUG", "0") == "1"
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "change_this_in_env"
    else:
        raise RuntimeError("DJANGO_SECRET_KEY is missing. Set it in .env")

# ======================
# Hosts
# ======================

ALLOWED_HOSTS = [
    "127.0.0.1",
    "localhost",
    "3.84.200.98",
    "femline.ca",
    "www.femline.ca",
]
_extra_hosts = os.getenv("DJANGO_ALLOWED_HOSTS", "")
if _extra_hosts:
    ALLOWED_HOSTS.extend([h.strip() for h in _extra_hosts.split(",") if h.strip()])
    ALLOWED_HOSTS = list(dict.fromkeys(ALLOWED_HOSTS))

CSRF_TRUSTED_ORIGINS = [
    "https://3.84.200.98",
    "https://femline.ca",
    "https://www.femline.ca",
]

# ======================
# Applications
# ======================

INSTALLED_APPS = [
    "jazzmin",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "crm.apps.CrmConfig",
    "aihub",
    "leadbrain.apps.LeadbrainConfig",
    "marketing.apps.MarketingConfig",
    "whatsapp.apps.WhatsAppConfig",
]

MIDDLEWARE = [
    "crm.middleware.ExceptionLoggingMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "iconic_site.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "marketing.context_processors.marketing_flags",
                "whatsapp.context_processors.whatsapp_flags",
            ],
        },
    },
]

WSGI_APPLICATION = "iconic_site.wsgi.application"

# ======================
# Database
# ======================

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# ======================
# Password validation
# ======================

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ======================
# Internationalization
# ======================

LANGUAGE_CODE = "en-us"
TIME_ZONE = "America/Vancouver"
USE_I18N = True
USE_TZ = True

# ======================
# Static and media
# ======================

STATIC_URL = "/static/"

STATIC_DIR = BASE_DIR / "static"
STATICFILES_DIRS = [STATIC_DIR] if STATIC_DIR.exists() else []

# IMPORTANT: this fixes collectstatic and lets nginx serve /static/
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ======================
# Auth redirects
# ======================
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/main-dashboard/"
LOGOUT_REDIRECT_URL = "/accounts/login/"

# ======================
# Role based access constants
# ======================

BD_TEAM_GROUP = "BD_TEAM"
CA_TEAM_GROUP = "CA_TEAM"

# Bangladesh team cannot access CA accounting module
CA_ACCOUNTING_GROUP = "CA_ACCOUNTING"

# ======================
# OpenAI settings
# ======================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

# ======================
# Marketing feature flags
# ======================

def _flag(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


MARKETING_ENABLED = _flag("MARKETING_ENABLED", default=DEBUG)
MARKETING_SEO_ENABLED = _flag("MARKETING_SEO_ENABLED", default=False)
MARKETING_SOCIAL_ENABLED = _flag("MARKETING_SOCIAL_ENABLED", default=False)
MARKETING_OUTREACH_ENABLED = _flag("MARKETING_OUTREACH_ENABLED", default=False)
MARKETING_ADS_ENABLED = _flag("MARKETING_ADS_ENABLED", default=False)
MARKETING_AI_ENABLED = _flag("MARKETING_AI_ENABLED", default=False)

SITE_BASE_URL = os.getenv("SITE_BASE_URL", "https://femline.ca")

# ======================
# Meta (Facebook/Instagram) OAuth
# ======================

MARKETING_META_APP_ID = os.getenv("MARKETING_META_APP_ID", "")
MARKETING_META_APP_SECRET = os.getenv("MARKETING_META_APP_SECRET", "")
MARKETING_META_REDIRECT_URI = os.getenv(
    "MARKETING_META_REDIRECT_URI",
    f"{SITE_BASE_URL}/marketing/oauth/meta/callback/",
)
_meta_scopes_raw = os.getenv(
    "MARKETING_META_SCOPES",
    "pages_show_list,pages_read_engagement,read_insights,instagram_basic,instagram_manage_insights,business_management",
)
MARKETING_META_SCOPES = [scope.strip() for scope in _meta_scopes_raw.split(",") if scope.strip()]

# ======================
# Email settings (SMTP)
# ======================

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = True
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", EMAIL_HOST_USER)

# ======================
# Email sync config (IMAP)
# ======================

EMAIL_SYNC = {
    "LEAD_INBOX": {
        "label": "lead",
        "imap_host": os.getenv("LEAD_IMAP_HOST", "imap.gmail.com"),
        "imap_port": int(os.getenv("LEAD_IMAP_PORT", "993")),
        "username": os.getenv("LEAD_EMAIL_USER", ""),
        "password": os.getenv("LEAD_EMAIL_PASS", ""),
        "use_ssl": os.getenv("LEAD_IMAP_SSL", "1") == "1",
    },
    "INFO_INBOX": {
        "label": "info",
        "imap_host": os.getenv("INFO_IMAP_HOST", "imap.gmail.com"),
        "imap_port": int(os.getenv("INFO_IMAP_PORT", "993")),
        "username": os.getenv("INFO_EMAIL_USER", ""),
        "password": os.getenv("INFO_EMAIL_PASS", ""),
        "use_ssl": os.getenv("INFO_IMAP_SSL", "1") == "1",
    },
}

EMAIL_SYNC_PASSWORDS = {
    "lead": os.getenv("LEAD_EMAIL_PASS", ""),
    "info": os.getenv("INFO_EMAIL_PASS", ""),
}

# ======================
# Email monitor rules
# ======================

EMAIL_MONITOR = {
    "monitor_label": "info",
    "form_subject_contains": [
        "new form entry",
        "contact form",
        "brand development",
        "new form",
        "form entry",
    ],
    "sale_keywords": [
        "quote",
        "pricing",
        "price",
        "cost",
        "moq",
        "sample",
        "order",
        "production",
        "bulk",
        "custom",
        "private label",
        "factory",
        "timeline",
    ],
}

# ======================
# WhatsApp settings (Meta Cloud API)
# ======================

WHATSAPP_PROVIDER = os.getenv("WHATSAPP_PROVIDER", "meta").strip().lower()
WHATSAPP_BASE_URL = os.getenv("WHATSAPP_BASE_URL", "")
WHATSAPP_API_KEY = os.getenv("WHATSAPP_API_KEY", "")
WHATSAPP_SENDER_NUMBER = os.getenv("WHATSAPP_SENDER_NUMBER", "")
WHATSAPP_INFOBIP_WEBHOOK_TOKEN = os.getenv("WHATSAPP_INFOBIP_WEBHOOK_TOKEN", "")
WHATSAPP_INFOBIP_TEMPLATE_LANG = os.getenv("WHATSAPP_INFOBIP_TEMPLATE_LANG", "en")
_wa_templates_raw = os.getenv("WHATSAPP_INFOBIP_TEMPLATES_JSON", "[]")
try:
    WHATSAPP_INFOBIP_TEMPLATES = json.loads(_wa_templates_raw)
except Exception:
    WHATSAPP_INFOBIP_TEMPLATES = []

WA_TOKEN = os.getenv("WA_TOKEN", "")
WA_PHONE_NUMBER_ID = os.getenv("WA_PHONE_NUMBER_ID", "")
WA_VERIFY_TOKEN = os.getenv("WA_VERIFY_TOKEN", "")
WA_APP_SECRET = os.getenv("WA_APP_SECRET", "")
WA_AUTO_REPLY_ENABLED = os.getenv("WA_AUTO_REPLY_ENABLED", "1") == "1"
WA_WEB_GATEWAY_URL = os.getenv("WA_WEB_GATEWAY_URL", "")
WA_WEB_API_KEY = os.getenv("WA_WEB_API_KEY", "")
WA_WEB_INGEST_TOKEN = os.getenv("WA_WEB_INGEST_TOKEN", "")

# ======================
# WhatsApp Web settings (QR login)
# ======================

WHATSAPP_ENABLED = os.getenv("WHATSAPP_ENABLED", "0") == "1"
WHATSAPP_AUTOMATION_ENABLED = os.getenv("WHATSAPP_AUTOMATION_ENABLED", "0") == "1"
WHATSAPP_OUTBOUND_ENABLED = os.getenv("WHATSAPP_OUTBOUND_ENABLED", "0") == "1"

WHATSAPP_PHONE_NUMBER = os.getenv("WHATSAPP_PHONE_NUMBER", "6045006009")
WHATSAPP_SERVICE_URL = os.getenv("WHATSAPP_SERVICE_URL", "http://127.0.0.1:3127")
WHATSAPP_SERVICE_SECRET = os.getenv("WHATSAPP_SERVICE_SECRET", "")
WHATSAPP_WEBHOOK_SECRET = os.getenv("WHATSAPP_WEBHOOK_SECRET", "")
WHATSAPP_SESSION_PATH = os.getenv("WHATSAPP_SESSION_PATH", "/var/lib/iconic_whatsapp")

WHATSAPP_DAILY_LIMIT = int(os.getenv("WHATSAPP_DAILY_LIMIT", "120"))
WHATSAPP_HOURLY_LIMIT = int(os.getenv("WHATSAPP_HOURLY_LIMIT", "20"))
WHATSAPP_CONTACT_DAILY_LIMIT = int(os.getenv("WHATSAPP_CONTACT_DAILY_LIMIT", "3"))

_wa_hours_raw = os.getenv("WHATSAPP_BUSINESS_HOURS_JSON", '{"start":"09:00","end":"17:00"}')
try:
    WHATSAPP_BUSINESS_HOURS_JSON = json.loads(_wa_hours_raw)
except Exception:
    WHATSAPP_BUSINESS_HOURS_JSON = {"start": "09:00", "end": "17:00"}

# ======================
# Invoice settings
# ======================

INVOICE_COMPANY_NAME = os.getenv("INVOICE_COMPANY_NAME", "Iconic Apparel House")
INVOICE_COMPANY_EMAIL = os.getenv("INVOICE_COMPANY_EMAIL", "info@iconicapparelhouse.com")
INVOICE_COMPANY_PHONE = os.getenv("INVOICE_COMPANY_PHONE", "604-500-6009")
INVOICE_COMPANY_WEBSITE = os.getenv("INVOICE_COMPANY_WEBSITE", "iconicapparelhouse.com")
INVOICE_LOGO_PATH = os.getenv("INVOICE_LOGO_PATH", "img/image.png")

INVOICE_ADDRESS_BD = os.getenv("INVOICE_ADDRESS_BD", "")
INVOICE_TAX_LABEL_BD = os.getenv("INVOICE_TAX_LABEL_BD", "VAT / BIN")
INVOICE_TAX_ID_BD = os.getenv("INVOICE_TAX_ID_BD", "")

INVOICE_ADDRESS_CA = os.getenv("INVOICE_ADDRESS_CA", "")
INVOICE_TAX_LABEL_CA = os.getenv("INVOICE_TAX_LABEL_CA", "GST / HST")
INVOICE_TAX_ID_CA = os.getenv("INVOICE_TAX_ID_CA", "")

INVOICE_POLICY_BD = os.getenv(
    "INVOICE_POLICY_BD",
    "Please refer to iconicapparelhouse.com for full terms and conditions.",
)
INVOICE_POLICY_CA = os.getenv(
    "INVOICE_POLICY_CA",
    "Please refer to iconicapparelhouse.com for full terms and conditions.",
)

INVOICE_PAYPAL_EMAIL = os.getenv("INVOICE_PAYPAL_EMAIL", "")

INVOICE_BD_BANK_NAME = os.getenv("INVOICE_BD_BANK_NAME", "")
INVOICE_BD_BANK_ACCOUNT_NAME = os.getenv("INVOICE_BD_BANK_ACCOUNT_NAME", "")
INVOICE_BD_BANK_ACCOUNT_NUMBER = os.getenv("INVOICE_BD_BANK_ACCOUNT_NUMBER", "")
INVOICE_BD_BANK_BRANCH = os.getenv("INVOICE_BD_BANK_BRANCH", "")
INVOICE_BD_BANK_ROUTING = os.getenv("INVOICE_BD_BANK_ROUTING", "")
INVOICE_BD_BANK_SWIFT = os.getenv("INVOICE_BD_BANK_SWIFT", "")
INVOICE_BD_PAYMENT_NOTE = os.getenv("INVOICE_BD_PAYMENT_NOTE", "")
INVOICE_BD_PAYPAL_EMAIL = os.getenv("INVOICE_BD_PAYPAL_EMAIL", "")

INVOICE_CA_ETRANSFER_EMAIL = os.getenv("INVOICE_CA_ETRANSFER_EMAIL", "")
INVOICE_CA_ETRANSFER_NAME = os.getenv("INVOICE_CA_ETRANSFER_NAME", "")
INVOICE_CA_ETRANSFER_NOTE = os.getenv("INVOICE_CA_ETRANSFER_NOTE", "")
INVOICE_CA_BANK_NAME = os.getenv("INVOICE_CA_BANK_NAME", "")
INVOICE_CA_BANK_ACCOUNT_NAME = os.getenv("INVOICE_CA_BANK_ACCOUNT_NAME", "")
INVOICE_CA_BANK_ACCOUNT_NUMBER = os.getenv("INVOICE_CA_BANK_ACCOUNT_NUMBER", "")
INVOICE_CA_BANK_INSTITUTION = os.getenv("INVOICE_CA_BANK_INSTITUTION", "")
INVOICE_CA_BANK_TRANSIT = os.getenv("INVOICE_CA_BANK_TRANSIT", "")
INVOICE_CA_BANK_SWIFT = os.getenv("INVOICE_CA_BANK_SWIFT", "")
INVOICE_CA_PAYMENT_NOTE = os.getenv("INVOICE_CA_PAYMENT_NOTE", "")
INVOICE_CA_PAYPAL_EMAIL = os.getenv("INVOICE_CA_PAYPAL_EMAIL", "")

# ======================
# Jazzmin
# ======================

JAZZMIN_SETTINGS = {
    "site_title": "Iconic Admin",
    "site_header": "Iconic CRM Admin",
    "site_brand": "Iconic CRM",
    "welcome_sign": "Welcome to Iconic CRM",
    "show_sidebar": True,
    "navigation_expanded": True,
    "show_ui_builder": True,
    "order_with_respect_to": ["crm", "auth"],
    "icons": {
        "auth.user": "fas fa-user",
        "auth.group": "fas fa-users",
        "crm.accountingentry": "fas fa-money-bill",
        "crm.bdstaff": "fas fa-user-friends",
        "crm.bdstaffmonth": "fas fa-file-invoice",
    },
}
