# UI Density Phase A.5 Report

## Summary

Phase A.5 was completed as UI polish only. No backend logic, models, migrations, services, URLs, permissions, formulas, workflow rules, database records, deployment scripts, collectstatic, or service restarts were touched.

The pass focused on the approved Sales detail pages:

- Lead Detail
- Opportunity Detail
- Customer Detail
- Quick Costing Detail
- CEO Quotation Approval Queue

## Files Changed In Phase A.5

- `static/crm/lead_detail.css`
- `static/crm/opportunity_detail.css`
- `static/crm/customer_detail.css`
- `crm/templates/crm/costing/quick_costing_detail.html`
- `crm/templates/crm/costing/ceo_quotation_approval_queue.html`

The current working tree also still contains previously approved Phase A Sales files:

- `crm/templates/crm/costing/costsheet_form.html`
- `static/crm/costing_list.css`
- `static/crm/customers_list.css`
- `static/crm/leads_list.css`
- `static/crm/opportunities_list.css`

Untracked documentation from earlier phases remains present and was not modified by this pass:

- `FULL_CRM_UI_LIVE_AUDIT.md`
- `POST_DEPLOYMENT_FINAL_REPORT.md`
- `UI_DENSITY_PHASE_A_SALES_REPORT.md`

## Behavior Added

- Lead Detail quick actions now use a compact 2-column action grid with wrapped labels.
- Opportunity Detail action bar now uses a compact 2-column grid and denser side rail.
- Customer Detail hero actions now use a compact 2-column grid and denser side rail.
- Quick Costing Detail actions now use a compact 2-column grid with smaller buttons and badges.
- Approval Queue mobile filters now use a compact 2-column layout, overriding the shared one-column mobile rule only on this page.
- Workflow/order summary cards on Lead and Opportunity received smaller min-height and padding.
- Customer mobile KPI summary uses 2 columns again to reduce vertical stacking.

## Preservation Check

Preserved:

- Forms
- CSRF tokens
- Hidden fields
- Form actions
- Buttons
- Links
- IDs
- Confirmation prompts
- Permissions
- Workflow behavior
- Quick Costing calculations
- Invoice behavior
- Production behavior
- Approval behavior
- Lead conversion behavior

No protected backend files are in the diff:

- No `models.py`
- No `views.py`
- No `urls.py`
- No `services/*`
- No `migrations/*`
- No `settings.py`
- No management commands
- No costing, invoice, production, shipment, accounting, or approval logic

## Screenshots

Generated locally from rendered HTML using the rehearsal database:

- Contact sheet: `/tmp/ui_density_phase_a5/phase_a5_contact_sheet.png`
- Before screenshots: `/tmp/ui_density_phase_a5/before/screenshots/`
- After screenshots: `/tmp/ui_density_phase_a5/after/screenshots/`

Representative after screenshots:

- `/tmp/ui_density_phase_a5/after/screenshots/lead_detail_desktop_1440_viewport.png`
- `/tmp/ui_density_phase_a5/after/screenshots/opportunity_detail_desktop_1440_viewport.png`
- `/tmp/ui_density_phase_a5/after/screenshots/customer_detail_desktop_1440_viewport.png`
- `/tmp/ui_density_phase_a5/after/screenshots/quick_costing_detail_desktop_1440_viewport.png`
- `/tmp/ui_density_phase_a5/after/screenshots/approval_queue_mobile_390_viewport.png`

## Metrics

Detailed metric CSV:

- `/tmp/ui_density_phase_a5/phase_a5_comparison_metrics.csv`

Local render/query metrics after A.5:

| Page | Status | Queries | Warm render |
| --- | ---: | ---: | ---: |
| Lead Detail | 200 | 47 | 95.4 ms |
| Opportunity Detail | 200 | 50 | 44.0 ms |
| Customer Detail | 200 | 29 | 33.7 ms |
| Quick Costing Detail | 200 | 18 | 24.9 ms |
| Approval Queue | 200 | 4 | 10.1 ms |

Query note: this was a UI-only pass and no query code was touched. Query counts remain backend/template-context dependent and exceed the long-term performance budget on several existing detail pages, but optimization would require backend/query work that is outside this approved scope.

Scroll-height comparison against the Phase A baseline:

| Page | Desktop | Tablet | Mobile |
| --- | ---: | ---: | ---: |
| Lead Detail | 0.3% reduction | 1.1% reduction | 1.3% reduction |
| Opportunity Detail | 8.0% reduction | 6.9% reduction | 11.5% reduction |
| Customer Detail | 1.7% reduction | 4.6% increase | 2.9% reduction |
| Quick Costing Detail | 6.5% reduction | 7.3% reduction | 13.8% reduction |
| Approval Queue | unchanged full viewport | unchanged full viewport | unchanged full viewport |

Action/filter density improvements:

- Approval Queue mobile filter changed from a tall one-column stack to a compact two-column layout.
- Detail page action groups changed to compact two-column grids where safe.
- Mobile action buttons reduced to 22px min-height in the approved detail action areas.

## Browser Results

Checked at:

- Desktop 1440
- Tablet 768
- Mobile 390

Results:

- Horizontal overflow: none detected
- Duplicate IDs: none detected
- JavaScript console errors: none detected
- Table overflow: none detected in measured viewport wrappers
- CSRF tokens: present on rendered forms
- Buttons and links: visible in compact action grids
- Mobile actions: reachable

## Test Results

Passed:

- `DJANGO_SECRET_KEY=local-phase-a python3 manage.py check`
- `DJANGO_SECRET_KEY=local-phase-a python3 manage.py makemigrations --check --dry-run`
- `git diff --check`
- `git diff --cached --check`
- `DJANGO_SECRET_KEY=local-phase-a python3 manage.py test crm.tests.test_active_pipeline_cleanup crm.tests.test_customer_workflow_improvements crm.tests.test_quick_costing crm.tests.test_unified_ceo_approval_queue`
  - 69 tests passed
- `DJANGO_SECRET_KEY=local-phase-a python3 manage.py test crm.tests`
  - 475 tests passed

Expected test-log noise appeared from mocked audit and shipment notification failure paths; the final result was `OK`.

## Limitations

- The requested total scroll reduction targets were only partially met. Hitting the Lead Detail and Customer Detail targets would require changing non-collapsible stacked content into collapsible/tabs or moving content between columns, which would be closer to redesign work and outside the Phase A.5 “do not redesign again” instruction.
- Some action-count metrics differ from the saved baseline because the Quick Costing detail screenshot uses a temporary local rollback fixture; Phase A.5 itself changed styling only and did not remove action HTML.
- Query counts remain above the long-term budget on existing detail pages. This phase did not change backend query behavior by design.

## Risk Assessment

Low code risk:

- CSS and inline template style only.
- No backend files changed.
- No migrations.
- No URL or permission changes.
- Full CRM regression passed.

Moderate visual review risk:

- Lead and Customer pages are cleaner, but total page height reduction is modest.
- Opportunity Detail improved most among the record pages but still has dense workflow content.
- Quick Costing Detail is noticeably denser, especially on mobile.

## Deployment Recommendation

Do not deploy yet. Phase A.5 is ready for visual review, but the scroll-height targets were not fully achieved under the strict no-redesign constraint. Approve this as a safe polish pass, or authorize a separate “detail content collapsibility” pass if the remaining scroll reduction targets must be met.
