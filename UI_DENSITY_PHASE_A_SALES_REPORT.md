# Phase A Sales UI Density Report

Date: 2026-07-13
Branch: `codex/ui-density-phase-a-sales`
Base commit: `195bb11d546c93c2278a9351d0cb800a8fa9bd18`
Deployment status: Not deployed

## Scope

Phase A applies frontend-only enterprise density improvements to Sales pages:

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

No backend logic, models, migrations, URLs, permissions, workflows, formulas, costing logic, invoice logic, production logic, or accounting logic were changed.

## Files Changed

- `crm/templates/crm/costing/ceo_quotation_approval_queue.html`
- `crm/templates/crm/costing/costsheet_form.html`
- `crm/templates/crm/costing/quick_costing_detail.html`
- `static/crm/costing_list.css`
- `static/crm/customer_detail.css`
- `static/crm/customers_list.css`
- `static/crm/lead_detail.css`
- `static/crm/leads_list.css`
- `static/crm/opportunities_list.css`
- `static/crm/opportunity_detail.css`

## Protected Files

No protected backend files are changed:

- No `models.py`
- No `views.py`
- No `urls.py`
- No services
- No migrations
- No settings
- No management commands
- No costing, invoice, approval, accounting, production, shipment, or workflow logic

## Behavior Added

- Shorter Sales page headers and command bars.
- Denser KPI/stat cards.
- Reduced card padding and section gaps.
- Compact filters and inputs.
- Compact table rows with sticky headers preserved.
- Smaller list-page product/customer imagery.
- Desktop side panels remain sticky where existing page structure supports them.
- Mobile cards and single-column layouts remain available.
- Page-level horizontal overflow eliminated at tested breakpoints.

## Screenshot Package

Generated screenshot root:

- `/tmp/ui_density_phase_a_sales/`

Contact sheets:

- `/tmp/ui_density_phase_a_sales/contact_sheet_desktop_1440.png`
- `/tmp/ui_density_phase_a_sales/contact_sheet_tablet_768.png`
- `/tmp/ui_density_phase_a_sales/contact_sheet_mobile_390.png`

Full screenshot directories:

- Before: `/tmp/ui_density_phase_a_sales/before/screenshots/`
- After: `/tmp/ui_density_phase_a_sales/after/screenshots/`

Screenshot count:

- 120 PNG files total
- 30 after full-page screenshots
- 30 after first-viewport screenshots

## Query Comparison

All measured Sales page query counts stayed unchanged.

| Page | Before | After |
|---|---:|---:|
| Leads List | 9 | 9 |
| Lead Detail | 45 | 45 |
| Opportunities List | 5 | 5 |
| Opportunity Detail | 51 | 51 |
| Customers List | 7 | 7 |
| Customer Detail | 29 | 29 |
| Quick Costing List | 4 | 4 |
| Quick Costing Form | 6 | 6 |
| Quick Costing Detail | 18 | 18 |
| Approval Queue | 4 | 4 |

## Scroll And Density Measurements

Measured viewports:

- Desktop: 1440 x 1000
- Tablet: 768 x 1000
- Mobile: 390 x 900

Average scroll reduction:

- Desktop: 11.0%
- Tablet: 14.8%
- Mobile: 16.1%

Average header height reduction, excluding pages without measurable hero header:

- Desktop: 26.7%
- Tablet: 22.1%
- Mobile: 19.1%

Highest reductions:

- Quick Costing List mobile: 36.3%
- Customers List tablet: 34.8%
- Customers List mobile: 31.3%
- Leads List mobile: 25.0%
- Quick Costing Detail desktop: 21.1%

Detail-page note:

- Lead Detail, Opportunity Detail, and Customer Detail were already heavily compacted in earlier density phases. Phase A further tightened them, but preserving all existing forms, history, AI sections, workflow panels, financial panels, and action rails limits total scroll reduction.
- Opportunity Detail mobile changed by -0.4% after keeping the nested Quick Costing table usable as an internal horizontal scroller. No horizontal page overflow remains.

Detailed metrics:

- `/tmp/ui_density_phase_a_sales/comparison_metrics.csv`

## Browser Safety Results

At 1440, 768, and 390 widths:

- Horizontal overflow: none detected
- Duplicate IDs: none detected
- JavaScript console errors: none detected
- Forms preserved: yes
- Buttons/actions count preserved: yes
- Links count preserved: yes
- CSRF tokens preserved: yes
- Mobile layouts render without page-level horizontal scrolling

## Test Results

Passed:

- `DJANGO_SECRET_KEY=local-phase-a python3 manage.py check`
- `DJANGO_SECRET_KEY=local-phase-a python3 manage.py makemigrations --check --dry-run`
- `git diff --check`
- `git diff --cached --check`
- `DJANGO_SECRET_KEY=local-phase-a python3 manage.py test crm.tests.test_active_pipeline_cleanup`
- `DJANGO_SECRET_KEY=local-phase-a python3 manage.py test crm.tests.test_customer_workflow_improvements`
- `DJANGO_SECRET_KEY=local-phase-a python3 manage.py test crm.tests.test_quick_costing`
- `DJANGO_SECRET_KEY=local-phase-a python3 manage.py test crm.tests.test_unified_ceo_approval_queue`
- `DJANGO_SECRET_KEY=local-phase-a python3 manage.py test crm.tests`

Full CRM regression:

- 475 tests passed
- 0 failures
- 0 errors

## Remaining Risks

- The detail pages still contain large historical and operational sections. Further scroll reduction would require a broader tab/collapse decision per section and explicit review of default visibility.
- Screenshot generation used the local rehearsal database through Django test rendering. It did not use production data and did not write persistent records.
- Quick Costing Detail screenshot used a temporary in-transaction Quick Costing record because the local rehearsal DB had no persisted Quick Costing rows; the transaction was rolled back.

## Recommendation

Ready for visual review.

Do not deploy until Phase A screenshots and report are approved.
