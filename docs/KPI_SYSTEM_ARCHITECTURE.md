# Iconic CRM KPI System Architecture

## Status

- Current stage: 10 - Final Integration and Deployment Preparation
- Working branch: `feature/kpi-stage-10-final-integration`
- Stage 10 base commit: `911cb1ee32735ed360cc56e7c6b03de98956812a`
- Baseline tag: `kpi-baseline-20260728`
- Baseline commit: `a9c2881f195e6608205e096552f4ce030200e9bd`
- Production and AWS were not accessed.

Stage 2 added the versioned KPI template foundation. Stage 3 added employee
assignment and immutable assignment-history tables plus a transactional service
layer. Stage 4 added the read-only, versioned calculation engine. Stage 5 added
permission-controlled performance views, review records, workflow history, and
immutable approval snapshots. Stage 6 added database-configured bonus
evaluation and immutable calculation snapshots. Stage 7 added one adaptive,
read-only dashboard with server-scoped, independently loaded widgets. Stage 8
adds snapshot-only executive intelligence, role-scoped analytics, signed
actions, configurable intelligence rules, and secure PDF, Excel, CSV, and
print reports. Stage 9 adds CRM-only, deduplicated notification automation.
Stage 10 adds draft policy preparation, cross-policy approval governance,
explicit assignment administration, release testing, and deployment
documentation. It does not activate policies or automation.

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

## Stage 5 Performance Reviews

`KPIReview` owns a monthly, quarterly, or annual employee review and its
workflow state. `KPIReviewItemEntry` stores manager-entered actual values and
Critical Red evidence. `KPIReviewTransition` is an append-only approval history
created only by the review service.

The review service freezes the effective Stage 3 assignments, effective Stage
2 template versions, item definitions, weights, status ranges, and manager at
creation. Draft calculation and approval call the Stage 4
`KPICalculationEngine`; views contain no score formula. Approval stores the
engine result, frozen definition, entered values, comments, Critical Red data,
and approver identity in one digest-protected JSON snapshot. Approved and
locked records are read only, and historical pages render the stored snapshot
without recalculation.

Permission checks are performed in both views and workflow services:

- Employee: own approved or locked history only
- Manager: assigned employees and Draft submission, never approval
- Director: own department, approval and locking, never self-approval
- HR: read-only access to all reviews
- CEO and Super Admin: all non-self workflow actions

Migration `crm.0194_kpi_performance_reviews` creates only the three Stage 5
tables, their indexes, and period/uniqueness constraints. It has no data
operation and changes no existing table.

## Stage 6 Bonus Engine

`KPIBonusWeightProfile` versions individual, team, and company weights plus a
free-form team scope. `KPIBonusRuleSet` versions score, attendance, Critical Red,
employee-status, payout floor/cap, currency, effective-date, and approval
requirements. Published versions are immutable.

`crm.services.kpi_bonus` reads scores only from digest-verified Stage 5 approved
snapshots. It never calls the Stage 4 engine or reads live KPI item values.
Stage 3 immutable assignment-history versions supply the `bonus_eligible` flag
that was not embedded in the Stage 5 definition snapshot. Eligible role scores
are taken from the approved Stage 4 role result and normalized by their frozen
role weights.

Final calculations are stored separately in `KPIBonusCalculation`. Each review
may have one immutable historical result containing the source snapshot IDs and
digests, rule and weight versions, formula and engine versions, review date,
employee, manager, department, configured team scope, component scores,
eligibility reasons, and estimated capped/floored amount. Repeated creation
returns the verified historical record instead of recalculating it.

Migration `crm.0195_kpi_bonus_engine` creates only the three Stage 6 tables,
their indexes, and configuration constraints. It has no seed, data operation,
protected-table alteration, payment field, or payroll/commission relationship.

## Stage 7 KPI Dashboards

`crm.services.kpi_dashboard` is the shared read service for employee, manager,
director, HR, CEO, and Super Admin dashboards. It reuses Stage 5 visibility
querysets and digest verification. Score, status, role, trend, ranking, risk,
completion, and health values are aggregated only from approved or locked
review snapshots. Stage 6 immutable results provide authorized bonus
eligibility and executive forecast values. The Stage 4 engine is not called.

One adaptive dashboard shell lazy-loads reusable widgets from authenticated
fragment routes. Widget access is enforced by a server-side audience registry,
and cache entries are isolated by user, audience, widget, and normalized
filters. The filter service covers employee, role, department, location,
period, month, quarter, year, manager, and stored status without duplicating
query logic.

Stage 7 adds no models, migrations, tables, permissions, public endpoints,
database writes, review transitions, or bonus calculations. PDF, Excel, CSV,
and print are disabled capability interfaces for Stage 8.

## Stages 8 And 9

Stage 8 reads approved review and bonus snapshots through the existing
dashboard and intelligence services. It adds configurable intelligence rules,
server-scoped actions, data-quality warnings, and permission-controlled PDF,
Excel, CSV, and print exports. It never recalculates approved history.

Stage 9 reuses the existing CRM notification inbox. Notification events store
source, rule, recipient, severity, action, and deduplication metadata, while
bounded automation runs record retries and sanitized failures. No email, SMS,
WhatsApp, provider, payroll, or payment integration is included.

## Stage 10 Release Governance

`KPIPolicyApproval` is a service-controlled envelope around one template,
settings, bonus-weight, bonus-rule, intelligence, or notification version. Its
states are Draft, Under Review, Approved, Published, and Retired. Database
constraints require exactly one correctly typed target and paired actor/time
fields. Normal update and deletion paths are blocked.

`crm.services.kpi_release` is the only Stage 10 transition service. It:

- Creates the 15 approved role-template definitions as Draft version 1.
- Creates inactive status, bonus, intelligence, and notification drafts.
- Requires an executive actor for approval, publication, retirement, policy
  versioning, assignment preview, and assignment activation.
- Creates successor Draft versions without changing published source records.
- Saves employee role assignments as inactive drafts and activates only an
  explicitly confirmed exact-100-percent set backed by effective published
  templates.

Migration `crm.0198_kpi_release_governance` creates only
`crm_kpipolicyapproval`, its six protected policy relationships, actor
relationships, one lookup index, and five integrity constraints. No policy or
employee data is seeded by the migration.

## Stage 8 Executive Intelligence

`crm.services.kpi_intelligence` extends the Stage 7 read architecture. It
consumes digest-verified Stage 5 approved snapshots, immutable Stage 6 bonus
results, and Stage 7 scoping/filter services. It never calls the Stage 4
calculation engine and never recalculates approved history.

The Intelligence Center uses an audience registry and independently loaded
widgets for company health, Red/Yellow/Green intelligence, department,
manager, employee, review, trend, location, bonus-readiness, action, and data
quality summaries. Signed action links are reauthorized on the server.
`crm.services.kpi_reporting` builds the same authorized evidence into PDF,
Excel, CSV, and print reports with HTML and spreadsheet-formula sanitization.

`KPIIntelligenceRuleSet` is a versioned, effective-dated policy model for
thresholds, alert severity, trend sufficiency, workload, review completion,
and company-health component weights. Published versions are immutable and
publication is restricted to CEO or Super Admin through the service.

Migration `crm.0196_kpi_intelligence_rules` creates only
`crm_kpiintelligenceruleset`, its lookup index, and validation constraints. It
contains no seed, data operation, rename, deletion, or protected-table change.
Fresh, populated, rollback, and reapply checks passed without changing
protected counts or ID ranges.

## Stage 9 Notifications and Automation

Stage 9 reuses the existing `AutomationNotification` inbox. A versioned
`KPINotificationRule` and child escalation rules configure event types,
reminder offsets, schedules, recipients, severity, retries, batch size, and
lookback. No final company policy is seeded.

`crm.services.kpi_automation` reads Stage 5 review state and approved
snapshots, Stage 6 immutable bonus calculations, and Stage 8 intelligence
results. It does not call the Stage 4 engine or change a source record.
`crm.services.kpi_notifications` creates recipient-specific CRM notifications
and immutable `KPINotificationEvent` metadata using stable deduplication keys.

Daily, weekly, monthly, quarterly, and annual evaluations run through a
bounded management command. Every run stores counters, retries, timestamps,
and sanitized failure state. There is no external provider, uncontrolled
process, or installed scheduler.

Migration `crm.0197_kpi_notifications_automation` creates only four Stage 9
tables. It has no data operation, seed, deletion, rename, or protected-table
change.

## Deferred Stages

- Later stage: seed and verify approved version 1 role templates
- Stage 10: final testing and controlled deployment preparation
- Later stage: separate final bonus approval and payment workflow
- Later stage: evidence file handling and authorized unlock workflow

No deferred model or workflow is represented by a placeholder table. Custom
authorization codenames remain deferred; Stage 5 reuses current organization
roles without changing the authorization schema or middleware.
