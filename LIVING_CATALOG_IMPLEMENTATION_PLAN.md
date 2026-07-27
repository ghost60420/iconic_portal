# Living Catalog Implementation Plan

## Deployment status
- Production deployment is stopped for this work.
- No production data, user accounts, storage services, global navigation, or unrelated CRM modules will be changed.
- This plan was completed before implementation and must be rechecked before any migration, push, or deploy.

## Current Library inspection findings

### Current files involved
- `crm/models.py`
  - `Product`
  - `Fabric`
  - `Accessory`
  - `Trim`
  - `ThreadOption`
  - `LibraryAttachment`
  - `ProductionOrder` has existing M2M links to library materials, but `Product` does not.
- `crm/forms.py`
  - `ProductForm`
  - `FabricForm`
  - `AccessoryForm`
  - `TrimForm`
  - `ThreadForm`
  - `LibraryAttachmentForm`
- `crm/views.py`
  - Library home and CRUD views.
  - Product, fabric, accessory, trim AI suggestion endpoints.
  - Existing thread add/edit views currently call `ThreadOptionForm`, but the form class is named `ThreadForm`.
- `crm/urls.py`
  - Existing Library URLs are under `/library/...`.
  - Current routes use `login_required` only, not the CRM `can_library` permission wrapper.
- `crm/templates/crm/`
  - `library_home.html`
  - `products_list.html`
  - `product_form.html`
  - `product_detail.html`
  - `fabric_list.html`
  - `fabric_form.html`
  - `fabric_detail.html`
  - `accessory_list.html`
  - `accessory_form.html`
  - `accessory_detail.html`
  - `trim_list.html`
  - `trim_form.html`
  - `trim_detail.html`
  - `thread_list.html`
  - `thread_form.html`
  - `thread_detail.html`
- Existing migrations:
  - `crm/migrations/0010_product_alter_opportunityfile_options.py`
  - `crm/migrations/0011_accessory_fabric_threadoption_trim.py`
  - `crm/migrations/0012_remove_fabric_dye_type_remove_fabric_finish_and_more.py`
  - `crm/migrations/0013_product_image.py`
  - `crm/migrations/0112_library_attachment.py`
  - `crm/migrations/0086_useraccess_can_library_and_more.py`
  - `crm/migrations/0087_useraccess_can_library.py`
  - `crm/migrations/0143_useraccess_can_view_internal_costing.py`

### Current data and image storage
- Local database inspection showed zero current local Product, Fabric, Accessory, Trim, Thread, and LibraryAttachment records.
- Existing image fields store paths in Django `ImageField` columns and actual files under `MEDIA_ROOT`.
- `MEDIA_ROOT` is configured as `BASE_DIR / "media"` and served through `MEDIA_URL = "/media/"`.
- Existing catalog media folders include product, fabric, accessory, trim/thread-related folders where present.
- There is no evidence of large image files stored directly in database fields.

### Current model support
- Current models support one image per Product, Fabric, Accessory, Trim, and ThreadOption through each model's `image` field.
- Current models do not support three images per record.
- Current Product does not support a `ForeignKey` main fabric or M2M related accessories, trims, and threads.
- `ProductionOrder` already supports M2M material links, but that does not satisfy Product-level Living Catalog relationships.
- Current archive behavior is only `is_active`; there are no explicit archive/restore endpoints or audit events.

## Files that need changes
- `crm/models.py`
  - Add Living Catalog status/detail/internal fields.
  - Add Product-to-material relationships.
  - Add image slot fields or a related image model.
  - Add audit tracking model.
- `crm/forms.py`
  - Simplify forms and expose only required/optional Living Catalog fields.
  - Keep legacy fields in collapsed More Details where needed.
  - Fix thread form usage.
- `crm/views.py`
  - Add shared catalog helpers.
  - Harden permissions.
  - Add search/filter support.
  - Add card/table context.
  - Add image handling and audit logging.
  - Add client-safe Presentation Mode.
  - Keep AI suggestions review-only.
- `crm/urls.py`
  - Use existing CRM permission wrapper for Library.
  - Add Presentation Mode and safe JSON endpoints as needed.
  - Add archive/restore endpoints if implemented in this phase.
- `crm/templates/crm/`
  - Modernize home, list, form, detail, and presentation templates.
  - Use small partials for repeated catalog image UI and non-empty detail rows.
- `crm/tests/`
  - Add focused tests for forms, views, permissions, image handling, Presentation Mode, and API leakage.
- `crm/migrations/`
  - Add a backward-compatible migration only. Do not delete or rename current columns.

## Existing fields that can be reused
- Product:
  - `name`
  - `product_type`
  - `product_category`
  - `default_fabric` as legacy fallback text only
  - `default_gsm` as legacy fallback/display support
  - `default_moq`
  - `default_price` as legacy/internal fallback if mapped safely
  - `image` as current first/cover image
  - `notes`
  - `is_active`
- Fabric:
  - `name`
  - `fabric_type`
  - `composition`
  - `gsm`
  - `stretch_type`
  - `color_options`
  - `image`
  - `notes`
  - `is_active`
  - legacy technical fields remain preserved.
- Accessory:
  - `name`
  - `accessory_type`
  - `color`
  - `material`
  - `supplier` as private/supplier detail only
  - `price_per_unit` as private/internal detail only
  - `image`
  - `notes`
  - `is_active`
- Trim:
  - `name`
  - `trim_type`
  - `color`
  - `material`
  - `price_per_meter` as private/internal detail only
  - `image`
  - `notes`
  - `is_active`
- Thread:
  - `name`
  - `thread_type`
  - `thread_code`
  - `color`
  - `use_for`
  - `price_per_cone` as private/internal detail only
  - `image`
  - `notes`
  - `is_active`

## New fields required
- Shared catalog fields:
  - `status` with model-specific choices.
  - `short_description`
  - `tags`
  - `best_use` for fabrics/accessories/trims/threads where needed.
  - `internal_cost`
  - `suggested_selling_price`
  - `internal_notes`
  - archive metadata if required: `archived_at`, `archived_by`.
- Product fields:
  - `main_fabric` as `ForeignKey(Fabric, null=True, blank=True, on_delete=SET_NULL)`.
  - `main_decoration`
  - `fit`
  - `main_colour`
  - `available_colours`
  - `size_range`
  - `accessories` M2M to `Accessory`.
  - `trims` M2M to `Trim`.
  - `threads` M2M to `ThreadOption`.
- Image storage:
  - Keep existing `image` as slot 1/cover.
  - Add `image_2` and `image_3` to each catalog model, or use a generic related catalog image model.
  - For this codebase, direct `ImageField` slots are lower risk because the existing UI/model pattern already uses direct image fields.

## Database migration requirements
- Backward-compatible additive migration only.
- Do not delete or rename existing columns.
- Do not delete records.
- Do not alter current file paths.
- Default `status` from existing `is_active`:
  - Active records map to each model's available status.
  - Inactive records map to `Archived`.
- Keep existing `image` paths as slot 1.
- Add nullable/blank fields only.
- Create M2M tables for Product related materials.
- Add audit model/table for Living Catalog actions.
- Production migration must be preceded by a database backup and a migration rehearsal on a production copy.

## Current data preservation
- Existing `image` fields remain untouched and become the cover image.
- Existing legacy fields remain in place and continue to render where data exists.
- Existing prices and supplier fields stay in the database, but templates/API/Presentation Mode must gate them as internal/private.
- Existing inactive records remain inactive and display as Archived where status is blank.
- No current columns or records are removed.

## Three image storage
- Slot 1: existing `image` field.
- Slot 2: new `image_2` field.
- Slot 3: new `image_3` field.
- Use Django `ImageField` with `MEDIA_ROOT` storage, not database BLOBs.
- Uploads accept phone/computer multipart uploads.
- UI supports drag/drop, preview before save, replace, remove, and `1 of 3` count.
- Server validates extension/content type and uses Pillow to resize/normalize images before saving.
- Removing a slot clears that image field after permission checks. It does not delete existing media files in this phase, preserving rollback safety.
- The first non-empty slot is used as the cover image, with slot 1 preferred.

## Internal pricing protection
- Internal fields:
  - `internal_cost`
  - `suggested_selling_price`
  - `internal_notes`
  - legacy price/supplier fields where applicable.
- Access is granted only to:
  - superusers
  - users with `can_view_internal_costing`
  - users in approved executive/accounting roles when represented in the existing permission system.
- Sales users must not receive private data in templates, JSON, Presentation Mode, or page source.
- Backend views must remove private fields before building response context.
- Do not rely on CSS or JavaScript hiding.

## Presentation Mode
- Add client-safe Presentation Mode views for each catalog type.
- The view context must be built from a safe serializer/helper that excludes:
  - internal pricing
  - internal notes
  - supplier information
  - audit history
  - staff names
  - edit controls
- Show:
  - large image gallery
  - public catalog details
  - related materials
  - next/previous record links within the same catalog type.
- Support full screen through browser UI controls only; no private data should be loaded into page source.

## Permission changes
- Reuse existing CRM permission system.
- Replace bare `login_required` Library routes with `require_access("can_library")` where possible.
- Add helper checks:
  - view Library: `can_library` or superuser.
  - add/edit: `can_library` plus non-sales/non-marketing restriction where the existing role flags support it.
  - internal pricing: `can_view_internal_costing`.
  - archive/restore: executive/manager access only.
- Do not add permanent delete access.
- If role names cannot be reliably inferred from the existing schema, keep enforcement tied to existing permission flags and document the limitation.

## Search and filters
- Add one Library home search field across:
  - name
  - type
  - category
  - colour/color
  - tags
- Catalog list pages:
  - search
  - type
  - status
  - colour/color
  - clear filters
- Product list additionally:
  - category
  - fabric
  - decoration
- Card View default, Table View optional.

## AI assistance
- Keep AI review-only.
- AI may suggest descriptions, GSM, fabric, decoration, best use, and tags.
- AI must not save records automatically.
- Existing product/fabric detail AI endpoints currently append notes; they must be changed or gated so Living Catalog AI suggestions require explicit user apply.

## Testing plan
- Add focused Django tests for:
  - existing records still load.
  - product/fabric/accessory/trim/thread create views.
  - one/two/three image uploads.
  - image replacement and removal.
  - automatic image resizing.
  - Product main fabric dropdown and detail display.
  - related materials.
  - internal pricing hidden from sales templates.
  - internal pricing omitted from safe JSON/API responses.
  - Presentation Mode omits private data.
  - search and filters.
  - card/table view rendering.
  - archive/restore permissions if implemented.
- Run:
  - targeted Living Catalog tests.
  - `python3 manage.py check`.
  - `python3 -m py_compile` on changed Python files.
  - migration dry-run/check.

## Performance verification plan
- Measure query count before and after for:
  - Library home.
  - Product list.
  - Fabric list.
  - Product detail.
  - Presentation Mode.
- Use `assertNumQueries` tests where practical.
- Watch for N+1 on related materials and images.
- Use `select_related("main_fabric")` and `prefetch_related("accessories", "trims", "threads")` for Product lists/details.

## Rollback plan
- Before production migration: create a database backup.
- Code rollback:
  - revert the Living Catalog commit.
  - redeploy previous commit.
  - restart only the app service after confirming service name.
- Database rollback:
  - additive fields can remain unused if code is rolled back.
  - for full rollback, restore the pre-migration database backup.
- Media rollback:
  - new uploaded media can remain harmless if code is rolled back.
  - do not delete existing media during rollback unless restoring the full backup.

## Safety confirmation after scoped review
- Architecture: extension of the existing Library models, URLs, and module entry points.
- Current local records: safe to inspect; no local Library records found during initial inspection.
- Current image storage: file-based Django media storage.
- Multiple images: implemented through additive `image_2` and `image_3` fields while preserving existing `image` as slot 1.
- Related Product materials: implemented through additive Product relationships to existing Fabric, Accessory, Trim, and Thread records.
- Internal pricing: protected in form construction, view context, templates, safe JSON/API payloads, and Presentation Mode.
- Presentation Mode: implemented as client-safe views under the existing Library item routes.
- Permissions: Library routes use the existing CRM permission system; edit/archive/internal checks are enforced in backend helpers.
