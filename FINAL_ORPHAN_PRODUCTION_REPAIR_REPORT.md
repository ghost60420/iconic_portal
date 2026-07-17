# Final Orphan Production Repair Report

Date: 2026-07-17

## Scope

Repair remaining orphan production opportunities:

- 83
- 84
- 88
- 89
- 185

Definition used:

`Opportunity.stage = "Production"` and no `ProductionOrder` exists for that opportunity.

The `Opportunity` model has no direct `production_order_id`; the production relationship is checked through `ProductionOrder.opportunity_id`.

## Backup

Fresh production backup:

`/home/ec2-user/backups/orphan_production_repair_20260717_191315`

SQLite integrity:

`ok`

Previous production commit before this phase:

`4e3e2a0f5ea13baceeee0c029fd656e34e5238ce`

Deployed commit:

`4b157e8994deede7f90dbdb2bde6b9f6dea0c9fe`

## Code Changes

Files changed:

- `crm/services/production_integrity.py`
- `crm/services/operations_notifications.py`
- `crm/services/ceo_executive.py`
- `crm/templates/crm/ceo_executive_dashboard.html`
- `crm/tests/test_customer_workflow_improvements.py`

No model changes.
No migration changes.
No invoice total changes.
No payment amount changes.
No accounting changes.
No ProductionOrder records were created by the repair.

## Repair Rules Applied

If invoice fully paid:

- Create missing ProductionOrder.
- Keep stage as `Production`.

If invoice partially paid, unpaid, or draft:

- Revert stage to `Awaiting Payment` if that stage exists.
- Otherwise revert stage to `Negotiation`.

If invoice cancelled:

- Revert to a non-production terminal stage where available.

If no invoice exists:

- Revert to `Proposal`, because no production-eligible financial record exists.

`Awaiting Payment` is not available in current `Opportunity.STAGE_CHOICES`, so the payment fallback is `Negotiation`.

## Per-Opportunity Repair Table

| ID | Customer | Opportunity Number | Created Date | Current Stage Before | Invoice Status | Amount Paid | Remaining Balance | Production Order Exists | Lifecycle Production Link | Invoice Production Link | Costing Type | Cause | Repair |
| --- | --- | --- | --- | --- | --- | ---: | ---: | --- | --- | --- | --- | --- | --- |
| 83 | Harries | OPP-LHT7ZA96GE-001 | 2026-02-22 | Production | None | - | - | No | None | None | None | No costing, invoice, or lifecycle exists. Stage was likely changed by the legacy/manual stage path before production-order guards existed. | Stage reverted to `Proposal` |
| 84 | Harries | OPP-LYW565MOPX-001 | 2026-02-22 | Production | None | - | - | No | None | None | None | No costing, invoice, or lifecycle exists. Stage was likely changed by the legacy/manual stage path before production-order guards existed. | Stage reverted to `Proposal` |
| 88 | Fireground Apparel | OPP-L1FVY1ZC58-001 | 2026-02-27 | Production | None | - | - | No | None | None | None | No costing, invoice, or lifecycle exists. Stage was likely changed by the legacy/manual stage path before production-order guards existed. | Stage reverted to `Proposal` |
| 89 | Fireground Apparel | OPP-LFMAQPYX1A-001 | 2026-02-27 | Production | None | - | - | No | None | None | None | No costing, invoice, or lifecycle exists. Stage was likely changed by the legacy/manual stage path before production-order guards existed. | Stage reverted to `Proposal` |
| 185 | Fatiha Apparel | OPP-CWBJ8JGGL9-001 | 2026-07-12 | Production | draft / unpaid | 0.00 | 25000.00 | No | None | None | QuickCosting cmt_sewing | Invoice INV00032 is draft/unpaid; production cannot be created until fully paid or explicitly overridden. | Stage reverted to `Negotiation` |

## Verification

Before repair:

- Target orphan IDs: `[83, 84, 88, 89, 185]`

After repair:

- Target orphan IDs: `[]`
- All orphan production opportunities: `[]`
- Broken Production States CEO dashboard card: `0`
- Active broken production notifications: `0`

Record counts before and after matched:

| Record Type | Before | After |
| --- | ---: | ---: |
| Customers | 629 | 629 |
| Leads | 930 | 930 |
| Opportunities | 111 | 111 |
| Invoices | 30 | 30 |
| Payments | 16 | 16 |
| Production Orders | 75 | 75 |

## Nightly Integrity Checker

Implemented in:

- `crm/services/production_integrity.py`
- `crm/services/operations_notifications.py`
- existing command: `python manage.py sync_operations_notifications`

The existing operations notification sync now scans:

`stage = "Production"` with no linked `ProductionOrder`

If found, it creates a critical CEO notification:

- Title: `Broken production state`
- Type: `general`
- Source key prefix: `operations:broken_production_state:`
- Target: Opportunity Detail

If the issue is fixed, the notification is automatically resolved on the next sync.

## Cron

Nightly cron added under `ec2-user`:

```cron
45 08 * * * cd /home/ec2-user/iconic_portal && /home/ec2-user/iconic_portal/venv/bin/python manage.py sync_operations_notifications >> /home/ec2-user/iconic_portal/logs/sync_operations_notifications.log 2>&1
```

Previous crontab was backed up in:

`/home/ec2-user/backups/orphan_production_repair_20260717_191315/ec2_user_crontab.txt`

Manual run result:

`Operations notifications active: 64`

Broken production verification after manual run:

- `BROKEN_COUNT = 0`
- `ORPHAN_IDS = []`
- `ACTIVE_BROKEN_NOTIFICATIONS = 0`

## Tests

Local checks:

- `python manage.py check`: passed
- `python manage.py makemigrations --check --dry-run`: no changes detected
- `python -m py_compile` on changed Python files: passed
- `git diff --check`: passed
- Focused production integrity tests: passed
- CEO dashboard query budget test: passed
- Full CRM regression: `514 tests OK`

Production checks:

- `python manage.py check`: passed
- `python manage.py makemigrations --check --dry-run`: no changes detected
- `collectstatic --noinput`: `0 static files copied, 212 unmodified`
- `gunicorn.service`: active
- `/ceo-dashboard/`: authenticated page check returned 200
- CEO dashboard card shows `Broken Production States = 0`

## Rollback

Code rollback:

```bash
cd /home/ec2-user/iconic_portal
git checkout 4e3e2a0f5ea13baceeee0c029fd656e34e5238ce
python3 manage.py collectstatic --noinput
sudo systemctl restart gunicorn.service
```

Crontab rollback:

```bash
crontab /home/ec2-user/backups/orphan_production_repair_20260717_191315/ec2_user_crontab.txt
```

Database rollback, only if the stage repair must be reverted:

```bash
cd /home/ec2-user/iconic_portal
cp /home/ec2-user/backups/orphan_production_repair_20260717_191315/db.sqlite3 db.sqlite3
sudo systemctl restart gunicorn.service
```

## Final Status

All known orphan production opportunities have been repaired.

The integrity query now returns zero rows.

The CEO dashboard shows:

`Broken Production States = 0`

