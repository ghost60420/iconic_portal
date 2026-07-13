# UI Density Phase 2 Visual Review

Status: visual review only. No deployment, no commit, no backend changes.

Branch reviewed: `codex/ui-density-phase2`

Baseline: approved Phase 1 commit `64774ab76fdfe5901e0da1de715a4a2912630435`

Pages reviewed:

- Lead Detail
- Opportunity Detail

Viewports reviewed:

- 1440 desktop
- 1024 tablet
- 768 tablet
- 430 mobile
- 390 mobile

## Screenshot Locations

Screenshot root:

`/tmp/iconic_density_phase2_visual_review/screenshots/`

Side-by-side comparison sheets:

- `/tmp/iconic_density_phase2_visual_review/contact_sheets/lead_viewport_comparison.png`
- `/tmp/iconic_density_phase2_visual_review/contact_sheets/lead_full_comparison.png`
- `/tmp/iconic_density_phase2_visual_review/contact_sheets/opportunity_viewport_comparison.png`
- `/tmp/iconic_density_phase2_visual_review/contact_sheets/opportunity_full_comparison.png`

Metric data:

- `/tmp/iconic_density_phase2_visual_review/visual_metrics.json`

## Measurement Summary

| Page | Viewport | Scroll Before | Scroll After | Reduction | Cards Above Fold | Info Items Above Fold | Header Before | Header After | Header Reduction | Sidebar Use |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Lead Detail | 1440 | 5039 | 4667 | 7.4% | 0 -> 3 | 2 -> 12 | 174 | 146 | 16.1% | 97% |
| Lead Detail | 1024 | 6864 | 5838 | 14.9% | 0 -> 0 | 2 -> 2 | 212 | 142 | 33.0% | n/a |
| Lead Detail | 768 | 7245 | 6195 | 14.5% | 0 -> 0 | 2 -> 2 | 255 | 142 | 44.3% | n/a |
| Lead Detail | 430 | 8120 | 7616 | 6.2% | 0 -> 1 | 2 -> 3 | 246 | 159 | 35.4% | 100% |
| Lead Detail | 390 | 8178 | 7650 | 6.5% | 0 -> 1 | 2 -> 3 | 246 | 159 | 35.4% | 100% |
| Opportunity Detail | 1440 | 4653 | 4011 | 13.8% | 1 -> 7 | 6 -> 12 | 211 | 152 | 28.0% | n/a above fold |
| Opportunity Detail | 1024 | 6179 | 5272 | 14.7% | 1 -> 1 | 6 -> 6 | 284 | 187 | 34.2% | n/a |
| Opportunity Detail | 768 | 8641 | 7323 | 15.3% | 1 -> 1 | 6 -> 6 | 330 | 251 | 23.9% | n/a |
| Opportunity Detail | 430 | 10473 | 8746 | 16.5% | 0 -> 0 | 5 -> 5 | 657 | 531 | 19.2% | n/a |
| Opportunity Detail | 390 | 10619 | 8948 | 15.7% | 0 -> 0 | 5 -> 5 | 694 | 571 | 17.7% | n/a |

Notes:

- Document-level horizontal overflow stayed false on every tested viewport.
- Console errors stayed at zero in the visual browser pass.
- Lead sidebar utilization is strong on desktop because quick actions and summary facts fit in the right column.
- Opportunity sidebar starts lower in the page, so it does not contribute much above the first fold. The action bar in the hero remains the primary action surface.

## Lead Detail Visual Review

### Hero Section

Verdict: improved, but can be denser.

The hero is shorter at every viewport, with the strongest reduction on tablet and mobile. Desktop reduction is more modest at 16.1%. The hero still reads as a large display section instead of a compact CRM record header.

Recommended next adjustment:

- Convert the hero into a tighter record header row.
- Keep `Lead ID`, customer name, owner/status pills, and primary action entry point.
- Move less critical metadata into the right sidebar or a compact details row.

### Action Buttons

Verdict: still too large on mobile; acceptable on desktop.

Desktop quick actions feel organized in the right sidebar. Mobile shows the preserved action stack clearly, but the buttons are visually heavy and consume too much vertical space.

Recommended next adjustment:

- Keep the same actions.
- Make mobile action buttons shorter.
- Use a two-column layout for safe non-destructive actions on 390-430px widths where labels fit.
- Keep destructive archive/delete actions separated.

### Empty Spacing

Verdict: improved but not enterprise-dense yet.

The desktop first fold now shows more useful information, but later sections still have card padding and vertical gaps that feel closer to a showcase layout than a dense operational CRM.

Recommended next adjustment:

- Reduce lower section padding by another 8-12%.
- Convert repeated label/value cards into compact definition-grid rows.

### Workflow Timeline

Verdict: can compress further.

The timeline remains readable and preserved, but cards are tall compared with Salesforce/HubSpot-style progress components.

Recommended next adjustment:

- Reduce timeline item padding and internal text stack.
- Use smaller badges and tighter connector spacing.
- Keep the current workflow order and URLs unchanged.

### Product Snapshot And Contact Cards

Verdict: can become smaller.

The order summary and product/contact cards are still visually large. They should become more like compact enterprise fact panels.

Recommended next adjustment:

- Use two-column label/value facts on desktop.
- Use compressed mobile rows rather than full-height cards.
- Keep images and links unchanged.

### Right Sidebar

Verdict: direction is right, but needs hierarchy refinement.

The sidebar feels more enterprise-level than the previous layout because actions, status, contact, and warnings are grouped. It should become less button-heavy and more like an operational command rail.

Recommended next adjustment:

- Keep primary action buttons visible.
- Group secondary links into a compact action group.
- Keep status/contact/warning facts visible on desktop.
- On mobile, continue avoiding duplicate read-only summary blocks.

## Opportunity Detail Visual Review

### Summary Cards

Verdict: improved but still oversized below the hero.

At 1440px, visible cards above fold improved from 1 to 7, which is the strongest visual win in this phase. Tablet and mobile still show limited above-fold gains because the hero/action section remains large.

Recommended next adjustment:

- Reduce snapshot card min-heights.
- Use smaller badge rows and tighter card gaps.
- Convert the six snapshot cards into a compact KPI strip on desktop.

### Section Spacing

Verdict: improved but can be denser.

The section rhythm is cleaner, but there is still too much space between hero, lifecycle, summary, workflow, product snapshot, and workspace.

Recommended next adjustment:

- Tighten the vertical stack before the main workspace.
- Collapse secondary lifecycle/help copy when status is not urgent.
- Keep warnings visible.

### Financial Cards

Verdict: need another density pass.

Financial/costing cards remain readable but not compact enough for daily enterprise scanning.

Recommended next adjustment:

- Use compact three-column financial rows on desktop.
- Reduce label and helper text line-height.
- Keep all values, links, approvals, and exports unchanged.

### Quotation, Invoice, Production Actions

Verdict: visible and preserved.

The action bar still makes `Create Quote`, `Create Costing`, `Quick Costing`, `Create Invoice`, and production actions easy to find. Mobile preserves the same actions, though the stack is tall.

Recommended next adjustment:

- Keep these actions prominent.
- On mobile, consider a compact primary/secondary action grouping without hiding actions.
- Keep archive/destructive actions visually separate.

### Sticky Sidebar

Verdict: implemented but not visually maximized.

The sidebar has sticky positioning, but on Opportunity Detail it begins below the above-fold stack. That means it does not help the first screen as much as the Lead Detail sidebar does.

Recommended next adjustment:

- Move the highest-value opportunity side facts closer to the top of the page, or make the hero itself behave more like the right command rail.
- Keep quotation, invoice, and production actions visible in the hero.

## Recommendation

Recommendation: **B. Make denser**

Do not revert. The current Phase 2 layout is safer and more organized than the previous stacked layout, and all technical safety checks passed. However, it does not yet reach the target density of HubSpot Enterprise, Salesforce Lightning, Monday CRM, ClickUp, or Zoho CRM.

Recommended visual direction:

- Keep the two-column desktop structure.
- Keep the preserved action surfaces.
- Tighten the hero further.
- Reduce mobile action height.
- Convert repeated large cards into compact fact grids.
- Compress workflow timelines and financial cards.

## Phase 3 Plan

### Main Dashboard

Goal: maximize above-fold operational visibility.

Plan:

- Convert header into a thinner enterprise command bar.
- Use one compact KPI strip across desktop.
- Reduce chart/widget padding.
- Collapse lower-priority analytics by default.
- Preserve all widgets, charts, links, filters, IDs, and dashboard actions.

### Customer Detail

Goal: turn long history into a compact account workspace.

Plan:

- Keep the existing tab structure.
- Add a right-side account command rail on desktop.
- Reduce history card height.
- Convert related records into denser tables.
- Preserve all customer actions, archive controls, links, forms, files, notes, and AI panels.

### Production Detail

Goal: keep stage visibility while reducing operational scroll.

Plan:

- Keep stage tracker sticky.
- Reduce stage card height and file panel padding.
- Keep shipment, invoice, costing, approval, upload, and AI sections.
- Use compact side facts for PO, customer, quantity, due dates, and shipment status.

### Invoice Detail

Goal: preserve finance safety while improving scan speed.

Plan:

- Keep payment and approval forms unchanged.
- Use a compact financial KPI row.
- Convert payment/history/production/shipment sections into denser tab panels.
- Keep internal profit values, invoice totals, taxes, payment records, and accounting links unchanged.

### Accounting Pages

Goal: dense financial operations view without touching formulas.

Plan:

- Compact financial KPI strip at top.
- Sticky filters and table headers.
- Reduce report card padding.
- Use denser ledger rows.
- Preserve all calculations, exports, filters, IDs, forms, permissions, and report links.

## Safety Notes

- No backend files changed for this visual review.
- No deployment performed.
- No commit performed.
- No migration, collectstatic, or service restart performed.
- Screenshots were generated from local dev servers using copied local DB state only.
