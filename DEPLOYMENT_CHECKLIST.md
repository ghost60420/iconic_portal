# UI Modernization Deployment Checklist

No deployment has been performed from this branch.

## Pre-Deployment Checklist

- Confirm approved branch name.
- Confirm production deploy source branch.
- Confirm AWS host.
- Confirm production project folder.
- Confirm app service name.
- Confirm static files deployment method.
- Confirm current production commit.
- Confirm working tree is clean except the approved UI modernization files.
- Confirm no migrations are included.
- Confirm no backend, model, URL, permission, query, workflow, or financial logic changes are included.
- Confirm known baseline failures from `BASELINE_FAILURES.md` are accepted or fixed separately.

Required commands:

```bash
git status
git branch --show-current
git rev-parse HEAD
python3 manage.py check
python3 manage.py makemigrations --check --dry-run
git diff --check
```

## Deployment Steps

Use only after approval.

```bash
git fetch origin
git checkout <approved_ui_branch>
git pull --ff-only origin <approved_ui_branch>
python3 manage.py check
python3 manage.py makemigrations --check --dry-run
python3 manage.py collectstatic --noinput
sudo systemctl restart <confirmed_app_service>
```

No `migrate` command is required for this UI-only release.

## Post-Deployment Checklist

Verify HTTP 200 and no server errors:

- Main Dashboard
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
- Employee list/detail/payroll pages
- Email Center
- WhatsApp Inbox
- Message Logs

Verify UI behavior:

- No horizontal overflow on desktop, tablet, or mobile.
- Sticky headers remain visible.
- Tabs switch correctly.
- Dropdown/action menus open.
- Filters submit.
- Pagination works.
- Forms retain CSRF tokens.
- Hidden fields remain present.
- Charts render where expected.
- No duplicate IDs in inspected pages.
- No browser console errors.

Smoke test actions without changing protected production data:

- Open Dashboard metrics.
- Open CEO Dashboard widgets.
- Open Approval Queue and verify approve/reject buttons are visible.
- Open Employee pages and verify forms are visible.
- Open Communication Center and verify inbox, filters, drafts, and attachment controls.
- Open Invoice Detail and verify payment/action sections are visible.
- Open Production Detail and verify stages, files, costing, invoice, shipment, and AI sections.

## Rollback Procedure

Code rollback:

```bash
git status
git checkout <previous_production_commit>
python3 manage.py check
python3 manage.py collectstatic --noinput
sudo systemctl restart <confirmed_app_service>
```

Database rollback:

- Not expected.
- This branch has no migrations and no database changes.

## Smoke Test Result Template

Record after deployment:

- Production commit:
- Service restarted:
- Dashboard:
- CEO Dashboard:
- AI pages:
- Accounting:
- Invoice Detail:
- Production Detail:
- Approval Queue:
- Employee pages:
- Communication Center:
- Mobile spot check:
- Browser console:
- Rollback needed:
