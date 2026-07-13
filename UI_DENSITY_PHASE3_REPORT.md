# UI Density Phase 3 Report

Status: ready for visual review, not deployed.

Branch: `codex/ui-density-phase3-enterprise`
Base commit: `64774ab76fdfe5901e0da1de715a4a2912630435`

## Scope

Phase 3 was kept frontend-only. No backend, model, migration, service, URL, permission, workflow, costing, invoice, production, shipment, accounting, or approval logic files were changed.

Pages visually covered:

- Main Dashboard
- Lead Detail
- Opportunity Detail
- Customer Detail
- Production Detail
- Invoice Detail
- Canada Accounting landing view reached from `/accounting/`

## Files Changed

- `crm/templates/crm/accounting_ca_master.html`
- `crm/templates/crm/lead_detail.html`
- `crm/templates/crm/opportunity_detail.html`
- `static/crm/customer_detail.css`
- `static/crm/lead_detail.css`
- `static/crm/opportunity_detail.css`
- `static/crm/production_detail.css`
- `static/crm/ui_system.css`

Protected files changed: none.

## Visual Work Completed

- Reduced shared card padding, section spacing, table density, dashboard KPI height, and panel radius through `ui_system.css`.
- Compressed dashboard topbar and KPI cards for desktop, tablet, and mobile.
- Converted customer and production hero areas into denser enterprise headers.
- Added sticky right-column behavior for customer and production side panels on desktop.
- Tightened lead and opportunity detail density while preserving the existing two-column enterprise layout from Phase 2.
- Converted the Canada accounting page-local CSS to a compact two-panel layout without changing forms or accounting behavior.
- Added mobile corrections after visual review:
  - Dashboard mobile toolbar now renders as a compact horizontal control strip.
  - Lead mobile action rail now stacks full-width instead of becoming a narrow side column.

## Screenshot Outputs

Contact sheets:

- `/tmp/iconic_density_phase3_enterprise/contact_sheets/dashboard_viewport_comparison.png`
- `/tmp/iconic_density_phase3_enterprise/contact_sheets/lead_viewport_comparison.png`
- `/tmp/iconic_density_phase3_enterprise/contact_sheets/opportunity_viewport_comparison.png`
- `/tmp/iconic_density_phase3_enterprise/contact_sheets/customer_viewport_comparison.png`
- `/tmp/iconic_density_phase3_enterprise/contact_sheets/production_viewport_comparison.png`
- `/tmp/iconic_density_phase3_enterprise/contact_sheets/invoice_viewport_comparison.png`
- `/tmp/iconic_density_phase3_enterprise/contact_sheets/accounting_viewport_comparison.png`

Raw screenshots and metrics:

- `/tmp/iconic_density_phase3_enterprise/screenshots/`
- `/tmp/iconic_density_phase3_enterprise/before_metrics.json`
- `/tmp/iconic_density_phase3_enterprise/after_metrics.json`

Captured viewports:

- 1440 x 1100 desktop
- 1024 x 900 tablet
- 768 x 900 tablet
- 430 x 900 mobile
- 390 x 844 mobile

## Measurement Summary

Average scroll reduction is measured across 1440, 1024, 768, 430, and 390 viewports.

| Page | Avg Scroll Reduction | 1440 Scroll | 390 Scroll | 1440 Cards Above Fold | 390 Cards Above Fold |
| --- | ---: | ---: | ---: | ---: | ---: |
| Dashboard | 4.7% | 5.2% | 4.0% | 22 -> 22 | 3 -> 18 |
| Lead Detail | 14.9% | 14.2% | 13.7% | 3 -> 6 | 1 -> 0 |
| Opportunity Detail | 22.3% | 10.9% | 23.3% | 7 -> 1 | 0 -> 1 |
| Customer Detail | 26.3% | 27.5% | 25.2% | 2 -> 12 | 0 -> 2 |
| Production Detail | 19.3% | 20.2% | 19.3% | 1 -> 20 | 0 -> 0 |
| Invoice Detail | 9.7% | 12.8% | 9.2% | 0 -> 1 | 0 -> 0 |
| Accounting | 15.6% | 0.0% | 28.6% | 0 -> 2 | 0 -> 2 |

Header compression highlights:

- Dashboard mobile header: `408px -> 101px`, 75.2% reduction.
- Lead desktop header: `146px -> 74px`, 49.3% reduction.
- Opportunity mobile header: `571px -> 295px`, 48.3% reduction at 390.
- Customer desktop header: `199px -> 142px`, 28.6% reduction.
- Production desktop header: `227px -> 149px`, 34.4% reduction.

Notes:

- Dashboard, customer, and production show the strongest above-the-fold card gains.
- Text-node density drops on some pages because lower-priority descriptions were shortened or hidden at small widths; action visibility and card visibility increased.
- Production mobile hero height remains nearly unchanged because all production actions are preserved and visible.
- Accounting desktop reports 0.0% scroll reduction at 1440/1024 because the page already fits within the viewport; mobile scroll reduced by 26.0%-28.6%.

## Browser Safety Results

Measured across all listed pages and viewports:

- Horizontal overflow: none.
- Duplicate IDs: none detected.
- JavaScript console warnings/errors: none detected.
- CSRF tokens: present on pages with POST forms.
- Forms and action links remained rendered.
- Mobile: no page-level horizontal overflow at 430 or 390.

## Query and Logic Safety

No query-bearing files were changed. This phase only modifies templates and CSS. No view functions, querysets, services, models, migrations, URLs, permission checks, workflow code, or financial logic were touched.

## Tests

Commands run:

```bash
DJANGO_SECRET_KEY=local-density-phase3 python3 manage.py check --settings=iconic_site.settings_testdb
DJANGO_SECRET_KEY=local-density-phase3 python3 manage.py makemigrations --check --dry-run --settings=iconic_site.settings_testdb
git diff --check
git diff --cached --check
DJANGO_SECRET_KEY=local-density-phase3 python3 manage.py test crm.tests.test_dashboard_and_misc crm.tests.test_customer_workflow_improvements crm.tests.test_invoice_from_opportunity crm.tests.test_production_profit_report crm.tests.test_production_po_display crm.tests.test_accounting_rbac crm.tests.test_workflow_safety_updates
DJANGO_SECRET_KEY=local-density-phase3 python3 manage.py test crm.tests
```

Results:

- `manage.py check`: passed.
- `makemigrations --check --dry-run`: passed, no changes detected.
- `git diff --check`: passed.
- `git diff --cached --check`: passed.
- Focused tests: 67 tests passed.
- Full CRM regression: 475 tests passed.

## Risks

- Phase 3 is visually broader than Phase 1 and Phase 2 because shared density tokens affect multiple modernized pages.
- Opportunity 1024 hero height increased slightly while total page scroll dropped 27.1%; this is due to preserving all action buttons and badges in a tablet-width row.
- Some first-screen description text is intentionally reduced to fit more operational controls and cards above the fold.
- Screenshots were generated against local test data, not production data.

## Deployment Recommendation

Do not deploy until reviewed.

Technical recommendation: ready for visual review. If approved, this can be bundled with the existing UI density work as a frontend-only deployment after the usual production backup, collectstatic, browser smoke test, and rollback plan.
