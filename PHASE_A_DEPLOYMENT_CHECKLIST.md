# Phase A Deployment Checklist

## Pre-Deployment

- Confirm deployment approval is explicit.
- Confirm branch and commit to deploy.
- Confirm `git status --short` is clean or contains only approved Phase A files.
- Confirm changed files are limited to templates, static CSS, and documentation.
- Confirm no protected backend files changed:
  - `models.py`
  - `views.py`
  - `urls.py`
  - `services/*`
  - `migrations/*`
  - `settings.py`
  - management commands
  - costing, invoice, production, shipment, accounting, or approval logic
- Create production restore point:
  - project directory
  - `db.sqlite3`
  - `staticfiles/`
  - `media/`
  - nginx config
  - gunicorn service file
  - current git branch and commit
- Validate database backup with `sqlite3 db.sqlite3 "PRAGMA integrity_check;"`.
- Run:

```bash
python3 manage.py check
python3 manage.py makemigrations --check --dry-run
git diff --check
```

## Deployment

Do not run migrations unless Django reports unexpected pending migrations and deployment is paused for review.

```bash
cd /home/ec2-user/iconic_portal
git fetch origin
git checkout <approved_phase_a_branch>
python3 manage.py check
python3 manage.py makemigrations --check --dry-run
python3 manage.py collectstatic --noinput
sudo systemctl restart gunicorn.service
```

Do not restart nginx unless a real nginx issue is found.

## Post-Deployment Smoke Tests

Verify HTTP 200 and visual load:

- Login
- Main Dashboard
- Leads List
- Lead Detail
- Opportunities List
- Opportunity Detail
- Customers List
- Customer Detail
- Quick Costing List
- Quick Costing Form
- Quick Costing Detail
- CEO Quotation Approval Queue

Verify:

- CSS loaded
- No 500s
- No missing static files
- No console errors
- No horizontal overflow at desktop/tablet/mobile widths
- Forms present
- CSRF tokens present
- Buttons and links visible
- Filters work
- No permission changes
- No workflow changes

## Post-Deployment Data Safety

Compare before and after counts:

- Customers
- Leads
- Opportunities
- Quick Costings
- Invoices
- Production Orders
- Shipments
- Payments
- Accounting Entries

Compare financial totals by currency:

- CAD
- USD
- BDT

All values should match exactly for this UI-only deployment.

## Monitoring

Check:

```bash
sudo systemctl status gunicorn.service --no-pager
journalctl -u gunicorn.service -n 100 --no-pager
sudo tail -100 /var/log/nginx/error.log
```

Rollback immediately for:

- Live 500s
- Template errors
- Missing critical CSS/JS
- Broken login
- Broken Sales workflows
- Unexpected data/count/financial changes
