# Quick Costing Full Package Production Fix Report

## Summary

Opportunity `186` could not move to Production because the production conversion workflow only supported:

- CEO-approved `CostingHeader` quotations.
- Bangladesh Local Sewing / CMT Quick Costing production.

Full Package `QuickCosting` records with a paid invoice were rejected by the existing workflow and never reached `ProductionOrder` creation.

## Root Cause

- `production_from_opportunity()` only looked for an approved `CostingHeader`.
- `create_or_link_production_order_from_invoice()` explicitly rejected non-Bangladesh-local Quick Costing invoices.
- `quick_costing_convert_to_production()` only allowed `quick_costing.is_bangladesh_local_sewing`.

The live Opportunity `186` has no `CostingHeader`; it has `QuickCosting.id=46`, `pricing_type=full_package`, `status=invoiced`, a paid invoice, and an existing lifecycle.

## Files Changed

- `crm/services/production_orders.py`
- `crm/services/costing_workflow.py`
- `crm/views.py`
- `crm/views_costing.py`
- `crm/tests/test_customer_workflow_improvements.py`

No models, migrations, URLs, database fields, accounting logic, shipment logic, or Bangladesh Local Sewing internals were changed.

## Behavior Added

Added a third production conversion path:

Full Package Quick Costing -> Paid Invoice -> Production Order

Eligibility:

- `QuickCosting` exists.
- `QuickCosting.effective_pricing_type == full_package`.
- Quick Costing is CEO approved and latest revision.
- Linked opportunity exists and is not archived.
- Linked invoice is paid.
- No production order already exists for the Quick Costing or opportunity.

Created `ProductionOrder` includes:

- `source_quick_costing`
- `customer`
- `lead` when present, otherwise `None`
- `opportunity`
- `invoice` link through `Invoice.order`
- `qty_total`
- `approved_currency`
- `approved_selling_price`
- `approved_total_value`
- approved Quick Costing summary snapshot
- production owner from Quick Costing salesperson or opportunity assignee
- lifecycle update through existing lifecycle service

Duplicate protection:

- Existing `ProductionOrder.source_quick_costing` is reused.
- Existing `ProductionOrder.opportunity` is reused.
- Paid invoice is linked to the existing order instead of creating another order.

## Preserved Behavior

- `CostingHeader` production conversion still uses `create_production_order_from_approved_quotation()`.
- Bangladesh Local Sewing / CMT still uses `create_production_order_from_approved_quick_costing()`.
- CMT still requires approved Quick Costing before direct local sewing production.
- No accounting entries are created by this change.
- No payment records are created or changed.
- No invoice totals are changed.

## Tests Run

- `python3 -m py_compile crm/services/production_orders.py crm/services/costing_workflow.py crm/views.py crm/views_costing.py crm/tests/test_customer_workflow_improvements.py`
- `DJANGO_SECRET_KEY=local-test-secret python3 manage.py check`
- `DJANGO_SECRET_KEY=local-test-secret python3 manage.py makemigrations --check --dry-run`
- `DJANGO_SECRET_KEY=local-test-secret python3 manage.py test crm.tests.test_customer_workflow_improvements.CustomerWorkflowImprovementTests.test_full_package_quick_costing_paid_invoice_moves_opportunity_to_production crm.tests.test_customer_workflow_improvements.CustomerWorkflowImprovementTests.test_full_package_quick_costing_paid_invoice_moves_from_quick_detail`
- `DJANGO_SECRET_KEY=local-test-secret python3 manage.py test crm.tests.test_production_order_from_quotation crm.tests.test_costing_invoice_workflow`
- `DJANGO_SECRET_KEY=local-test-secret python3 manage.py test crm.tests.test_local_sewing.LocalSewingApprovalGateTests.test_ceo_approval_unlocks_explicit_local_production_move crm.tests.test_local_sewing.LocalSewingApprovalGateTests.test_sales_creator_still_uses_pending_ceo_queue`
- `DJANGO_SECRET_KEY=local-test-secret python3 manage.py test crm.tests.test_customer_workflow_improvements`
- `git diff --check`
- `DJANGO_SECRET_KEY=local-test-secret python3 manage.py test crm.tests`

## Test Results

- Django check: passed.
- Migration dry-run: passed, no changes detected.
- New Full Package Quick Costing tests: passed.
- Existing CostingHeader workflow tests: passed.
- Existing Bangladesh Local Sewing workflow tests: passed.
- Customer workflow regression file: passed.
- Full CRM regression: 480 tests passed.
- `git diff --check`: passed.

Expected test log noise was observed from existing audit and shipment notification resilience tests; the full suite completed successfully.

## Risk Assessment

Risk level: Medium-low.

Reason:

- The change touches production conversion code, which is a critical workflow.
- Scope is limited to adding a new Full Package Quick Costing path.
- Existing CostingHeader and Bangladesh Local Sewing paths remain covered by passing regression tests.
- No schema or data changes are required.

Remaining limitation:

- `QuickCosting` does not have a direct `Product` foreign key. The production order stores product context through existing snapshots: `product_name_snapshot`, `product_type_snapshot`, `style_name`, quantity, and approved costing summary.

## Deployment Recommendation

Ready for review.

Do not deploy until approved. Deployment should not run migrations.
