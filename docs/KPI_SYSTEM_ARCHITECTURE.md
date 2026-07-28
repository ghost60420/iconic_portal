# Iconic CRM KPI System Architecture

## Status

- Stage: 2 - foundation models and migration
- Working branch: `feature/kpi-stage-2-models`
- Baseline tag: `kpi-baseline-20260728`
- Baseline commit: `a9c2881f195e6608205e096552f4ce030200e9bd`
- Production and AWS were not accessed.

This stage adds only the versioned KPI template foundation. It does not add
employee assignments, review records, scoring, bonus records, pages, routes,
notifications, or seeded role templates.

## Design Boundary

The KPI system uses new tables and does not extend or rename existing employee,
role, payroll, commission, finance, production, notification, or file tables.
Existing employee roles remain the authorization and organization source. KPI
role templates are reusable performance definitions and do not replace CRM
roles.

KPI bonuses will remain separate from `SalesCommission` and existing payroll
bonus fields. Later stages must reference the existing company currency rules
without automatic currency conversion.

## Stage 2 Models

### `KPIRoleTemplate`

Reusable, employee-independent role identity.

- Unique stable `code`
- Unique display `name`
- Description and active status
- Creator and timestamps

### `KPITemplateVersion`

Immutable published snapshot for one role template.

- Role template and positive version number
- Draft, published, or retired status
- Effective date range
- Creator and publication identity
- Publication timestamp
- Unique `(template, version)` constraint

A draft can be published only after its active KPI item weights total exactly
`100.00`. Published versions can only transition to retired. Published and
retired definition fields cannot be edited or deleted through the model API.
Future changes require a new version, preserving the definition used by
historical reviews.

### `KPIItemDefinition`

Version-specific KPI item definition.

- Name, description, and purpose
- Measurement method and editable target
- Decimal weight
- Daily, weekly, monthly, quarterly, or annual frequency
- Data source
- Green, Yellow, and Red score ranges
- Manager approval and evidence requirements
- Bonus eligibility and Critical Red rule
- Active status and stable sort order

The database constrains each weight to `0.00` through `100.00` and requires
ordered, non-overlapping ranges. Model validation requires complete coverage
from `0.00` through `100.00` in `0.01` increments. Items can be changed only
while their persisted template version is a draft.

### `KPISettings`

Versioned global defaults.

- One active settings version
- Green `85.00-100.00`
- Yellow `70.00-84.99`
- Red `0.00-69.99`
- Individual bonus weight `60.00`
- Team bonus weight `25.00`
- Company bonus weight `15.00`

Model validation requires complete score-range coverage and bonus component
weights totaling exactly `100.00`. Database constraints enforce positive
versions, a single active row, complete range continuity, individual component
bounds, and an exact `100.00` bonus-weight total.

## Database Migration

Migration `crm.0192_kpi_template_foundation` depends on the approved
`crm.0191_library_permission_controls` leaf. It creates:

1. `crm_kpiroletemplate`
2. `crm_kpitemplateversion`
3. `crm_kpiitemdefinition`
4. `crm_kpisettings`

The migration has no `RunPython`, seed data, existing-table alteration, data
conversion, rename, or removal operation. Foreign keys to users use `SET_NULL`;
version-to-template uses `PROTECT`; item-to-version uses `CASCADE`.

## Migration Verification

The migration was tested on:

- A fresh empty database through the complete project migration graph
- A populated repaired-development copy
- A separate populated rollback copy

Results:

- Fresh migration: PASS
- Populated forward migration: PASS
- Populated rollback to `crm.0191`: PASS
- Populated reapply: PASS
- SQLite integrity check: `ok`
- Foreign-key violations: `0`
- Protected-data signature before/after:
  `d61bf0925cc1a2901960306c8083739e88c1eeb1fd6eb911aa8c7f735332b046`
- Existing KPI rows created: `0`
- Django system check: PASS
- Migration graph and empty migration plan: PASS
- `makemigrations --check --dry-run`: PASS
- Full regression: `766 of 766` tests passed in `312.168s`

## Rollback

Before any rollback, take and verify a database backup. While Stage 2 contains
no KPI data, the tested rollback is:

```bash
python3 manage.py migrate crm 0191
```

This removes only the four Stage 2 KPI tables and its migration ledger entry.
Reapply with:

```bash
python3 manage.py migrate crm 0192
```

After role templates or settings are created in later stages, rolling back
`0192` would delete KPI data and must not be used as a routine production
rollback. At that point, prefer an additive corrective migration or application
rollback that leaves the schema intact.

## Deferred Stages

- Stage 3: seed and verify version 1 role templates
- Stage 4: employee KPI role assignments and exact active-role weight rules
- Stage 5: employee Performance tab
- Stage 6: entry, evidence, comments, approval, locking, and unlock workflow
- Stage 7: individual, role, employee, team, and company score calculations
- Stage 8: separate KPI bonus review records and approval
- Stage 9: server authorization and immutable audit events
- Stage 10: dashboard, reports, exports, and KPI notifications

No deferred model or workflow is represented by a placeholder table in Stage 2.
Custom authorization codenames and all permission checks are deferred to Stage
9 so this foundation migration does not change the current authorization model.
