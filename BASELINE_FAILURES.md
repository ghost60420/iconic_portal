# Baseline Test Failures

Verified on:

- Original clean `main` baseline: `2217ec1b58c148d0e4594338b6de8857c19c586b`
- Clean UI branch: `codex/ui-modernization-clean-scope`

These failures reproduce before Phase D and are unrelated to UI modernization.

## 1. `crm.tests.test_invoice_internal_costing`

Command:

```bash
DJANGO_SECRET_KEY=baseline-test python3 manage.py test crm.tests.test_invoice_internal_costing
```

Failing tests:

- `crm.tests.test_invoice_internal_costing.InvoiceInternalCostingTests.test_bangladesh_sewing_charge_invoice_does_not_split_when_style_quantities_missing`
- `crm.tests.test_invoice_internal_costing.InvoiceInternalCostingTests.test_bangladesh_sewing_charge_invoice_shows_multiple_style_summary`
- `crm.tests.test_invoice_internal_costing.InvoiceInternalCostingTests.test_bangladesh_sewing_charge_pdf_uses_real_style_quantities`

Original `main` traceback:

```text
ERROR: test_bangladesh_sewing_charge_invoice_does_not_split_when_style_quantities_missing
Traceback (most recent call last):
  File "/private/tmp/iconic-main-baseline-check/crm/tests/test_invoice_internal_costing.py", line 363, in test_bangladesh_sewing_charge_invoice_does_not_split_when_style_quantities_missing
    ProductionOrderLine.objects.create(order=order, line_no=1, style_name="Training Tee", quantity=50)
  File "/Library/Frameworks/Python.framework/Versions/3.12/lib/python3.12/site-packages/django/db/models/base.py", line 569, in __init__
    raise TypeError(
TypeError: ProductionOrderLine() got unexpected keyword arguments: 'quantity'

ERROR: test_bangladesh_sewing_charge_invoice_shows_multiple_style_summary
Traceback (most recent call last):
  File "/private/tmp/iconic-main-baseline-check/crm/tests/test_invoice_internal_costing.py", line 331, in test_bangladesh_sewing_charge_invoice_shows_multiple_style_summary
    ProductionOrderLine.objects.create(order=order, line_no=1, style_name="Training Tee", quantity=50)
  File "/Library/Frameworks/Python.framework/Versions/3.12/lib/python3.12/site-packages/django/db/models/base.py", line 569, in __init__
    raise TypeError(
TypeError: ProductionOrderLine() got unexpected keyword arguments: 'quantity'

ERROR: test_bangladesh_sewing_charge_pdf_uses_real_style_quantities
Traceback (most recent call last):
  File "/private/tmp/iconic-main-baseline-check/crm/tests/test_invoice_internal_costing.py", line 409, in test_bangladesh_sewing_charge_pdf_uses_real_style_quantities
    ProductionOrderLine.objects.create(order=order, line_no=1, style_name="Training Tee", quantity=50)
  File "/Library/Frameworks/Python.framework/Versions/3.12/lib/python3.12/site-packages/django/db/models/base.py", line 569, in __init__
    raise TypeError(
TypeError: ProductionOrderLine() got unexpected keyword arguments: 'quantity'
```

Clean UI branch traceback:

```text
ERROR: test_bangladesh_sewing_charge_invoice_does_not_split_when_style_quantities_missing
Traceback (most recent call last):
  File "/Users/hossain/iconic_portal_ui_clean_scope/crm/tests/test_invoice_internal_costing.py", line 363, in test_bangladesh_sewing_charge_invoice_does_not_split_when_style_quantities_missing
    ProductionOrderLine.objects.create(order=order, line_no=1, style_name="Training Tee", quantity=50)
  File "/Library/Frameworks/Python.framework/Versions/3.12/lib/python3.12/site-packages/django/db/models/base.py", line 569, in __init__
    raise TypeError(
TypeError: ProductionOrderLine() got unexpected keyword arguments: 'quantity'

ERROR: test_bangladesh_sewing_charge_invoice_shows_multiple_style_summary
Traceback (most recent call last):
  File "/Users/hossain/iconic_portal_ui_clean_scope/crm/tests/test_invoice_internal_costing.py", line 331, in test_bangladesh_sewing_charge_invoice_shows_multiple_style_summary
    ProductionOrderLine.objects.create(order=order, line_no=1, style_name="Training Tee", quantity=50)
  File "/Library/Frameworks/Python.framework/Versions/3.12/lib/python3.12/site-packages/django/db/models/base.py", line 569, in __init__
    raise TypeError(
TypeError: ProductionOrderLine() got unexpected keyword arguments: 'quantity'

ERROR: test_bangladesh_sewing_charge_pdf_uses_real_style_quantities
Traceback (most recent call last):
  File "/Users/hossain/iconic_portal_ui_clean_scope/crm/tests/test_invoice_internal_costing.py", line 409, in test_bangladesh_sewing_charge_pdf_uses_real_style_quantities
    ProductionOrderLine.objects.create(order=order, line_no=1, style_name="Training Tee", quantity=50)
  File "/Library/Frameworks/Python.framework/Versions/3.12/lib/python3.12/site-packages/django/db/models/base.py", line 569, in __init__
    raise TypeError(
TypeError: ProductionOrderLine() got unexpected keyword arguments: 'quantity'
```

Reason:

`ProductionOrderLine` in `crm/models.py` currently defines order, line number, style, color, size group, notes, and packaging fields, but it does not define `quantity`. The tests create `ProductionOrderLine(..., quantity=50)`, so Django raises before any invoice detail UI code is exercised.

Why unrelated to UI modernization:

The failure exists on untouched `main` at the same base commit and occurs during test data setup before rendering the modernized invoice page. The clean UI branch did not modify `ProductionOrderLine`, migrations, invoice creation logic, or invoice calculations.

Recommendation:

Fix in a separate backend/migration task. Decide whether `ProductionOrderLine.quantity` is still a required business field. If yes, add the model field and migration safely. If no, update the tests to use the current production-line quantity source.

## 2. `marketing.tests_social_connections.MarketingSocialConnectionsTests.test_google_business_account_metrics_feed_dashboard_rollups`

Command:

```bash
DJANGO_SECRET_KEY=baseline-test python3 manage.py test marketing.tests_social_connections.MarketingSocialConnectionsTests.test_google_business_account_metrics_feed_dashboard_rollups
```

Original `main` traceback:

```text
FAIL: test_google_business_account_metrics_feed_dashboard_rollups
Traceback (most recent call last):
  File "/private/tmp/iconic-main-baseline-check/marketing/tests_social_connections.py", line 328, in test_google_business_account_metrics_feed_dashboard_rollups
    self.assertEqual(totals["impressions"], 100)
AssertionError: 0 != 100
```

Clean UI branch traceback:

```text
FAIL: test_google_business_account_metrics_feed_dashboard_rollups
Traceback (most recent call last):
  File "/Users/hossain/iconic_portal_ui_clean_scope/marketing/tests_social_connections.py", line 328, in test_google_business_account_metrics_feed_dashboard_rollups
    self.assertEqual(totals["impressions"], 100)
AssertionError: 0 != 100
```

Reason:

The test creates a `google_business` `SocialAccount` and one `AccountMetricDaily` row, then expects `_metric_totals(...)` and `_platform_comparison(...)` to include those metrics. The actual aggregation returns zero impressions.

Why unrelated to UI modernization:

The failure exists on untouched `main` at the same base commit. The clean UI branch does not modify `marketing/*`, Google/GA4 services, marketing models, sync commands, marketing templates, or dashboard metric aggregation code.

Recommendation:

Fix in a separate marketing metrics task. Review `_metric_totals` and `_platform_comparison` platform filters to confirm whether `google_business` should be included in the aggregate totals and platform comparison cards.
