# Opportunity 182 Production Repair Report

Date: 2026-07-17

## Scope

Investigate and prepare a safe fix for Opportunity 182 production conversion.

No production deployment was performed.
No production data was changed.
No invoice totals, payment amounts, accounting entries, or existing production records were changed.

## Production Backup

Fresh AWS production backup created before inspection:

`/home/ec2-user/backups/opportunity_182_prod_repair_20260717_173324`

SQLite integrity check result:

`ok`

Production branch and commit at inspection:

- Branch: `codex/historical-data-entry-mode`
- Commit: `edd1cec822b6e52f1594b09c693e161234b1e71f`

## Live Record Findings

Opportunity:

- ID: `182`
- Opportunity: `OPP-IN-1007-001`
- Stage: `Production`
- Customer ID: `593`
- Lead ID: `1555`
- Quantity: `605`
- Currency: `CAD`
- Sales value source: `order_value_usd=22990.00`
- Converted BDT value: `order_value=1954150.00`

Quick Costing:

- ID: `43`
- Display: `QC-43`
- Quotation: `QQT20260018`
- Status: `invoiced`
- Pricing type: `full_package`
- Quantity: `605`
- Unit selling price: `CAD 38.00`
- Calculated sales value: `CAD 22,990.00`
- No linked ProductionOrder

Invoice:

- Invoice: `INV00030`
- ID: `41`
- Status: `partial`
- Total: `CAD 22,990.00`
- Paid: `CAD 8,000.00`
- Balance: `CAD 14,990.00`
- `order_id`: `NULL`
- `opportunity_id`: `NULL`
- `quick_costing_id`: `43`

Lifecycle:

- ID: `57`
- Status: `invoice`
- Invoice ID: `41`
- Opportunity ID: `182`
- `production_order_id`: `NULL`

ProductionOrder checks:

- `ProductionOrder.objects.filter(opportunity_id=182).count()` = `0`
- `ProductionOrder.objects.filter(source_quick_costing_id=43).count()` = `0`

## Orphan Production Scan

Live production scan found `6` opportunities where `stage="Production"` but no linked ProductionOrder exists.

Generated report:

`ORPHAN_PRODUCTION_OPPORTUNITIES_REPORT.md`

This deployment is approved to repair Opportunity 182 only. The other five orphan records are reported and will receive the visible `Broken Production State` warning after deployment, but they are not modified by this task.

## Root Cause

The production workflow had three paths:

1. Traditional `CostingHeader`
2. Bangladesh CMT / local sewing `QuickCosting`
3. Full Package `QuickCosting`

The Full Package Quick Costing path correctly requires a fully paid invoice before creating a ProductionOrder.

However, the opportunity stage update flow could set `Opportunity.stage = "Production"` before confirming that a ProductionOrder was actually created. When the invoice was only partially paid, production creation was blocked, but the opportunity remained in `Production`, creating this mismatch:

- Opportunity stage showed `Production`
- Workflow timeline had no ProductionOrder
- Production tab was inactive
- Move to Production could not create an order because the invoice was not fully paid

A secondary UI issue hid the intended Django message: `opportunity_detail` was using the context key `messages` for AI messages, shadowing Django's framework messages.

## Payment Rule Confirmed

Partial payment is not allowed for Full Package Quick Costing production conversion in the current approved workflow.

Required behavior:

- Fully paid invoice: create or reuse ProductionOrder.
- Partially paid invoice: block conversion and show:

`Invoice must be fully paid before moving to Production.`

For an orphaned Production stage with no ProductionOrder, restore the opportunity to a pre-production stage.

For Opportunity 182, the safe restored stage is:

`Negotiation`

## Prepared Code Fix

Files changed locally:

- `crm/services/production_orders.py`
- `crm/views.py`
- `crm/templates/crm/opportunity_detail.html`
- `crm/tests/test_customer_workflow_improvements.py`

No model changes.
No migration changes.
No URL changes.
No database changes.
No CostingHeader workflow changes.
No Bangladesh CMT workflow changes.

Implemented safeguards:

- Added a helper to find the newest Full Package Quick Costing and latest invoice even when the invoice is not paid, so the view can show the correct payment-blocking message.
- Preserved the existing paid-only creation path for actual production creation.
- Added a disabled-by-default CEO/Admin payment override gate: `ALLOW_PARTIAL_PAYMENT_PRODUCTION_CEO_OVERRIDE`.
- With the override disabled, partially paid invoices cannot create ProductionOrders and cannot move an opportunity to `Production`.
- With the override explicitly enabled, CEO/Admin users can create production from a partially paid Full Package invoice.
- Added shared opportunity production helper that supports:
  - CostingHeader workflow
  - Bangladesh CMT / local sewing Quick Costing workflow
  - Full Package Quick Costing workflow
- Prevented manual stage update to `Production` unless a real ProductionOrder exists or can be created safely.
- Restored orphan `Production` stage when production creation is blocked.
- Restored orphan `Production` records to `Awaiting Payment` if that stage exists; otherwise to `Negotiation`.
- Added a visible `Broken Production State` badge and warning banner on Opportunity Detail when an opportunity is marked `Production` but has no linked ProductionOrder.
- Kept duplicate prevention by returning the existing ProductionOrder when one already exists.
- Renamed the AI message queryset context key from `messages` to `ai_messages` so Django warnings render normally.

## Copied Production Database Rehearsal

Rehearsal database:

`/home/ec2-user/iconic_portal/db_rehearsal.sqlite3`

Settings:

`iconic_site.settings_testdb`

Before and after copied DB snapshot remained unchanged:

- Opportunities: `111`
- Invoices: `30`
- Production Orders: `75`
- Payments: `16`
- Accounting Entries: `125`
- Invoice CAD total: `39672.6600000000`
- Payment CAD total: `18870.4100000000`
- Accounting CAD total: `68352.9000000000`

Rollback transaction rehearsal results:

Partial invoice case:

- Stage restored to `Negotiation`
- ProductionOrder count remained `0`
- Invoice `order_id` remained `NULL`
- Lifecycle `production_order_id` remained `NULL`
- Invoice totals and payments unchanged

Fully paid copied invoice case:

- ProductionOrder created in rollback transaction only
- Quantity: `605`
- Opportunity linked: `182`
- Source Quick Costing linked: `43`
- Invoice linked to ProductionOrder
- Lifecycle linked to ProductionOrder
- Lifecycle status became `production`
- Duplicate conversion returned the existing order and did not create a second one

Final rehearsal result:

`OPPORTUNITY_182_REHEARSAL_OK`

## Modified-Code Route Rehearsal

Using copied production DB and the local fixed code:

- GET `/production/from-opportunity/182/` followed redirect safely.
- Invoice was still partial.
- No ProductionOrder was created.
- Stage was restored from `Production` to `Negotiation` inside rollback transaction.
- Required message was visible:

`Invoice must be fully paid before moving to Production.`

After transaction rollback:

- Production copied DB returned to original state.
- Opportunity 182 remained unchanged in copied DB.
- Live production remained untouched.

## Test Results

Focused tests:

`DJANGO_SECRET_KEY=local-test-secret python3 manage.py test crm.tests.test_customer_workflow_improvements.CustomerWorkflowImprovementTests.test_full_package_quick_costing_paid_invoice_moves_opportunity_to_production crm.tests.test_customer_workflow_improvements.CustomerWorkflowImprovementTests.test_full_package_quick_costing_partial_invoice_blocks_production crm.tests.test_customer_workflow_improvements.CustomerWorkflowImprovementTests.test_orphan_production_stage_with_partial_invoice_is_restored crm.tests.test_customer_workflow_improvements.CustomerWorkflowImprovementTests.test_broken_production_state_badge_shows_without_production_order crm.tests.test_customer_workflow_improvements.CustomerWorkflowImprovementTests.test_stage_update_to_production_requires_real_production_order crm.tests.test_customer_workflow_improvements.CustomerWorkflowImprovementTests.test_ceo_payment_override_can_move_partial_invoice_to_production_when_enabled crm.tests.test_customer_workflow_improvements.CustomerWorkflowImprovementTests.test_full_package_quick_costing_paid_invoice_moves_from_quick_detail`

Result:

`7 tests OK`

Focused workflow tests:

`DJANGO_SECRET_KEY=local-test-secret python3 manage.py test crm.tests.test_customer_workflow_improvements crm.tests.test_ceo_gate_invoice_currency`

Result:

`42 tests OK`

Full CRM regression:

`DJANGO_SECRET_KEY=local-test-secret python3 manage.py test crm.tests`

Result:

`512 tests OK`

Django check:

`DJANGO_SECRET_KEY=local-test-secret python3 manage.py check`

Result:

`System check identified no issues`

Migration dry check:

`DJANGO_SECRET_KEY=local-test-secret python3 manage.py makemigrations --check --dry-run`

Result:

`No changes detected`

Python compile:

`DJANGO_SECRET_KEY=local-test-secret python3 -m py_compile crm/views.py crm/services/production_orders.py crm/tests/test_customer_workflow_improvements.py`

Result:

Passed.

Diff check:

`git diff --check`

Result:

Passed.

## Risk Assessment

Risk level: Medium.

Reason:

- The fix touches a central opportunity-to-production path.
- The implementation is constrained to production conversion checks and tests.
- Existing CostingHeader and Bangladesh CMT workflows are preserved and covered by existing regression coverage.
- Full Package Quick Costing paid and partially paid cases are now covered by focused tests.

Main remaining risk:

- Production has an existing inconsistent live record, so after deploying the code fix, Opportunity 182 should be repaired through the normal route or a reviewed targeted admin action. If the invoice is still partial, the route will restore the stage and will not create production.

## Recommended Production Handling After Approval

If invoice remains partially paid:

1. Deploy the code fix.
2. Open or trigger Move to Production for Opportunity 182.
3. Confirm the warning appears:
   `Invoice must be fully paid before moving to Production.`
4. Confirm stage is restored to `Negotiation`.
5. Do not create ProductionOrder.

If invoice becomes fully paid before deployment or before repair:

1. Deploy the code fix.
2. Trigger Move to Production once.
3. Confirm exactly one ProductionOrder is created.
4. Confirm:
   - Opportunity links to ProductionOrder
   - Quick Costing QC-43 links to ProductionOrder
   - Invoice INV00030 links to ProductionOrder
   - Lifecycle 57 `production_order_id` is populated
5. Trigger Move to Production again and confirm no duplicate order is created.

## Rollback Plan

Code rollback:

```bash
git checkout <previous_production_commit>
python3 manage.py collectstatic --noinput
sudo systemctl restart gunicorn.service
```

Database restore should only be used if a reviewed data repair changes production data unexpectedly:

```bash
cp /home/ec2-user/backups/opportunity_182_prod_repair_20260717_173324/db.sqlite3 /home/ec2-user/iconic_portal/db.sqlite3
sudo systemctl restart gunicorn.service
```

Do not restore the database for a code/template-only issue unless production data was changed.

## Deployment Status

Not deployed.

The exact cause is confirmed and the fix is prepared locally for review.
