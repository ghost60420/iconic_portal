# Iconic CRM KPI System Architecture

## Status

- Current stage: 4 - KPI calculation engine
- Working branch: `feature/kpi-stage-4-calculation-engine`
- Stage 4 base commit: `1e0b7713ea50e975d416a4fa8d65ddde1a6c7f83`
- Baseline tag: `kpi-baseline-20260728`
- Baseline commit: `a9c2881f195e6608205e096552f4ce030200e9bd`
- Production and AWS were not accessed.

Stage 2 added the versioned KPI template foundation. Stage 3 added employee
assignment and immutable assignment-history tables plus a transactional service
layer. Stage 4 adds a read-only, versioned calculation engine. It does not add
review records, bonus calculations, pages, routes, notifications, or seeded
employee assignments.

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

## Stage 3 Assignment Foundation

`EmployeeKPIRoleAssignment` connects an existing `EmployeeProfile` to an
independent `KPIRoleTemplate`. It stores a decimal role weight, optional
manager, effective dates, active/archive state, bonus eligibility, notes,
creator/updater identity, timestamps, and an incrementing assignment version.
It does not reference login groups or `UserAccess`.

`EmployeeKPIRoleAssignmentHistory` stores an append-only old/new snapshot for
every create and update, including manager, weight, dates, active/archive
state, bonus eligibility, actor, reason, and assignment version. Assignment and
history deletion are blocked by the model layer.

The assignment service:

- Creates one `100.00` role or an atomic set of roles totaling `100.00`
- Atomically reweights several roles
- Deactivates and archives without deleting history
- Lists effective assignments for today or a supplied date
- Calculates and validates effective role weight
- Resolves the applicable manager and template set
- Filters bonus-eligible assignments without calculating a bonus
- Detects incomplete, overweight, and duplicate-template overlaps

An employee may have no effective assignments. If one or more active,
non-archived assignments cover a date, their combined weight must be exactly
`100.00`. Future and expired rows are evaluated only inside their date ranges.

Migration `crm.0193_kpi_employee_role_assignments` depends on `crm.0192` and
creates only:

1. `crm_employeekpiroleassignment`
2. `crm_employeekpiroleassignmenthistory`

The migration has no data operation, seed, rename, deletion, or protected-table
alteration. It was tested on fresh and populated databases and through rollback
to `crm.0192` and reapplication. The protected-data signature remained
`19ad00265555f261cf0711a0aac817114ac6e8e26b9fa5df96f792d32e643d13`.
The full regression result is `797 of 797` tests passed.

## Stage 4 Calculation Engine

`crm.services.kpi_calculation_engine` is the single source of truth for KPI
item, template, employee-role, and multi-role employee scores.

The engine:

- Uses `Decimal` arithmetic and returns immutable structured results
- Supports percentage, count, currency, boolean, manual, duration, and decimal
  measurements
- Applies explicit higher-is-better, lower-is-better, or exact-target formulas
- Requires template and employee weights to total exactly `100`
- Reads aggregate status ranges from the active `KPISettings` version
- Uses versioned item ranges for item-level statuses
- Preserves calculated scores when Critical Red forces the final status to Red
- Carries formula, engine, template, assignment, review-date, and range versions
- Resolves historical assignments and immutable template versions by date
- Rejects ambiguous historical template-version schedules
- Performs no database writes

The complete model-backed employee calculation is bounded to four queries for
both one-role and three-role cases. Pure calculations use zero queries.
Reusing one engine caches immutable template definitions and reduces a repeated
calculation to two queries. The full Stage 4 regression result is `823 of 823`
tests passed in `367.520s`.

Stage 4 creates no migration or result table. The later review workflow must
persist the engine's immutable `as_dict()` snapshot so approved history is
never recalculated against a newer formula, assignment, template, or settings
version.

## Deferred Stages

- Stage 5: employee Performance UI using the Stage 4 engine
- Later stage: seed and verify approved version 1 role templates
- Later stage: entry, evidence, comments, approval, locking, and unlock workflow
- Later stage: separate KPI bonus review records and approval
- Later stage: server authorization and audit-event integration
- Later stage: dashboard, reports, exports, and KPI notifications

No deferred model or workflow is represented by a placeholder table. Custom
authorization codenames and all KPI permission checks remain deferred, so
Stages 2 and 3 do not change the current authorization model.
