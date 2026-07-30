# KPI Private Staging Deployment Report

## Deployment

- Date: 2026-07-29 America/Vancouver
- Source branch: `feature/kpi-development-integration`
- Source commit: `1b3f9294c4129bde3d1aef68ef280633b2723471`
- Deployment branch: `deploy/kpi-private-staging`
- Deployed application commit: `03b250ce92f903b81f0596f29b1483a5bb4ec074`
- URL: `https://kpi-staging.100-51-11-48.sslip.io/`
- Host: `ec2-user@femline.ca`
- Project: `/home/ec2-user/iconic_kpi_staging`
- Service: `iconic-kpi-staging.service`
- Bind: `127.0.0.1:8011`
- Settings: `iconic_site.settings_staging`
- Database: `/home/ec2-user/iconic_kpi_staging/var/db/staging.sqlite3`
- Migration: `crm.0198_kpi_release_governance`
- Static: `/home/ec2-user/iconic_kpi_staging/var/static`
- Media: `/home/ec2-user/iconic_kpi_staging/var/media`
- Logs: `/home/ec2-user/iconic_kpi_staging/var/log`
- Backups: `/home/ec2-user/iconic_kpi_staging/var/backups`

Nothing was deployed, pulled, merged, migrated, or restarted in production.
The production branch remained `living-catalog-production-deployment` at
`1084fc9`, and `gunicorn.service` remained active.

## Source Confirmation

The source history contains KPI Stages 2 through 11, final release gates, and
the development CRM integration. The deployed build includes KPI navigation,
policies, assignments, review workflow, dashboards, intelligence, bonus
readiness, CRM notifications, reports, and PDF/XLSX/CSV/Print exports.

## Isolation

- New systemd application service; no shared Gunicorn process.
- Dedicated SQLite database sourced from the verified KPI development database.
- Dedicated environment file with a generated staging-only secret.
- Dedicated static, media, log, backup, and local automation-audit paths.
- No staging cron, Celery worker, KPI timer, or recurring KPI automation.
- In-memory email backend and memory-only Celery configuration.
- WhatsApp, SMS, payment, payroll, Marketing provider, OpenAI, webhook, and
  outbound message settings disabled.
- HTTPS uses a staging-only Let's Encrypt certificate.
- HTTP Basic Auth is required before the CRM login.

## Database Preparation

The production database was never copied or opened for staging. The verified
KPI development database was copied, backed up, and sanitized before upload.
Sanitization preserved primary keys and relationships while:

- deactivating and anonymizing all non-UAT users;
- assigning unusable passwords to all non-UAT users;
- rotating all seven UAT passwords;
- masking customer, lead, and employee identity data;
- clearing provider tokens, inbox passwords, payment details, and private file
  references;
- clearing email and WhatsApp content;
- labeling KPI policy fixtures `STAGING TEST POLICY`.

Results:

- Active users: 7 UAT identities only
- Real user email domains: 0
- Non-UAT usable passwords: 0
- OAuth tokens: 0
- Private file references: 0
- Integrity: `ok`
- Foreign-key violations: 0
- Pending migrations: 0
- Model changes: 0

## Backups

Pre-migration backup:

`/home/ec2-user/iconic_kpi_staging/var/backups/pre_migration_20260730T012000Z/staging.sqlite3`

Post-UAT backup:

`/home/ec2-user/iconic_kpi_staging/var/backups/post_uat_20260730T014300Z/staging.sqlite3`

Live post-UAT database checksum:

`877827dc011437bb996b65d782e45db13db4954746f6168af8e62a7e033f50b7`

Post-UAT online backup checksum:

`d9b968a0647c66022af99acec20e87d77c38f69e5caa01108e7f513ce9b7e155`

The SQLite online backup is transactionally equivalent although its byte-level
layout differs. Both contain 7 reviews, 52 review transitions, and 119 KPI
notification events. The backup passes integrity and foreign-key checks.

## Verification

- KPI-focused suite: 218 passed
- Full CRM suite: 981 passed
- `manage.py check --deploy`: passed
- Changed Python compilation: passed
- HTTPS certificate: valid through 2026-10-28
- Unauthenticated HTTPS response: `401`
- Staging service: active
- Production service: active
- Staging nginx responses with actual status `5xx`: 0
- Staging app traceback/timeout events: 0
- Real Chrome workflow, roles, exports, and responsive screenshots: passed

The certificate-renewal timer is enabled for TLS maintenance only. It does not
run Django, KPI, provider, notification, or CRM automation.

## Manual KPI Automation

Recurring automation is absent. The staging-only command is:

```bash
cd /home/ec2-user/iconic_kpi_staging
set -a
source config/staging.env
set +a
venv/bin/python manage.py run_kpi_staging_automation --schedule daily --dry-run
```

An explicit real run and cleanup were verified with run `2`: 12 CRM-only test
notifications were created and all 12 were removed. The run and cleanup remain
recorded in `var/log/kpi_automation_manual.jsonl`. No external provider was
contacted.
