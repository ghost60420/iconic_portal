# Final Phase D Deployment Checklist

## Pre Deployment

- [ ] Confirm final approval was granted.
- [ ] Confirm deployment source branch is `codex/ui-density-phase-d-final-prep`.
- [ ] Confirm current AWS project path is `/home/ec2-user/iconic_portal`.
- [ ] Confirm current live rollback branch is `codex/ui-density-phase3-enterprise`.
- [ ] Confirm current live rollback commit is `06d7888b5d70a3ea042174f45c87c4d3704c0612`.
- [ ] Confirm backup path exists:
  - `/home/ec2-user/backups/phase_d_predeploy_20260714_031528`
- [ ] Confirm backup SQLite integrity:
  - `cat /home/ec2-user/backups/phase_d_predeploy_20260714_031528/sqlite_integrity.txt`
  - Expected: `ok`
- [ ] Confirm DB hashes match:
  - `cat /home/ec2-user/backups/phase_d_predeploy_20260714_031528/db_sha256.txt`
- [ ] Confirm live working tree is clean except protected local directories:
  - `.reconcile_worktree/`
  - `backups/`
  - `logs/`
- [ ] Confirm no migrations are expected for this release.

## Scope Verification

Allowed file types:

- `crm/templates/**`
- `marketing/templates/**`
- `static/crm/**`
- `static/marketing/**`
- `marketing/views.py`
- `marketing/tests.py`
- documentation files

Blocked file types:

- `crm/models.py`
- `crm/migrations/**`
- `crm/services/**`
- `crm/urls.py`
- `crm/views_costing.py`
- `crm/views_invoice.py`
- settings files
- management commands
- deployment scripts
- production workflow logic
- invoice logic
- costing logic
- accounting formulas
- payroll calculation logic
- email or WhatsApp sending services

## Deployment Commands

Run only after final approval.

```bash
cd /home/ec2-user/iconic_portal

git fetch origin
git status --short
git branch --show-current
git rev-parse HEAD

git checkout codex/ui-density-phase-d-final-prep

python3 manage.py check
python3 manage.py makemigrations --check --dry-run
python3 manage.py collectstatic --noinput

sudo systemctl restart gunicorn.service
```

Do not run migrations unless Django unexpectedly reports pending migrations and the deployment is paused for review.

Do not restart nginx unless a real nginx issue is found and reported.

## Post Deployment Health Checks

```bash
sudo systemctl is-active gunicorn.service
curl -I http://127.0.0.1:8000/accounts/login/
curl -I https://femline.ca/accounts/login/
journalctl -u gunicorn.service -n 100 --no-pager
sudo tail -100 /var/log/nginx/error.log
```

## Page Smoke Tests

### Marketing

- [ ] Marketing Dashboard
- [ ] Campaign Dashboard
- [ ] Campaign List
- [ ] Outreach Dashboard
- [ ] Connection Diagnostics

### AI Tools

- [ ] AI Dashboard
- [ ] AI Assistant
- [ ] AI Operations
- [ ] AI Health
- [ ] AI System Status
- [ ] Audit Logs

### Control Center

- [ ] CEO Dashboard
- [ ] Employee Management
- [ ] Role Management
- [ ] Notifications Center
- [ ] Email Center
- [ ] WhatsApp Center
- [ ] Payroll
- [ ] Approvals
- [ ] System Settings
- [ ] User Permissions
- [ ] Activity Logs
- [ ] System Health

## Browser Verification

For desktop and mobile:

- [ ] HTTP 200
- [ ] No 500 errors
- [ ] No template errors
- [ ] CSS loaded
- [ ] No missing static files
- [ ] No JavaScript console errors
- [ ] No horizontal overflow
- [ ] No duplicate IDs
- [ ] Tables remain readable
- [ ] Filters work
- [ ] Forms still contain CSRF tokens
- [ ] Buttons and links remain visible
- [ ] Email/WhatsApp pages do not trigger sending during view checks
- [ ] Payroll values render unchanged
- [ ] Permissions pages do not alter role assignments

## Data Safety Verification

This is a UI deployment. No data changes are expected.

- [ ] No migrations run.
- [ ] No database writes performed by deployment commands.
- [ ] No media files deleted or replaced.
- [ ] No nginx config changed.
- [ ] No gunicorn service file changed.
- [ ] Static files updated only through `collectstatic`.

## Stop Conditions

Stop and rollback if any of these occur:

- `manage.py check` fails.
- `makemigrations --check --dry-run` reports changes.
- `collectstatic` fails.
- Gunicorn fails to restart.
- Any critical page returns HTTP 500.
- Template errors appear.
- Email or WhatsApp behavior changes.
- Payroll values change.
- Permissions behavior changes.
- Static CSS/JS fails to load.
- A serious mobile overflow makes a page unusable.
