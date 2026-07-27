# Library Permission Matrix

## Permission Names

- `can_library`: View Library. Reused for Products, Fabrics, Accessories, Trims, and Threads view access.
- `can_add_library`: Add Library Items.
- `can_edit_library`: Edit Library Items. Existing field preserved for backward compatibility.
- `can_upload_library_images`: Upload or replace Library images.
- `can_remove_library_images`: Remove Library images.
- `can_archive_library`: Archive and restore Library records.
- `can_use_library_presentation`: Open client-safe Presentation Mode.
- `can_view_library_pricing`: View Library internal cost, suggested selling price, and internal notes.

## What Each Permission Allows

- View Library: opens approved catalog records and catalog lists.
- Add Library Items: opens and submits add forms.
- Edit Library Items: opens and submits edit forms.
- Upload Images: accepts image files before form processing.
- Remove Images: accepts image removal requests before form processing.
- Archive and Restore: archives and restores records.
- Use Presentation Mode: opens client-safe Presentation Mode.
- View Internal Pricing: renders internal pricing fields in server templates only for approved users.

No permanent delete permission is added.

## Default Role Access

- CEO: all Library permissions.
- Super Admin: all Library permissions.
- Director: view, add, edit, upload images, remove images, archive/restore, Presentation Mode, internal pricing.
- Production Manager: view, add, edit, upload images, remove images, Presentation Mode.
- Factory Manager: view, add, edit, upload images, remove images, Presentation Mode.
- Approved Manager: view, add, edit, upload images, remove images, Presentation Mode.
- Sales: view and Presentation Mode only.
- Accounts: view and internal pricing only.
- Marketing: approved-record view and Presentation Mode only.

## Backend Protection Locations

- `crm/urls.py`: Library route entry requires at least one Library permission.
- `crm/views.py`: add, edit, image upload, image removal, archive, restore, detail, list, safe JSON, and Presentation Mode each check the matching permission.
- `crm/forms_access.py`: Library checkbox dependencies keep View Library enabled when dependent Library permissions are granted.
- `crm/views_access.py`: permission changes are saved through existing `UserAccess` and audited.
- `crm/templates/crm/living_catalog_*`: buttons and controls are hidden when the matching backend permission is absent.

## Migration Details

- Migration: `crm/migrations/0191_library_permission_controls.py`
- Type: additive.
- Adds granular Library permission booleans to `UserAccess`.
- Includes a data migration to seed safe defaults from existing role/profile data and current Library permissions.

## Backward Compatibility Rules

- Existing `can_library=True` users keep View Library.
- Existing `can_library=True` users keep Presentation Mode access.
- Existing `can_edit_library=True` users keep View, Add, Edit, and Upload Images.
- Remove Images, Archive and Restore, and View Internal Pricing are not granted from `can_edit_library` alone.
- Existing internal costing access is mapped to Library pricing access to preserve current approved internal visibility.

## Testing Results

Run before deployment:

- `python3 manage.py makemigrations --check --dry-run`
- `python3 manage.py check`
- `python3 -m py_compile` on changed Python files
- `python3 manage.py test crm.tests.test_living_catalog --verbosity=1`
- Existing permission, role, employee access, and Library-related tests

## Rollback Method

Before production migration, create a database backup. Rollback is:

1. Restore the pre-migration database backup.
2. Return code to the previous commit.
3. Restart `gunicorn.service`.
4. Verify Library home, Products, Fabrics, Accessories, Trims, Threads, image display, Presentation Mode, and internal pricing permissions.
