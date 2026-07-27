# Living Catalog Visual Upgrade

## Pages Changed
- Library home: modernized five-section showroom cards and search results.
- Catalog lists: improved default Card View, retained Table View, added simple GSM and composition filters where relevant.
- Catalog detail pages: added shared gallery/lightbox and related-record display.
- Presentation Mode: kept standalone client-safe page, added Exit Presentation, fullscreen, shared gallery, and related records.

## Shared Components Used
- `crm/templates/crm/partials/living_catalog_card.html`
- `crm/templates/crm/partials/living_catalog_gallery.html`
- `crm/templates/crm/partials/living_catalog_gallery_script.html`
- `crm/templates/crm/partials/living_catalog_related.html`
- `crm/templates/crm/partials/living_catalog_status.html`

## Design Improvements
- Larger cover images on Library home and catalog cards.
- Dark and gold CRM styling without changing the global CRM navigation.
- Cleaner detail hero area with gallery, title, category or type, status, and scoped actions.
- Reusable status badge and card structure across Products, Fabrics, Accessories, Trims, and Threads.
- Detail pages suppress empty fields and empty related sections.

## Search Improvements
- Product search now includes linked fabric name, fabric type, composition, GSM, decoration, status, colour, category, and tags.
- Fabric search includes type, composition, GSM, status, colour, best use, and tags.
- Accessory, Trim, and Thread search include status in addition to existing name, type, colour, best use, and tags.

## Filter Improvements
- All catalog pages retain Type, Status, Colour, and Clear Filters.
- Product filters include Category, Fabric, GSM, and Decoration.
- Fabric filters include Composition and GSM in addition to Type.

## Presentation Mode Improvements
- Presentation Mode remains a standalone page with no CRM navigation.
- Edit controls, audit information, staff names, internal pricing, internal notes, and supplier information are not rendered.
- Added Exit Presentation, fullscreen, previous and next records, large images, thumbnails, lightbox, and mobile swipe support.

## Performance Review
- No database schema change.
- Product list queries continue to use `select_related("main_fabric")`.
- Product detail related materials use existing prefetched relationships from the approved catalog queryset.
- Fabric, Accessory, Trim, and Thread detail reverse references are limited to 24 active products and use `select_related("main_fabric")`.
- No heavy JavaScript libraries were added.
- Full-size image storage was not changed; the gallery uses existing image URLs.
- Temporary test-database probe, current changed code:
  - Library home: 10 warm queries, 5.3 ms warm response.
  - Product list: 6 warm queries, 5.3 ms warm response.
  - Product search: 6 warm queries, 8.7 ms warm response.
  - Product detail: 8 warm queries, 5.0 ms warm response after final measurement.
  - Product Presentation Mode: 8 warm queries, 3.6 ms warm response after annotating previous and next record IDs onto the main presentation record query.
- N plus 1 review: product list uses joined fabric data; detail related products are bounded and do not add unbounded per-record lookups in the measured sample.

## Security Review
- Library permissions were not changed.
- Internal pricing and supplier fields remain controlled by existing backend context checks.
- Presentation Mode uses the client-safe public detail context and does not render internal details.
- Search results and cards do not include internal pricing, internal notes, private supplier fields, or audit rows.
- Image upload validation and storage were not changed.

## Tests Added
- Showroom card useful details and private pricing exclusion.
- Shared image gallery/lightbox controls on detail and Presentation Mode.
- Product search and filters by linked fabric specs, GSM, and decoration.
- Fabric composition and GSM filters.
- Related materials and reverse related products from existing relationships.
- Unauthorized detail controls remain hidden for view-only users.

## Testing Results
- `DJANGO_SECRET_KEY=local-test-secret python3 manage.py makemigrations --check --dry-run`: passed, no changes detected.
- `DJANGO_SECRET_KEY=local-test-secret python3 manage.py check`: passed, no issues.
- `DJANGO_SECRET_KEY=local-test-secret python3 -m py_compile crm/views.py crm/tests/test_living_catalog.py`: passed.
- `DJANGO_SECRET_KEY=local-test-secret python3 manage.py test crm.tests.test_living_catalog --verbosity=1`: passed, 31 tests.
- `DJANGO_SECRET_KEY=local-test-secret python3 manage.py test crm.tests.test_living_catalog crm.tests.test_accounting_rbac crm.tests.test_internal_costing_permissions --verbosity=1`: passed, 41 tests.

## Rollback Method
- Revert the visual upgrade commit.
- No migration rollback is required because this upgrade does not change database structure.
- If static collection is run during deployment, restore the previous deployed static artifact as part of the normal rollback process.
