# UI Density Phase 1 Report

## Scope

Phase 1 only:

- Shared compact spacing tokens
- Main Dashboard compact header/toolbar
- Main Dashboard KPI cards
- Two dashboard density modes only: Comfortable and Compact

No backend logic, models, migrations, URLs, permissions, formulas, workflow logic, database records, collectstatic, deployment, commit, or push.

## Branch State

- Source safe branch recorded before work: `codex/ui-modernization-rc2-safe`
- Source commit recorded before work: `bca6156699ca8238437bcdcf241eba2719b8a32e`
- New working branch: `codex/ui-density-phase1`
- New branch base commit: `bca6156699ca8238437bcdcf241eba2719b8a32e`
- Tracked file count recorded before work: 806
- Pre-existing source checkout local status recorded before branch creation:
  - `M static/crm/ui_system.css`
  - `?? POST_DEPLOYMENT_MONITORING_REPORT.md`
  - `?? POST_DEPLOYMENT_REPORT.md`
- Phase 1 was done in a clean worktree so those pre-existing local files were not carried into this branch.

## Files Changed

- `crm/templates/crm/main_dashboard.html`
- `static/crm/dashboard.js`
- `static/crm/ui_system.css`
- `UI_DENSITY_PHASE1_REPORT.md`

## Protected File Audit

No protected backend files appear in the diff.

Confirmed absent from the diff:

- `models.py`
- `views.py`
- `views_costing.py`
- `views_invoice.py`
- `urls.py`
- `settings.py`
- `services/**`
- `migrations/**`
- `management/commands/**`
- costing, invoice, shipment, accounting, approval, and production workflow logic

## Behavior Added

- Activated the shared dashboard shell class on Main Dashboard.
- Converted the dashboard density control to two modes only:
  - Comfortable
  - Compact
- Dashboard density preference remains browser-local via `localStorage`.
- Legacy `iconic.dashboard.compact` preference is read once for compatibility and then cleared when the user changes density.
- Compact mode reduces shared spacing, card padding, row height, control height, and KPI height using CSS variables only.
- Dashboard top section is now a compact toolbar while preserving title, period chips, alerts, date range, quick actions, AI action, user menu, widget customization, and density control.
- Dashboard KPI cards are shorter and arranged as a compact row on desktop where width allows.
- Lower dashboard sections keep existing lazy containment behavior to avoid mobile scroll regression.

## Query Comparison

Comparable authenticated Django test-client request:

| Metric | Before | After |
| --- | ---: | ---: |
| Status | 200 | 200 |
| Queries | 96 | 96 |
| Render time | 118.12 ms | 122.13 ms |
| Response bytes | 106,974 | 106,993 |

After warmed repeat:

| Metric | After warm |
| --- | ---: |
| Status | 200 |
| Queries | 94 |
| Render time | 50.30 ms |

No new backend queries were introduced. This phase is frontend-only.

## Browser Measurements

| Viewport | Scroll Height Before | Scroll Height After | Reduction | Topbar Before -> After | KPI Grid Before -> After |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1440 | 8,868 | 6,323 | 28.7% | 304 -> 119 | 750 -> 238 |
| 1024 | 9,630 | 7,506 | 22.1% | 423 -> 270 | 1,817 -> 484 |
| 768 | 11,374 | 8,823 | 22.4% | 539 -> 362 | 3,426 -> 730 |
| 430 | 11,454 | 8,673 | 24.3% | 617 -> 408 | 3,428 -> 1,386 |
| 390 | 11,504 | 8,673 | 24.6% | 665 -> 408 | 3,430 -> 1,386 |

Compact mode at 1440:

- Scroll height: 5,805
- Topbar height: 90
- KPI grid height: 208
- First KPI card height: 64
- `data-density`: `compact`
- `localStorage` density: `compact`

## Browser Safety Checks

Checked at 1440, 1024, 768, 430, and 390 widths:

- Horizontal overflow: none
- Duplicate IDs: none
- Console errors: none
- Forms preserved: 3 before, 3 after
- Buttons preserved: 10 before, 10 after
- Links preserved: 104 before, 104 after
- CSRF inputs preserved in browser DOM: 1 before, 1 after
- Dashboard CSS loaded: yes
- Shared UI CSS loaded: yes
- KPI displayed values unchanged
- Financial KPI displayed values unchanged
- Dashboard density toggle works
- Legacy compact localStorage key is not retained after density change

Charts:

- 15 canvas elements present before and after.
- 4 Chart.js instances detected in the browser pass.
- 11 chart shells remained marked `.is-loading` both before and after; this appears pre-existing and unchanged by this phase.

Tabs:

- Main Dashboard browser pass detected 0 dashboard tab controls before and after. No tab behavior changed in Phase 1.

## Screenshot Paths

Before:

- `/tmp/iconic_density_phase1/screenshots/before_dashboard_1440.png`
- `/tmp/iconic_density_phase1/screenshots/before_dashboard_1024.png`
- `/tmp/iconic_density_phase1/screenshots/before_dashboard_768.png`
- `/tmp/iconic_density_phase1/screenshots/before_dashboard_430.png`
- `/tmp/iconic_density_phase1/screenshots/before_dashboard_390.png`

After:

- `/tmp/iconic_density_phase1/screenshots/after_dashboard_1440.png`
- `/tmp/iconic_density_phase1/screenshots/after_dashboard_1024.png`
- `/tmp/iconic_density_phase1/screenshots/after_dashboard_768.png`
- `/tmp/iconic_density_phase1/screenshots/after_dashboard_430.png`
- `/tmp/iconic_density_phase1/screenshots/after_dashboard_390.png`
- `/tmp/iconic_density_phase1/screenshots/after_compact_dashboard_1440.png`

## Test Results

Passed:

- `DJANGO_SECRET_KEY=local-density-phase1 python3 manage.py check --settings=iconic_site.settings_testdb`
- `DJANGO_SECRET_KEY=local-density-phase1 python3 manage.py makemigrations --check --dry-run --settings=iconic_site.settings_testdb`
- `git diff --check`
- `git diff --cached --check`
- Focused dashboard tests:
  - `crm.tests.test_operations_control_center.DashboardAndRoleTests.test_dashboard_renders_operations_sections`
  - `crm.tests.test_operations_control_center.DashboardAndRoleTests.test_dashboard_has_clickable_metric_cards`
  - `crm.tests.test_active_pipeline_cleanup.ActivePipelineCleanupTests.test_main_dashboard_active_counts_exclude_completed_and_converted_records`
  - `crm.tests.test_internal_costing_permissions.InternalCostingPermissionTests.test_restricted_lifecycle_and_dashboard_hide_profit_metrics`
  - `crm.tests.test_local_sewing.LocalSewingWorkflowTests.test_main_dashboard_and_report_show_separate_local_totals`
- Full CRM regression:
  - `DJANGO_SECRET_KEY=local-density-phase1 python3 manage.py test crm.tests`
  - Result: 475 tests passed

## Risk Report

Low risk:

- Changes are limited to template, CSS, JavaScript, and documentation.
- No backend, database, migration, URL, permission, formula, or workflow files changed.
- Query count is unchanged on comparable request.
- Main dashboard forms, buttons, links, CSRF token presence, KPI text, and financial values were preserved.
- No horizontal overflow or duplicate IDs were detected at tested desktop, tablet, and mobile widths.

Remaining risk:

- Activating the dashboard shell affects dashboard presentation beyond only the first KPI strip, but only through CSS.
- Some chart shells remain marked loading before and after; this is unchanged from baseline but should be reviewed separately if chart loading indicators are visible in production.
- Screenshots were generated from the local copied rehearsal database, not production.

## Deployment Recommendation

Do not deploy yet.

Phase 1 is ready for review. Continue only after screenshot and report approval.
