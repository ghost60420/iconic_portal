# iconic_site/settings.py
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# ======================
# Core Django settings
# ======================

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "change_this_in_env")
DEBUG = os.getenv("DJANGO_DEBUG", "1") == "1"

# ======================
# Hosts
# ======================

ALLOWED_HOSTS = [
    "127.0.0.1",
    "localhost",
    "10.0.0.99",
]

CSRF_TRUSTED_ORIGINS = [
    "http://127.0.0.1:8001",
    "http://localhost:8001",
    "http://10.0.0.99:8001",
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
]

MIDDLEWARE = [
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

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ======================
# Login redirects
# ======================

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/main-dashboard/"
LOGOUT_REDIRECT_URL = "/accounts/login/"

# ======================
# OpenAI settings
# ======================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

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
# Put real values in .env
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
# Email monitor rules (NO DUPLICATES)
# ======================

EMAIL_MONITOR = {
    # which inbox we treat as monitoring
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
# Put real values in .env
# ======================

WA_TOKEN = os.getenv("WA_TOKEN", "")
WA_PHONE_NUMBER_ID = os.getenv("WA_PHONE_NUMBER_ID", "")
WA_VERIFY_TOKEN = os.getenv("WA_VERIFY_TOKEN", "")
WA_APP_SECRET = os.getenv("WA_APP_SECRET", "")

WA_AUTO_REPLY_ENABLED = os.getenv("WA_AUTO_REPLY_ENABLED", "1") == "1"

# ======================
# Jazzmin
# ======================

JAZZMIN_SETTINGS = {
    "site_title": "Iconic Admin",
    "site_header": "Iconic CRM Admin",
    "welcome_sign": "Welcome to Iconic CRM",
    "show_sidebar": True,
    "navigation_expanded": True,
    "icons": {
        "auth.user": "fas fa-user",
        "auth.group": "fas fa-users",
        "crm.accountingentry": "fas fa-money-bill",
        "crm.bdstaff": "fas fa-user-friends",
        "crm.bdstaffmonth": "fas fa-file-invoice",
    },
}