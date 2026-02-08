# Marketing Module Setup

## Feature flags
Set in `.env` (default off, except MARKETING_ENABLED in debug).

```
MARKETING_ENABLED=1
MARKETING_SEO_ENABLED=1
MARKETING_SOCIAL_ENABLED=1
MARKETING_OUTREACH_ENABLED=1
MARKETING_ADS_ENABLED=1
MARKETING_AI_ENABLED=1
SITE_BASE_URL=https://femline.ca
```

## OAuth / integrations
Store OAuth tokens in the admin for `OAuthCredential` or use the connect page.
Tokens are encrypted at rest using `MARKETING_ENCRYPTION_KEY` or fallback to `DJANGO_SECRET_KEY`.

Example env:
```
MARKETING_ENCRYPTION_KEY=your_random_key
```

## Background jobs (no Celery in this project)
Use management commands via cron:

```
python manage.py marketing_sync_gsc_daily
python manage.py marketing_sync_ga4_daily
python manage.py marketing_sync_meta_daily
python manage.py marketing_sync_tiktok_daily
python manage.py marketing_sync_youtube_daily
python manage.py marketing_sync_linkedin_daily
python manage.py marketing_sync_google_business_daily

python manage.py marketing_outreach_enqueue_emails
python manage.py marketing_outreach_send_emails
python manage.py marketing_outreach_pause_on_risk
python manage.py marketing_generate_insights
```

## Outreach email (Outlook SMTP)
The system uses Django SMTP settings in `iconic_site/settings.py`.
For Outlook, set:

```
EMAIL_HOST=smtp.office365.com
EMAIL_PORT=587
EMAIL_HOST_USER=your@company.com
EMAIL_HOST_PASSWORD=your_password
EMAIL_USE_TLS=1
DEFAULT_FROM_EMAIL=your@company.com
```

## CSV import
Supported columns:
- email (required)
- first_name,last_name,company,phone,website,city,state,country,industry,job_title
- consent_status,do_not_contact

## Troubleshooting
- If sync returns no data, confirm OAuth tokens are valid.
- If email send fails, verify SMTP settings and allow send from the configured inbox.
- Run `marketing_setup_roles` after migrations to create the Marketing Manager group.

## New intelligence module pages
- /marketing/dashboard/ for the main intelligence dashboard
- /marketing/connect/ to connect social accounts and paste tokens
- /marketing/platform/<platform>/ for per-platform detail
- /marketing/content/ for the unified content library
- /marketing/ads/ for ads rollup (requires MARKETING_ADS_ENABLED)
- /marketing/best-practices/ for internal playbooks
- /marketing/insights/ for action items
- /marketing/workflow/ for the weekly rhythm guide
