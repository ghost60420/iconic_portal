# UI Density Phase 2 Report

Branch: `codex/ui-density-phase2`

Base: approved Phase 1 commit `64774ab76fdfe5901e0da1de715a4a2912630435`

Deployment status: not deployed, not pushed, not committed.

## Scope

Phase 2 was limited to:

- Lead Detail
- Opportunity Detail

No backend logic, models, migrations, URLs, permissions, workflows, formulas, costing logic, invoice logic, production logic, approval logic, or accounting logic were changed.

## Files Changed

- `crm/templates/crm/lead_detail.html`
- `crm/templates/crm/opportunity_detail.html`
- `static/crm/lead_detail.css`
- `static/crm/opportunity_detail.css`
- `UI_DENSITY_PHASE2_REPORT.md`

## Behavior Added

Lead Detail:

- Converted the page body into a two-column enterprise layout on desktop.
- Moved the existing top action block into a sticky right-side quick actions panel.
- Preserved the same form actions, CSRF tokens, hidden fields, confirmation prompts, URLs, and button labels.
- Added read-only status, contact, value, and warning summary panels in the desktop side column.
- On mobile, kept quick actions visible and suppressed only the added duplicate read-only side summaries because the same data already exists in the main page.

Opportunity Detail:

- Kept the production template structure intact.
- Added a scoped UI bridge class and compact CSS rules for the existing hero, workspace, side panels, cards, actions, forms, and mobile layout.
- Preserved all forms, actions, links, IDs, data attributes, and existing page sections.

## Query Comparison

Measured with Django test client against the copied local DB.

| Page | Before Queries | After Queries | Result |
| --- | ---: | ---: | --- |
| Lead Detail `/leads/1/` | 47 | 47 | unchanged |
| Opportunity Detail `/opportunities/1/` | 52 | 50 | reduced by 2 |

Measured response timing:

| Page | Before Render | After Render |
| --- | ---: | ---: |
| Lead Detail | 567.36 ms | 597.18 ms |
| Opportunity Detail | 44.36 ms | 39.56 ms |

No query logic was changed.

## Scroll Height Comparison

Browser screenshots and DOM metrics captured with Playwright against local runserver.

| Page | Viewport | Before | After | Reduction |
| --- | ---: | ---: | ---: | ---: |
| Lead Detail | 1440 | 5039 px | 4667 px | 7.4% |
| Lead Detail | 1024 | 6864 px | 5838 px | 14.9% |
| Lead Detail | 768 | 7245 px | 6195 px | 14.5% |
| Lead Detail | 430 | 8120 px | 7616 px | 6.2% |
| Lead Detail | 390 | 8178 px | 7650 px | 6.5% |
| Opportunity Detail | 1440 | 4653 px | 4011 px | 13.8% |
| Opportunity Detail | 1024 | 6179 px | 5272 px | 14.7% |
| Opportunity Detail | 768 | 8641 px | 7323 px | 15.3% |
| Opportunity Detail | 430 | 10473 px | 8746 px | 16.5% |
| Opportunity Detail | 390 | 10619 px | 8948 px | 15.7% |

The two-column layout improves scan density but does not yet hit the requested 20-25% card-height target on every viewport. Further reductions would require broader component consolidation or collapsible behavior, which should be reviewed separately before continuing.

## Forms, Buttons, Links, CSRF

Browser DOM comparison:

| Page | Viewport | Forms | Buttons | Links | CSRF |
| --- | ---: | ---: | ---: | ---: | ---: |
| Lead Detail | all tested widths | 9 -> 9 | 23 -> 23 | 82 -> 82 | 8 -> 8 |
| Opportunity Detail | all tested widths | 11 -> 11 | 27 -> 27 | 91 -> 91 | 10 -> 10 |

Server-rendered HTML also preserved expected form and CSRF output:

- Lead Detail: 9 forms, 23 buttons, 9 CSRF token strings.
- Opportunity Detail: 11 forms, 27 buttons, 12 CSRF token strings.

## Browser Verification

Viewports checked:

- 1440 desktop
- 1024 tablet
- 768 tablet
- 430 mobile
- 390 mobile

Results:

- Duplicate IDs: none detected.
- Console errors: none detected.
- Scrollable horizontal overflow: none detected; document `scrollWidth` matched viewport width at every tested size.
- Forms remained present.
- CSRF tokens remained present.
- Buttons remained present.
- Links remained present.
- Existing confirmation prompts remained present.
- No financial text or formula output was changed by this frontend-only work.

Note: the global off-canvas navigation still produces off-screen bounding boxes at narrow widths, but it does not create document-level horizontal scrolling.

## Screenshot Paths

Screenshot root:

`/tmp/iconic_density_phase2/screenshots/`

Representative files:

- `before_lead_1440_viewport.png`
- `after_lead_1440_viewport.png`
- `before_lead_1440_full.png`
- `after_lead_1440_full.png`
- `before_lead_1024_viewport.png`
- `after_lead_1024_viewport.png`
- `before_lead_390_viewport.png`
- `after_lead_390_viewport.png`
- `before_opportunity_1440_viewport.png`
- `after_opportunity_1440_viewport.png`
- `before_opportunity_1440_full.png`
- `after_opportunity_1440_full.png`
- `before_opportunity_1024_viewport.png`
- `after_opportunity_1024_viewport.png`
- `before_opportunity_390_viewport.png`
- `after_opportunity_390_viewport.png`

Complete before/after screenshots exist for each page at 1440, 1024, 768, 430, and 390 widths, with both first-screen and full-page captures.

## Tests

Commands run:

```bash
DJANGO_SECRET_KEY=local-density-phase2 python3 manage.py check
DJANGO_SECRET_KEY=local-density-phase2 python3 manage.py makemigrations --check --dry-run
git diff --check
git diff --cached --check
DJANGO_SECRET_KEY=local-density-phase2 python3 manage.py test crm.tests.test_workflow_safety_updates crm.tests.test_active_pipeline_cleanup crm.tests.test_invoice_from_opportunity crm.tests.test_customer_workflow_improvements crm.tests.test_iconic_ai_brain
DJANGO_SECRET_KEY=local-density-phase2 python3 manage.py test crm.tests
```

Results:

- `manage.py check`: passed.
- `makemigrations --check --dry-run`: passed, no changes detected.
- `git diff --check`: passed.
- `git diff --cached --check`: passed.
- Focused lead/opportunity tests: 47 tests passed.
- Full CRM regression: 475 tests passed.

## Risk Assessment

Low backend risk:

- No Python files changed.
- No model, migration, URL, service, settings, workflow, permission, or formula files changed.
- Protected-file scan returned no matches.

Moderate frontend review risk:

- Lead Detail now repositions the existing action area into a sticky side column on desktop.
- On mobile, added duplicate read-only side summaries are hidden to avoid scroll regression, while the original main-page information remains available.
- Opportunity Detail compaction is CSS-driven and scoped to the existing page wrapper.

## Deployment Recommendation

Do not deploy yet.

Phase 2 is ready for review, but it has not been committed, pushed, collected, migrated, restarted, or deployed.
