# Living Catalog Deployment Readiness

## Architecture Summary

Living Catalog is implemented as an extension of the existing CRM Library module.
It does not create a second Library app, duplicate item models, duplicate Product/Fabric/Accessory/Trim/Thread records, or duplicate navigation.

Existing Library URLs remain the entry points:

- `/library/`
- `/library/products/`
- `/library/fabrics/`
- `/library/accessories/`
- `/library/trims/`
- `/library/threads/`

The existing `Product`, `Fabric`, `Accessory`, `Trim`, and `ThreadOption` models are extended in place. Shared Living Catalog helpers in `crm/services/living_catalog.py` and shared templates are used by the current Library routes.

## Migration Summary

Migration file: `crm/migrations/0183_living_catalog.py`

Dependency chain:

- Depends on tracked migration `crm.0182_invoice_opportunity_link`
- Does not depend on unrelated `0188_shipment_estimated_delivery_date`
- Does not include unrelated shipment, marketing, dashboard, sales, finance, production, employee, AI, or Control Center migrations

Migration operations are additive:

- Adds nullable or defaulted fields
- Adds Product relationships to existing Fabric, Accessory, Trim, and Thread records
- Adds image slots `image_2` and `image_3`
- Adds internal pricing fields
- Adds archive metadata
- Adds `LivingCatalogAudit`
- Maps existing inactive Library rows to `status="Archived"`

No operation deletes fields, renames fields, deletes records, or removes existing image paths.

Reverse behavior:

- Django can reverse the schema migration.
- Added fields, added relationship tables, and the audit table are removed on reverse.
- The inactive-to-Archived data mapping uses a no-op reverse because the added `status` column is removed during reverse.
- Existing legacy Library rows remain after reverse.

## Files Changed

Approved Living Catalog files:

- `LIVING_CATALOG_IMPLEMENTATION_PLAN.md`
- `LIVING_CATALOG_DEPLOYMENT_READINESS.md`
- `crm/models.py`
- `crm/forms.py`
- `crm/views.py`
- `crm/urls.py`
- `crm/services/living_catalog.py`
- `crm/migrations/0183_living_catalog.py`
- `crm/templates/crm/library_home.html`
- `crm/templates/crm/living_catalog_list.html`
- `crm/templates/crm/living_catalog_form.html`
- `crm/templates/crm/living_catalog_detail.html`
- `crm/templates/crm/living_catalog_presentation.html`
- `crm/tests/test_living_catalog.py`

No unrelated Marketing, Dashboard, AI, Control Center, Financial, Production, Sales, Employee, or Shipment files are part of this deployment branch.

## Database Impact

The migration preserves current Library records and existing image paths.

Database rehearsal used a temporary SQLite database under `/private/tmp` with synthetic Library rows only. It seeded two legacy rows per Library table, including legacy image path values and inactive rows, then ran forward, reverse, and reapply checks.

Rehearsal result:

- Product count matched before and after
- Fabric count matched before and after
- Accessory count matched before and after
- Trim count matched before and after
- Thread count matched before and after
- Existing image path counts matched before and after
- Old columns were preserved
- New fields were present
- Inactive synthetic rows mapped to Archived
- Active synthetic rows were not archived
- Product relationship tables were created
- Living Catalog audit table was created
- Reverse and reapply preserved record counts

No production data was read, displayed, modified, migrated, or deployed during this review.

## Permission Impact

The existing CRM permission system is reused through the Library route wrapper and backend checks.

Expected access:

- CEO and Director: full access
- Production Manager and Factory Manager style roles: add/edit where permitted by existing access/profile checks
- Sales: view and Presentation Mode only
- Accounts: view internal pricing when internal costing access is granted
- Marketing: limited view of approved records

Archive and restore require backend permission checks. Sales users cannot edit or archive records.

## Image Storage Impact

The existing secure CRM file storage pattern is preserved through Django `ImageField` storage.

Each Library item supports:

- `image`
- `image_2`
- `image_3`

The first available image is used as the cover image. Existing `image` values are not removed or renamed. Large image binary data is not stored in database fields; database columns store file paths only.

Upload validation:

- JPG, PNG, and WEBP only
- Maximum upload size enforced
- Unsafe path names are normalized
- Images are resized before storage

## Presentation Mode Security

Presentation Mode uses a dedicated shared view path and template.

It does not include:

- Edit buttons
- Internal pricing
- Internal notes
- Supplier/private legacy pricing fields
- Audit history
- Staff names

Presentation Mode context is built with `presentation=True`, which forces `can_view_internal=False` and `can_edit=False`.

## Internal Pricing Security

Internal fields:

- `internal_cost`
- `suggested_selling_price`
- `internal_notes`

Internal legacy price/supplier details are included only in the internal detail builder and only when backend permission checks allow it.

Security is enforced through:

- Backend permission checks
- Form field removal for unauthorized users
- Template permission checks
- Client-safe JSON response that excludes internal fields entirely
- Presentation Mode context filtering

Tests confirm Sales users cannot see internal values on detail pages, Presentation Mode, or the client-safe JSON endpoint.

## Related Material Relationships

Products link to existing source records:

- `Product.main_fabric -> Fabric`
- `Product.accessories -> Accessory`
- `Product.trims -> Trim`
- `Product.threads -> ThreadOption`

The Product detail view reads related material data from the linked Library records, so related details update when the source record changes. Composition, GSM, colour, material, and image data are not copied into duplicate Product fields.

## Living Catalog Verification

Passing scoped verification:

- Library home opens
- Product list/detail/edit routes open
- Fabric list/detail/edit routes open
- Accessory list/detail/edit routes open
- Trim list/detail/edit routes open
- Thread list/detail/edit routes open
- Existing image values render
- Card View renders
- Table View renders
- Three image upload works
- Image replace and remove audit paths work
- Main fabric selection works
- Related materials work
- Archive and restore work
- Internal pricing permission tests pass
- Presentation Mode hides private information
- Client-safe JSON excludes private fields
- Desktop, tablet, and mobile CSS breakpoints render in templates

Verification commands:

- `python3 manage.py makemigrations --check --dry-run`: passed
- `python3 manage.py check`: passed
- `python3 -m py_compile` on changed Python files: passed
- `python3 manage.py test crm.tests.test_living_catalog crm.tests.test_dashboard_and_misc --verbosity=1`: passed, 18 tests
- `git diff --check`: passed

Synthetic performance check:

- Library home cold: 10 queries, 62.96 ms
- Library home warm: 9 queries, 5.43 ms
- Product list cold: 5 queries, 6.08 ms
- Product list warm: 5 queries, 4.03 ms
- Product search: 5 queries, 4.85 ms
- Product detail cold: 7 queries, 7.11 ms
- Product detail warm: 7 queries, 4.51 ms
- Product Presentation Mode: 8 queries, 6.00 ms

N+1 result: no Product list N+1 was observed in the scoped synthetic checks; Product related materials are loaded through bounded `select_related` and `prefetch_related` paths.

## Known Unrelated Test Failures

The full suite currently has one failing test:

- `marketing.tests_intelligence.MarketingIntelligenceTests.test_marketing_calendar_shows_due_content`

Classification: pre-existing unrelated failure.

Evidence:

- The failure is in the Marketing app.
- The assertion checks that a Marketing Intelligence due date string appears on the Marketing Intelligence page.
- It does not import or call Living Catalog models, forms, views, services, templates, migrations, or URLs.
- The same exact test fails on a detached pristine `origin/main` worktree at commit `2eb88d0`, before Living Catalog changes are present.
- No Marketing files are changed or staged for this deployment.

This failure does not affect the Library routes, Library models, Living Catalog migration, image upload handling, internal pricing security, or Presentation Mode.

## Deployment Recommendation

Living Catalog is safe to prepare as a scoped deployment branch from the Library perspective.

Do not deploy until:

- The scoped Living Catalog files are the only staged files
- A current production database backup is confirmed by the existing backup process
- The production migration is run through the normal deployment process
- Post-deploy Library smoke checks pass
- Gunicorn is restarted safely through the normal service procedure

The unrelated Marketing test failure should be tracked separately and should not be fixed in the Living Catalog deployment unless it is later proven to block Library deployment.

## Rollback Procedure

Before production deploy:

- Abort by unstaging/removing the scoped branch changes.
- No database rollback is needed because production has not been migrated.

After production deploy:

- Stop traffic or put the app in maintenance mode if required by the existing process.
- Restore the pre-deployment database backup if data rollback is required.
- Revert the Living Catalog deployment commit.
- Re-run migrations back to the previous CRM migration head if schema rollback is chosen.
- Re-run `collectstatic` only if static output changed in the deployment process.
- Restart Gunicorn safely.
- Verify existing Library home and all five Library sections.
