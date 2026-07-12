# FINAL DEPLOYMENT APPROVAL REPORT

Deployment status: BLOCKED UNTIL APPROVED

No deployment was performed. No AWS git checkout, git pull, collectstatic, migration,
Gunicorn restart, Nginx restart, database restore, or service restart was performed.

## Production Restore Point

Backup location:

`/home/ec2-user/backups/ui_rc2_complete_predeploy_20260712_041022/`

Included and verified:

- Full project archive: `full_iconic_portal.tar.gz`
- Database copy: `db.sqlite3`
- Static files copy: `staticfiles/`
- Media/uploads copy: `media/`
- Nginx config copy: `nginx/nginx.conf`, `nginx/conf.d/`
- Gunicorn service metadata: `systemd/gunicorn.service.cat.txt`
- Gunicorn fragment copy: `systemd/gunicorn.service.fragment`
- Current git branch: `metadata/current_branch.txt`
- Current git commit: `metadata/current_commit.txt`
- Git status/remotes/log metadata
- Pip freeze metadata
- Rollback instructions: `ROLLBACK_README.txt`

Backup verification:

- Backup directory size: `1.5G`
- Full project archive size: `999M`
- Database backup size: `107M`
- Staticfiles backup size: `15M`
- Media backup size: `407M`
- SQLite integrity check: `ok`
- Live DB and backup DB SHA256 matched:
  `57cb7ccfec0162df5414fde5954ed0361ca2a60f298b8117b3946898a4e7f060`
- Full project archive was read end-to-end using `tar -tzf`.
- Key restore artifacts were verified present.

Rollback dry-run:

- Non-destructive rollback verification completed.
- No files were restored over production.
- The archive was validated as readable.
- Database/static/media/nginx/gunicorn/git metadata were confirmed present.

## Production State Confirmed

Production project path:

`/home/ec2-user/iconic_portal`

Production branch:

`codex/quick-costing-recall-workflow`

Production commit:

`d506504143a8b9c648dec14921eddb23c9e6609d`

Production git status:

- Only protected runtime folders are untracked:
  - `.reconcile_worktree/`
  - `backups/`
  - `logs/`

Root disk after EBS expansion and backup:

- `/dev/nvme0n1p1`
- XFS
- `40G`
- About `32G` available before/after backup verification window

## RC2 Scope Verification

Local RC2 branch:

`codex/ui-modernization-rc2-safe`

RC2 base commit:

`d506504143a8b9c648dec14921eddb23c9e6609d`

Changed file scope:

- `crm/templates/**`
- `static/crm/**`
- `crm/tests/**`
- Documentation/report files

Disallowed file audit:

- No `models.py` changes
- No migration changes
- No service changes
- No URL changes
- No settings changes
- No management command changes
- No costing backend logic changes
- No invoice backend logic changes
- No shipment backend logic changes
- No accounting backend logic changes
- No production workflow backend logic changes

Path-name review:

The following files contain protected domain names in the path, but are templates only:

- `crm/templates/crm/accounting_list.html`
- `crm/templates/crm/costing/ceo_quotation_approval_queue.html`
- `crm/templates/crm/invoice/invoice_view.html`
- `crm/templates/crm/production_detail.html`
- `crm/templates/crm/production_profit_report.html`

## Changed Files

Templates:

- `crm/templates/crm/accounting_list.html`
- `crm/templates/crm/ai/ai_health_monitor.html`
- `crm/templates/crm/ai/ai_system_status.html`
- `crm/templates/crm/ai_operations_assistant.html`
- `crm/templates/crm/bd_staff_form.html`
- `crm/templates/crm/bd_staff_list.html`
- `crm/templates/crm/bd_staff_month_form.html`
- `crm/templates/crm/bd_staff_month_generate.html`
- `crm/templates/crm/bd_staff_month_list.html`
- `crm/templates/crm/ceo_dashboard.html`
- `crm/templates/crm/ceo_executive_dashboard.html`
- `crm/templates/crm/costing/ceo_quotation_approval_queue.html`
- `crm/templates/crm/customer_detail.html`
- `crm/templates/crm/daily_ceo_briefing_email_draft.html`
- `crm/templates/crm/dashboard/_metric_card.html`
- `crm/templates/crm/email_sync/dashboard.html`
- `crm/templates/crm/invoice/invoice_view.html`
- `crm/templates/crm/lead_detail.html`
- `crm/templates/crm/main_dashboard.html`
- `crm/templates/crm/opportunities_list.html`
- `crm/templates/crm/production_detail.html`
- `crm/templates/crm/production_profit_report.html`
- `crm/templates/crm/whatsapp/disabled.html`
- `crm/templates/crm/whatsapp/inbox.html`
- `crm/templates/crm/whatsapp/infobip_events.html`

Static:

- `static/crm/ui_system.css`

Tests:

- `crm/tests/test_communication_center_ui.py`
- `crm/tests/test_control_center_ui.py`
- `crm/tests/test_employee_module_ui.py`

Documentation:

- `FINAL_PREDEPLOY_REPORT.md`
- `FINAL_DEPLOYMENT_APPROVAL_REPORT.md`

## Local Verification

Environment note:

- Local checks used `DJANGO_SECRET_KEY=local-predeploy-check`.
- Local browser rehearsal used `iconic_site.settings_testdb`.
- Temporary local database: `db_rehearsal.sqlite3`.
- No production database was used for local browser rehearsal.

Commands run:

```bash
DJANGO_SECRET_KEY=local-predeploy-check python3 manage.py check
DJANGO_SECRET_KEY=local-predeploy-check python3 manage.py makemigrations --check --dry-run
DJANGO_SECRET_KEY=local-predeploy-check python3 manage.py test crm.tests
DJANGO_SECRET_KEY=local-predeploy-check python3 manage.py collectstatic --noinput
git diff --check
git diff --cached --check
```

Results:

- `manage.py check`: passed
- `makemigrations --check --dry-run`: passed, no changes detected
- Full CRM tests: passed
- Test count: `475`
- `git diff --check`: passed
- `git diff --cached --check`: passed
- `collectstatic`: passed
- Static files copied locally: `0`
- Static files unchanged locally: `207`

Collectstatic warning:

- Django reported duplicate destination paths for `admin/js/cancel.js` and
  `admin/js/popup_response.js`.
- Collectstatic completed successfully.
- No AWS collectstatic was run.

## Browser Rehearsal

Browser connector note:

- The in-app browser execution tool was not available in this session after tool discovery.
- Fallback used Python Playwright against a local Django runserver.

Local server:

`http://127.0.0.1:8026`

Viewports checked:

- Desktop: `1440x1000`
- Tablet: `768x1000`
- Mobile: `390x900`

Pages checked:

- Main Dashboard
- CEO Dashboard
- Opportunity List
- Customer Detail
- Lead Detail
- Production Detail
- Invoice Detail
- Accounting
- Production Profit Report
- Approval Queue
- Employee List
- Email Center
- WhatsApp Inbox
- AI Operations
- AI Health
- AI System Status

Browser result:

- Page/viewport combinations checked: `48`
- Failed checks: `0`
- HTTP 500 errors: `0`
- JavaScript page errors: `0`
- Console errors on checked pages: `0`
- Horizontal overflow failures: `0`

Local dev-server note:

- The login page requested `/static/img/iconic-login-bg.png` and the local dev
  server returned `404`.
- This was outside the RC2 page verification set and did not affect authenticated
  page checks.
- The RC2 page CSS and JS assets loaded successfully in local rehearsal.

## Rollback Commands

If a future deployment is approved and must be rolled back:

```bash
cd /home/ec2-user/iconic_portal
git checkout d506504143a8b9c648dec14921eddb23c9e6609d
rm -rf staticfiles
cp -a /home/ec2-user/backups/ui_rc2_complete_predeploy_20260712_041022/staticfiles /home/ec2-user/iconic_portal/staticfiles
sudo systemctl restart gunicorn.service
curl -I http://127.0.0.1:8000/accounts/login/
curl -I https://femline.ca/accounts/login/
```

Restore the database only if data was modified:

```bash
cp /home/ec2-user/backups/ui_rc2_complete_predeploy_20260712_041022/db.sqlite3 /home/ec2-user/iconic_portal/db.sqlite3
```

Restore media only if media files were modified:

```bash
rm -rf /home/ec2-user/iconic_portal/media
cp -a /home/ec2-user/backups/ui_rc2_complete_predeploy_20260712_041022/media /home/ec2-user/iconic_portal/media
```

Full project archive:

```bash
/home/ec2-user/backups/ui_rc2_complete_predeploy_20260712_041022/full_iconic_portal.tar.gz
```

## Remaining Risks

- Deployment has not been performed yet.
- AWS collectstatic has not been run for RC2.
- AWS Gunicorn has not been restarted for RC2.
- The local login background image 404 should be reviewed separately if it is
  expected to exist in production static assets.
- RC2 files are still uncommitted in the local RC2 workspace at the time of this
  report.

## Recommendation

READY FOR MANUAL REVIEW.

Deployment remains blocked until this report is approved.
