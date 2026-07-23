# Production Stage Button Fix Report

Date: 2026-07-22

## Summary

Fixed the Production Detail stage tracker so all eight visual stage cards render an `Edit stage` control:

- Sampling
- Fabric
- Cutting
- Printing
- Sewing
- QC
- Packing
- Shipping

No deployment was performed.

## Root Cause

The Production Detail visual tracker is built from `production_visual_stages`, not directly from the raw `ProductionStage` table.

Sampling, Cutting, Sewing, QC, Packing, and Shipping were backed by real `ProductionStage` rows, so each card had `card.stage` and the template rendered:

```django
{% if card.stage %}
  <a href="{% url 'production_stage_edit' card.stage.id %}">Edit stage</a>
{% endif %}
```

Fabric and Printing are intentionally inferred workflow cards today. The operational-status service documents that `ProductionStage` has no stored fabric sourcing or printing stage; those states are inferred from fabric fields, print-related order notes, and surrounding stage progress. Because those two cards had no `card.stage`, the template condition hid their edit buttons.

## Files Changed

- `crm/views.py`
  - Added `edit_url` to the shared `_production_stage_card` namespace.
  - Real stage-backed cards keep using `production_stage_edit`.
  - Inferred Fabric and Printing cards use the existing `production_edit` route as their safe edit target.
  - Renamed the final visual card label from `Shipment` to `Shipping` to match the requested eight-stage tracker.

- `crm/templates/crm/production_detail.html`
  - Removed the `card.stage` display guard.
  - Every visual stage card now renders `<a href="{{ card.edit_url }}">Edit stage</a>`.

- `crm/tests/test_production_po_display.py`
  - Added a focused regression asserting the exact eight visual card labels and eight rendered `Edit stage` links.

## Migration

No migration is required.

This fix does not add production stage rows, alter choices, or modify existing production records. It preserves the current inferred Fabric/Printing workflow and avoids changing Next Stage sequencing.

## Before Behavior

- Fabric card rendered status, timestamp, and assigned user, but no `Edit stage` button.
- Printing card rendered status, timestamp, and assigned user, but no `Edit stage` button.
- Other visual tracker cards showed the button because they had a linked `ProductionStage`.

## After Behavior

- All eight visual tracker cards render status, timestamp, assigned user, and `Edit stage`.
- Stage-backed cards still open the existing stage edit page.
- Inferred Fabric and Printing cards open the existing production edit page.
- Production permissions are unchanged because no URL permission wrappers were changed.
- No costing, invoice, payment, accounting, or production workflow calculations were changed.

## Browser Verification

Local server: `DJANGO_DEBUG=1 python3 manage.py runserver 127.0.0.1:8038`

Verified with Python Playwright against the real page:

- CSS loaded: `static/crm/production_detail.css` returned HTTP 200.
- Desktop `1440x1100`: 8 labels, 8 edit links.
- Tablet `820x1100`: 8 labels, 8 edit links.
- Mobile `390x1200`: 8 labels, 8 edit links.
- Mobile document width stayed within viewport width.
- The existing mobile tracker remains a horizontal scroller (`overflow-x: auto`), and the added edit links remain visible and tappable inside each card.

Screenshots:

- `test_artifacts/production_stage_button_fix/desktop_css.png`
- `test_artifacts/production_stage_button_fix/tablet_css.png`
- `test_artifacts/production_stage_button_fix/mobile_css.png`
- `test_artifacts/production_stage_button_fix/mobile_tracker_css.png`

## Test Results

Passed:

- `python3 -m py_compile crm/views.py`
- `python3 manage.py check`
- `python3 manage.py makemigrations --check --dry-run`
- `python3 manage.py test crm.tests.test_production_po_display.ProductionPurchaseOrderDisplayTests.test_stage_progress_tracker_shows_edit_stage_for_all_visual_cards`
- `python3 manage.py test crm.tests.test_production_po_display`
- `python3 manage.py test crm.tests.test_production_order_from_quotation`
- `python3 manage.py test crm.tests.test_production_operational_status`
- `python3 manage.py test crm.tests`
- `git diff --check`

Full CRM regression result:

- 559 tests passed.

## Performance

- Query count before: not re-run against a pre-patch checkout; the changed code path previously did not add ORM queries.
- Query count after: 46 queries for the local production detail page.
- Cold local response time: 639.68 ms.
- Warm local response time: 31.73 ms.
- N+1 verification: no new query loop was introduced. The edit URLs are generated from already-prefetched stage objects and the current order primary key.

The production detail page remains above the project detail-page query target, but this is pre-existing page breadth, not introduced by this fix.

## Risk Notes

- No database records are created or modified by viewing the page.
- No production stage sequence or Next Stage logic was changed.
- No permission wrappers were changed.
- Fabric and Printing remain inferred visual workflow cards, consistent with the current operational-status service.

## Deployment Recommendation

Safe to deploy after review as a low-risk template/view fix.

Recommended deployment order:

1. Pull the reviewed branch.
2. Run `python3 manage.py check`.
3. Run `python3 manage.py migrate` only as part of the normal deploy process; this change has no migration.
4. Restart Gunicorn only.
5. Smoke test Production Detail on desktop and mobile for one production order.

Rollback:

1. Revert the commit containing this report and the three changed source/test files.
2. Restart Gunicorn.
3. Reopen the same Production Detail page and verify the prior tracker rendering returns.
