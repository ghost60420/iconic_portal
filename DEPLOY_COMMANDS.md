# UI Modernization Deployment Commands

Status: command sheet only. Do not execute until deployment is explicitly approved and the unresolved values below are confirmed.

Release branch: `codex/ui-modernization-final-stabilization`

Release tag: `UI_MODERNIZATION_v1.0_RC1`

## Confirmed Information

- Git remote: `https://github.com/ghost60420/iconic_portal.git`
- Public hosts in settings: `femline.ca`, `www.femline.ca`, `3.84.200.98`
- Project directory from repo docs/services: `/home/ec2-user/iconic_portal`
- Virtual environment path from repo docs/services: `/home/ec2-user/iconic_portal/venv`
- Static root from Django settings: `/home/ec2-user/iconic_portal/staticfiles`
- Database from Django settings: `/home/ec2-user/iconic_portal/db.sqlite3`
- Requirements file: `requirements.txt`
- Main Django service name: not confirmed in repo
- Nginx config path/location: not confirmed in repo
- Production source branch on AWS: not confirmed in repo

## Manual Values Required Before Execution

Replace these before any deployment:

```bash
APP_SERVICE="<confirmed_app_service>"
PRODUCTION_BRANCH="<confirmed_production_branch>"
PREVIOUS_PRODUCTION_COMMIT="<previous_production_commit>"
```

## Pre-Deployment Read-Only Verification

```bash
cd /home/ec2-user/iconic_portal
source venv/bin/activate
git status
git branch --show-current
git rev-parse HEAD
git remote -v
python3 manage.py check
python3 manage.py makemigrations --check --dry-run
```

Expected:

- Current branch and commit match the approved deployment target.
- `check` passes.
- `makemigrations --check --dry-run` reports no changes.

## Deployment Commands

Use only after approval.

```bash
cd /home/ec2-user/iconic_portal
source venv/bin/activate
git fetch origin --tags
git checkout "$PRODUCTION_BRANCH"
git pull --ff-only origin "$PRODUCTION_BRANCH"
git rev-parse HEAD
python3 -m pip install -r requirements.txt
python3 manage.py check
python3 manage.py makemigrations --check --dry-run
python3 manage.py collectstatic --noinput
sudo systemctl restart "$APP_SERVICE"
sudo systemctl status "$APP_SERVICE" --no-pager
```

No migration command is required for this UI-only release.

If dependency install is not desired for this frontend-only release, skip `python3 -m pip install -r requirements.txt` only after confirming the production environment already has the required packages.

## Verification URLs

Base URL confirmed in settings: `https://femline.ca`

Verify these page groups with the actual CRM URLs available to the logged-in production user:

- `https://femline.ca/` or the configured dashboard URL
- Dashboard
- CEO Dashboard
- AI Operations Assistant
- AI Health Monitor
- AI System Status
- Opportunity List
- Customer Detail
- Lead Detail
- Production Detail
- Invoice Detail
- Accounting
- Production Profit Report
- Approval Queue
- Employee pages
- Email Center
- WhatsApp Inbox
- Message Logs

Smoke checks:

- HTTP 200 for every target page.
- No new server errors in logs.
- No browser console errors.
- No horizontal overflow on desktop and mobile.
- Tabs switch correctly.
- Dropdown/action menus open.
- Filters and pagination remain reachable.
- CSRF tokens remain present on forms.
- Charts render where expected.
- Email, WhatsApp, automation, and production side-effect actions are not triggered unless intentionally tested by an authorized user.

## Rollback Commands

Use only if deployment verification fails.

```bash
cd /home/ec2-user/iconic_portal
source venv/bin/activate
git status
git checkout "$PREVIOUS_PRODUCTION_COMMIT"
python3 manage.py check
python3 manage.py collectstatic --noinput
sudo systemctl restart "$APP_SERVICE"
sudo systemctl status "$APP_SERVICE" --no-pager
```

Database rollback is not expected for this release because no migrations or database changes are included.
