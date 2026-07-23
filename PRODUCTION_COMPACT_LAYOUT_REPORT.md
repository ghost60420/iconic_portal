# Production Compact Layout Report

Date: 2026-07-22

## Summary

Compacted the Production Detail page around the Daily Production Updates area without removing information or changing backend behavior.

No deployment was performed.

## Scope

Presentation-only changes:

- Reduced vertical density for Daily Production Updates.
- Reduced nearby spacing around Size Breakdown, Line Specifications, and Linked Material Specifications.
- Kept all existing text, status badges, fields, buttons, and workflow controls visible.
- Kept production backend logic, forms, permissions, stage rules, calculations, and database schema unchanged.

## Files Changed For This Layout Work

- `crm/templates/crm/production_detail.html`
  - Added scoped panel classes: `production-lines-panel` and `production-daily-panel`.
  - Removed template whitespace from the QC notes output so `white-space` no longer creates a tall blank card.
  - Preserved QC note line breaks with `linebreaksbr`.

- `static/crm/production_density.css`
  - Added scoped compact rules for Production Lines and Daily Production Updates.
  - Desktop Daily Production Updates uses three compact columns.
  - Tablet uses two columns.
  - Mobile uses one column.
  - Reduced margins, gaps, badge sizes, and daily report form spacing in the targeted area.

- `crm/templates/crm/partials/production_density_assets.html`
  - Bumped the `production_density.css` cache token for the future deploy.

The worktree also still contains the previously approved stage tracker button fix.

## Root Cause Of Excess Height

The Daily Production Updates cards use `.production-action-grid strong`, which preserves whitespace. The QC Notes template placed the `for` loop and `if` block across multiple indented lines inside `<strong>`, so the rendered card preserved template whitespace as blank vertical space. CSS grid then stretched the other two cards to match the QC card height.

## Before And After Measurements

Measured with Python Playwright against the real local page on `DJANGO_DEBUG=1`.

| Viewport | Page Height Before | Page Height After | Daily Section Before | Daily Section After | Daily Reduction | After Card Heights |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 1440 | 4830px | 4440px | 667px | 341px | 48.9% | 180 / 180 / 180px |
| 768 | 6899px | 6417px | 959px | 540px | 43.7% | 150 / 150 / 150px |
| 390 | 9400px | 8848px | 1048px | 566px | 46.0% | 58 / 58 / 58px |

Desktop target of about 180 to 220px for the three Daily Production Updates cards was met.

## Screenshots

- `test_artifacts/production_compact_layout/after/1440.png`
- `test_artifacts/production_compact_layout/after/768.png`
- `test_artifacts/production_compact_layout/after/390.png`
- `test_artifacts/production_compact_layout/after/1440_daily.png`
- `test_artifacts/production_compact_layout/after/768_daily.png`
- `test_artifacts/production_compact_layout/after/390_daily.png`

## Responsive Verification

- Desktop 1440: three compact Daily Production Updates columns.
- Tablet 768: two columns where suitable, with QC Notes on the second row.
- Mobile 390: one column, no document-level horizontal overflow.
- Daily report controls now sit directly under the status badges.
- Text remained readable in all tested widths.

## Test Results

Passed:

- `python3 manage.py check`
- `python3 manage.py makemigrations --check --dry-run`
- `python3 -m py_compile crm/views.py`
- `python3 manage.py test crm.tests.test_production_po_display crm.tests.test_production_order_from_quotation.ProductionOrderFromQuotationTests.test_production_detail_displays_approved_quotation_snapshot crm.tests.test_local_sewing.LocalSewingPermissionTests`
- `python3 manage.py test crm.tests`
- `git diff --check`

Full CRM regression:

- 559 tests passed.

## Performance

- Query count before compact layout: 46 on the same Production Detail view.
- Query count after compact layout: 46.
- Cold local response time after: 741.60 ms.
- Warm local response time after: 44.73 ms.
- N+1 verification: no new database query path was introduced. The changes are CSS and template presentation only.

The Production Detail page remains above the project detail-page query target, but this compaction did not add queries or backend work.

## Risk Notes

- No models changed.
- No migrations required.
- No production records modified.
- No production calculations changed.
- No stage rules changed.
- No form fields or permissions changed.
- No deploy performed.

## Deployment Recommendation

Safe for visual review. After approval, deploy as a low-risk static/template update.

Recommended future deployment sequence:

1. Pull reviewed branch.
2. Run `python3 manage.py check`.
3. Run `python3 manage.py migrate` only as part of the standard deploy sequence; this change has no migration.
4. Collect/static-refresh if required by the current production deployment process.
5. Restart Gunicorn only.
6. Smoke test Production Detail at desktop and mobile widths.

Rollback:

1. Revert the compact-layout commit.
2. Restore the previous density CSS cache token.
3. Restart Gunicorn and refresh static assets if applicable.
