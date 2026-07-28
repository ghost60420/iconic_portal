# Iconic CRM KPI System Audit

## Repair Execution Decision - 2026-07-28

The pre-KPI database repair supersedes the audit stop condition for development
work on the reconciled branch and repaired database copy:

- The populated source data was loaded at the approved CRM `0141`, Marketing
  `0007`, and WhatsApp `0004` boundaries with all existing primary keys.
- All 56 missing approved migrations then applied normally; none were faked.
- Populated rollback to those three boundaries and full reapplication passed.
- SQLite integrity and foreign-key checks passed before and after repair.
- The Marketing migration identity and calendar regression are resolved.
- The unapproved FedEx automation migration remains excluded; approved FedEx
  carrier/link behavior is tested.
- The full result is **760 of 760 tests passed**.
- Employee profile warm queries improved from 18 to 14 and the populated main
  dashboard from 92 to 48. These remain performance debt, but the requested
  database and regression gates pass.

The tested baseline is commit `a9c2881` with annotated tag
`kpi-baseline-20260728`. Stage 2 starts from that tag on
`feature/kpi-stage-2-models`.

**SAFE TO START KPI STAGE 2** from the tested baseline using isolated database
copies.

Deployment remains **NOT SAFE TO DEPLOY**. No production or AWS action was
performed.

## Audit Status

- Stage: 1 - System audit
- Audit date: 2026-07-28
- Repository: Iconic CRM
- Working branch: `main`
- Audited commit: `2217ec1` (`Add GA4 Admin API diagnostics`)
- Locally cached tracking commit: `origin/main` at `2eb88d0`
- Decision: **STOP BEFORE STAGE 2**
- Deployment status: **NOT SAFE TO DEPLOY**

This audit is intentionally read only except for this document. No KPI model,
migration, route, template, permission, employee record, financial record, or
production configuration was created or changed.

## Reconciliation Addendum - 2026-07-28

The initial repository/migration reconciliation confirmed the Stage 1 stop
decision at that time:

- `git fetch origin` completed without merge or rebase.
- Local `main` remains 84 commits behind and 0 ahead of refreshed
  `origin/main`; it has not diverged.
- Production-lineage reports identify deployed Calendar hotfix `490df39`.
  The approved source base is its remote branch head,
  `origin/calendar-504-hotfix` at `1084fc9`, whose final commit only updates the
  recovery report.
- Clean branch `chore/pre-kpi-reconciliation` was created from that approved
  base in a separate worktree.
- The approved graph migrates a fresh database successfully through CRM
  `0191`, rolls approved CRM `0190`/`0191` back to `0189`, and reapplies them.
- A copied current development database fails at the first pending CRM
  migration, `0142`, because `crm_whatsappmessage` is absent despite the
  predecessor ledger.
- The development database also has a conflicting Marketing migration name,
  27 missing tables, 208 column differences, 81 index differences, 35
  foreign-key differences, and six constraint differences.
- The full clean-branch suite ran 760 tests: 759 passed and one date-sensitive
  Marketing calendar test failed reproducibly.
- No KPI code, production database, AWS setting, or deployment target was
  changed.

The source base is identified, but the restored/current-data database and full
regression gates have not passed. The updated decision remains:

**SUPERSEDED BY THE REPAIR EXECUTION DECISION ABOVE**

## Evidence Boundary

The audit used three sources:

1. The current working tree and its local SQLite development database.
2. The repository's migration graph and Django system/test tooling.
3. Read-only inspection of the locally cached `origin/main` Git reference.

No network fetch, merge, rebase, database backup, production database query, AWS
connection, or deployment command was performed. Therefore, the cached
`origin/main` findings must be confirmed against the remote repository before
Stage 2. The local database is development evidence only and must not be treated
as a representation or backup of production.

## Executive Decision

Stage 2 must not start in the current checkout. The audit found a serious
database and source-control reconciliation risk:

- The checked-out `main` branch is 84 commits behind its locally cached
  `origin/main`.
- The working tree contains many modified, staged, and untracked files,
  including migrations `0165` through `0190`.
- The cached upstream migration chain ends at `0182`, while the working tree
  contains additional staged or untracked migrations through `0190`.
- The local migration ledger reports CRM migrations after `0141` as unapplied,
  but at least one table from that unapplied range already exists.
- The current model code expects fields that the local database does not have.
- Several pending data migrations use a no-op reverse function and cannot
  provide a complete data rollback.
- No verified database backup or tested migration rollback is available.

Adding KPI migrations on top of this state could select the wrong migration
dependency, conflict with unpublished work, or apply a large unrelated migration
chain to protected CRM modules. That creates unacceptable employee, financial,
notification, production, and historical-data risk.

## Product Governance Review

### Business problem

The requested system provides consistent role-based performance measurement,
multi-role score aggregation, controlled manager review, bonus review, employee
privacy, and an immutable performance history.

### Departments benefiting

All listed departments benefit. Direct users are employees, assigned managers,
Directors, the CEO, Super Admins, Accounts, and Administration.

### Existing feature coverage

No existing feature safely provides the requested employee KPI workflow. The
current financial KPI scorecard is an accounting dashboard, not an employee
performance system. Existing employee, role, currency, notification, file, and
audit patterns can be reused without replacing their working behavior.

### Reuse decision

- Reuse the canonical employee profile as the host for a Performance tab.
- Reuse authentication, EmployeeProfile identity, Department, Position, Django
  Groups, established form/view patterns, currency formatting, notification
  presentation, and audit presentation patterns.
- Do not repurpose the accounting KPI scorecard, payroll bonus field, sales
  commission models, QuickCosting commission fields, or team sales performance
  service.
- Use a separate KPI role-template taxonomy. Requested KPI roles do not map
  one-to-one to login roles, authorization Groups, positions, or departments.

### Complexity decision

The business value justifies isolated KPI tables and services because historical
template snapshots, multi-role effective dates, approvals, locking, private
evidence, bonus privacy, and append-only audit records cannot be represented
safely by the existing profile or commission tables.

## Current System Audit

### 1. Employee profiles and employee database model

Current-checkout evidence:

- `crm/models_employee.py` exists as an untracked file and is imported by the
  modified `crm/models.py`.
- `EmployeeProfile` has a one-to-one relationship to Django `User`, a generated
  employee ID, legacy text position/department fields, normalized Position and
  Department references, one manager, photo, notes, and lifecycle status.
- The User relationship uses `PROTECT`, which is appropriate for historical
  employee identity.
- There is no weighted KPI role assignment, effective-dated role history,
  bonus-eligibility assignment, or KPI review relationship.
- The current local accounting employee screens use `BDStaff` and
  `BDStaffMonth`, which are payroll-oriented records and are not linked to
  `EmployeeProfile` or authentication identity.
- Disabled Performance tab buttons exist in the Bangladesh staff templates.
  They do not implement a KPI workflow and are not the canonical employee
  profile.

Cached-upstream evidence:

- The employee implementation is integrated through
  `crm/models_employee.py`, `crm/forms_employee.py`,
  `crm/views_people.py`, `crm/services/employee_profiles.py`, and people
  templates.
- The upstream employee edit flow is the correct existing screen to extend with
  a Performance tab or partial. Existing profile sections must stay in place.
- Employee profile, account, archive/deactivate, directory, and sales profile
  behaviors already exist and must remain unchanged.

Risk:

The current checkout is not a trustworthy base for employee KPI development
because its employee files and migrations are not cleanly aligned with the
checked-out commit or local database.

### 2. Role assignment model

The CRM currently has several distinct concepts:

- `UserAccess.role`: geographic access (`CA` or `BD`), not an employee job role.
- Django authentication Groups: reusable authorization roles; a user may have
  several Groups.
- `EmployeeProfile.position`: job title.
- `EmployeeProfile.department`: organizational department.
- `EmployeeProfile.manager`: one reporting manager.
- `BDStaff.role`: payroll text, not an authorization or KPI template relation.

Cached upstream role-management code supports multiple Django Groups per user
and protects important administrative role operations. Existing role labels
include CEO, Director, Manager, Sales, Production, Accounts, Merchandising,
Merchandiser, Supervisor, Finance, QC, Warehouse, HR, Admin, Read Only, and
Sales Manager.

This can support authorization but cannot safely store KPI target definitions,
weights, template versions, effective dates, assignment weights, or bonus
eligibility. KPI roles must be related to employees without renaming or changing
existing Groups, positions, departments, or geographic access roles.

### 3. Permission middleware and server authorization

Files inspected include:

- `crm/models_access.py`
- `crm/permissions.py`
- `crm/views_access.py`
- `crm/urls.py`
- cached-upstream employee and operations permission services

Findings:

- Authentication uses Django's standard session and authentication middleware.
- CRM routes commonly use permission wrappers from `crm/permissions.py`.
- `UserAccess` contains module flags for leads, opportunities, customers,
  inventory, production, shipping, AI, calendar, marketing, WhatsApp, costing,
  accounting, CEO tools, and related modules.
- Superusers bypass module wrappers.
- CEO-style access is currently represented partly by
  `can_view_ceo_tools`, while employee access also uses staff status, model
  permissions, Groups, department, and manager relationships.
- The access-management view allows staff or superusers to manage access, with
  additional protection around superuser and CEO-tool access.
- Cached-upstream employee services use Group, staff, superuser, department,
  and manager checks. Team performance access is broader than the requested
  explicit assigned-manager KPI rule.

Permission logic is currently distributed across several mechanisms. KPI access
must use one object-level policy service and enforce the same policy in every
view, form action, export, private evidence download, and approval endpoint.
Template visibility or hidden buttons alone are insufficient.

### 4. Authentication rules

- The project uses Django's default `auth.User`; no custom user model or custom
  authentication backend was found.
- Login uses Django authentication URLs and redirects into the existing CRM.
- Standard session, authentication, CSRF, and security middleware are enabled.
- KPI work does not require changing login, authentication backends, user IDs,
  or session behavior.

### 5. Bonus and commission code

Existing compensation paths are separate and must remain separate:

- `BDStaffMonth.bonus_bdt` is a payroll bonus field included in Bangladesh
  monthly staff pay.
- `SalesCommission` is invoice-backed sales commission data with approval and
  payment states.
- QuickCosting/order profitability code contains sales commission concepts.
- Existing invoice and costing currency behavior stores CAD, USD, and BDT
  explicitly rather than automatically converting them.
- `crm/services/costing_currency.py` contains established money formatting,
  including the existing CAD display format.

KPI bonus records must use new KPI-owned tables. They must not update
`BDStaffMonth.bonus_bdt`, `SalesCommission`, invoice, payment, payroll,
QuickCosting, or commission records. The requested formula currently calculates
a bonus score only; it does not define a monetary base, pool, cap, or conversion
from score to amount. Monetary calculation cannot be implemented until that
business rule is approved.

### 6. Dashboard structure

- The main dashboard is a large existing view with established cards and
  permission-filtered data.
- The CEO dashboard and team-performance views are stable protected modules.
- An existing `/accounting/kpi-scorecard/` route reports financial/accounting
  metrics. It is not employee KPI functionality and must not be renamed,
  repurposed, or have its permission boundary weakened.
- Cached-upstream people templates provide the canonical employee-profile UI.

Recommendation:

- Add the Performance tab to the canonical employee profile through a partial.
- Build the authorized KPI dashboard as an isolated route/view because its
  object-level privacy, bonus privacy, approval filters, and query budget cannot
  be added safely to an unrelated existing dashboard.
- Reuse existing card, filter, table, status, pagination, and responsive styling
  patterns rather than duplicating visual components.

The isolated dashboard is justified because none of the current dashboards owns
employee KPI authorization or review data, and the task explicitly prohibits
changing existing dashboards.

### 7. Notification system

- `AutomationNotification` supports notification type, priority, read/resolved
  state, record relationships, target URLs, and assignment concepts.
- The automation engine already supplies dashboard notification context.
- Shipment notification behavior has focused tests, including failure and
  retry behavior.
- The local database has notification schema drift: the
  `crm_automationnotification` table exists even though its creating migration
  is recorded as unapplied, and its columns do not fully match current model
  expectations.

After baseline reconciliation, KPI alerts may be integrated through a small KPI
adapter that creates existing notification records. Core notification logic,
email sending, background queues, and existing delivery behavior should not be
changed for early KPI stages.

### 8. Database migration history

Local development database findings:

- SQLite integrity check: `ok`.
- CRM migrations through `0141_order_lifecycle` are recorded as applied.
- CRM migrations `0142` through `0190` are reported as unapplied.
- `crm_automationnotification` exists despite its migration being reported as
  unapplied.
- EmployeeProfile, Department, Position, CRMAuditLog, and SalesCommission
  tables expected by later code are absent.
- The database `crm_useraccess` table does not include all fields expected by
  the current model code.
- Local sample counts observed during the audit: 33 users, 28 UserAccess rows,
  zero auth Groups, zero BDStaff records, and zero BDStaffMonth records.

Repository findings:

- Pending migrations include changes to protected WhatsApp, inventory, costing,
  invoice, production, notification, employee, access, and accounting areas.
- Some pending data migrations use `RunPython.noop` for reverse execution.
- Cached `origin/main` ends at migration `0182`; local work continues through
  `0190`.
- `makemigrations --check --dry-run` reports model state consistency, but that
  does not prove database schema consistency or migration safety.

No KPI migration number or dependency may be selected until the source branch,
working tree ownership, remote branch, migration graph, development schema, and
production migration state are reconciled.

### 9. Audit log system

Existing audit patterns include:

- `CRMAuditLog`
- `SystemActivityLog`
- costing audit records
- invoice audit records
- accounting-entry audit records
- cached-upstream employee and role audit services

`CRMAuditLog` provides actor, module, record, action, field, previous/new text,
target URL, and timestamp. It does not provide all required KPI relationships,
structured old/new snapshots, written reasons, failed approval attempts, or a
KPI-specific immutability contract.

Recommendation:

Create an append-only `KPIAuditLog` that follows the existing presentation and
actor-recording pattern but adds structured old/new values, reason, related
employee, related period, related KPI object, outcome, timestamp, and request
context where appropriate. Normal users must have no edit or delete path. Any
admin registration must be read-only and deletion-disabled.

### 10. File upload system

- Existing media settings and FileField/ImageField patterns are available.
- Existing image-validation services demonstrate file-size, extension, and
  content-type checks.
- Development serves media through Django only when `DEBUG` is enabled.
- Production media delivery rules were not verified.
- The current media directory was not inspected for employee data contents.

KPI evidence may contain private employee, client, quality, or financial
information. Evidence must use a private storage path and a permission-gated
download view. It must not be exposed as an unrestricted `/media/` URL. Server
validation must enforce size, allowed extensions, detected content/MIME type,
safe generated names, and access checks. File contents and paths must not be
written to browser logs.

### 11. Current test setup

- Tests use Django's built-in test runner.
- No pytest or coverage configuration was found.
- The current test tree contains 52 test classes and 273 test methods.
- Existing focused coverage includes employee/accounting UI, accounting RBAC,
  dashboards, notifications, employee profiles, roles, mentions, and operations
  controls across the current and cached-upstream implementations.
- There are no KPI model, calculation, permission, locking, bonus, evidence, or
  dashboard tests.
- Existing bounded-query tests establish patterns that can be reused.

Commands and results:

```text
python3 manage.py check
PASS - System check identified no issues (0 silenced).

python3 manage.py makemigrations --check --dry-run
PASS - No changes detected.

python3 manage.py test \
  crm.tests.test_employee_module_ui \
  crm.tests.test_accounting_rbac \
  crm.tests.test_dashboard_and_misc.MainDashboardTests \
  crm.tests.test_shipment_notifications_async --verbosity 1
PASS - Ran 17 tests in 16.082s; OK.
```

The passing tests do not resolve the migration/schema drift because Django
created a fresh test database from the migration graph.

## Reusable Existing Features

1. Django User identity, login, session, CSRF, and authentication middleware.
2. Canonical EmployeeProfile, Department, Position, manager, and lifecycle
   concepts after source reconciliation.
3. Django Groups and existing role-management UI for authorization labels.
4. Existing employee profile screen and responsive form/layout patterns.
5. Existing card, filter, table, badge, history, and dashboard patterns.
6. Existing money/currency codes and CAD formatting helper.
7. Existing notification model and presentation after its migration state is
   repaired.
8. Existing audit display and service conventions.
9. Existing safe transaction, permission-wrapper, upload-validator, pagination,
   and bounded-query test patterns.
10. Existing Department and manager relationships as inputs to policy, without
    making them the sole source of KPI access.

## Missing Features

1. Versioned role-based KPI templates and item definitions.
2. Definitions for the requested Merchandiser and Pattern Master templates.
3. Effective-dated, weighted, multi-role employee KPI assignments.
4. Atomic validation that role and KPI item weights total exactly 100 percent.
5. Daily, weekly, monthly, quarterly, and annual KPI review periods.
6. Historical template and assignment snapshots.
7. Decimal scoring, traffic-light settings, and Critical Red override.
8. Employee submission, manager review, Director review, correction, rejection,
   and approval workflow.
9. Record finalization, locking, permissioned unlock, and written unlock reason.
10. Private evidence storage and protected download.
11. KPI-specific append-only audit records and failed-approval-attempt logging.
12. Individual/team/company bonus scoring and separate KPI bonus approval.
13. Approved monetary bonus business rule and source of team/company scores.
14. Performance tab and private employee history presentation.
15. Authorized KPI dashboard, filters, trends, reports, and exports.
16. KPI notifications.
17. Complete required KPI, security, regression, performance, migration, and
    rollback tests.

## Specification Decisions Required

These items must be approved before their dependent stages:

1. KPI item names and weights for Merchandiser and Pattern Master. They are in
   the required role list but not in the supplied first-version definitions.
2. The source and approval owner for team KPI score.
3. The source and approval owner for company KPI score.
4. The monetary bonus base/pool, score-to-amount formula, cap, and treatment of
   Yellow results. The supplied 60/25/15 formula produces a score, not an amount.
5. Whether daily and weekly tracking are scored records, progress entries, or
   rollups into the monthly review.
6. Director department assignment source. Current departments and Groups do not
   encode an explicit Director-to-department authorization mapping.
7. Exact permissions for allowed employee result entry and paid-status changes.
8. Evidence retention, maximum size, allowed types, and production private
   storage provider.
9. Rules for assignment overlap and mid-period role changes. Recommended rule:
   assignment weights must total 100 percent for every effective date interval,
   and a review snapshots assignments active on the period start date.
10. Whether a Critical Red affects one role review or the entire employee period.
    The request says "related review"; the safest default is the entire employee
    period until explicitly cleared by authorized review.

## Recommended Data Architecture

Names are recommendations only. They must be checked against the reconciled
codebase before implementation.

### Template data

- `KPIRoleTemplate`: stable KPI role identity and active state.
- `KPITemplateVersion`: effective version, lifecycle state, and publication
  metadata.
- `KPIItemDefinition`: name, description, purpose, measurement method, target,
  weight, frequency, data source, Green/Yellow/Red ranges, approval/evidence/
  bonus flags, Critical Red rule, activity state, and ordering.

Published template versions must be immutable. Editing a future template creates
a new version. Historical reviews retain the version and field snapshots used.

### Assignment data

- `KPIEmployeeRoleAssignment`: EmployeeProfile, template/version, Decimal role
  weight, manager, start/end dates, active state, bonus eligibility, notes, and
  audit metadata.

Inactive or expired assignments remain in history and are excluded from new
period snapshots.

### Review data

- `KPIReviewPeriod`: daily, weekly, monthly, quarterly, or annual period.
- `KPIReview`: employee period, calculated/final status, workflow state, final
  approval, lock, and unlock metadata.
- `KPIRoleReview`: snapshotted template version, role weight, score, and status.
- `KPIItemResult`: snapshotted item definition/target/weight, actual, Decimal
  score, employee comment, manager comment, and workflow state.
- `KPIEvidence`: private evidence metadata and storage key.
- `KPICriticalIncident`: reason, date, responsible person, manager comment,
  corrective action, and CEO/Director review.
- `KPIApproval`: actor, level, outcome, reason/comment, and timestamp.

### Bonus and settings data

- `KPIBonusReview`: separate individual/team/company scores and weights,
  currency, monetary basis, estimated amount, recommended amount, approved
  amount, approval/payment state, and approvers.
- `KPISettings`: typed/versioned traffic-light thresholds, bonus component
  weights, and permitted settings. Thresholds must be non-overlapping and cover
  0 through 100. Bonus component weights must total 100 percent.

Do not use existing payroll or commission tables for KPI bonus records. Store
the explicit currency code and use the existing formatter. Do not convert
currency automatically.

### Audit data

- `KPIAuditLog`: append-only user/action/outcome, structured old/new values,
  reason, employee, period, related object, date/time, and request metadata.

Use `PROTECT` or immutable snapshots for historical identities and definitions.
Avoid cascading deletion of approved KPI history.

## Calculation and Validation Design

Use `Decimal`, not floating-point arithmetic:

```text
item contribution = item score * (item weight / 100)
role score = sum(item contributions)
employee contribution = role score * (employee role weight / 100)
employee final score = sum(employee contributions)
```

Round only for display, to at most two decimal places.

Default calculated status:

- Green: 85 through 100
- Yellow: 70 through 84.99
- Red: below 70

An applicable Critical Red incident overrides the calculated status to Red.
The calculated score remains available for audit, but the effective status and
bonus eligibility must use Red.

Cross-row totals cannot be enforced reliably with a simple database check
constraint. Use all of the following:

1. Per-row database constraints for values from 0 through 100.
2. Service/formset validation of the full item or assignment collection.
3. `transaction.atomic()` and `select_for_update()` around publication and
   assignment changes.
4. Publish/activate only when the effective total is exactly 100.00.
5. Prevent direct writable admin paths that bypass the service.
6. Recalculate on the server; never trust browser totals.

For role assignments, validation must cover each overlapping effective-date
interval, not only rows whose `active` flag is true on the save date.

## Permission Design

Create one object-level KPI policy service. All UI and server actions must call
the same policy functions.

- Employee: own permitted review data and approved history/bonus only; no
  target, weight, approval, unlock, or other-employee access.
- Assigned manager: only assignments where that user is the recorded manager;
  no self-approval or self-bonus approval.
- Director: only explicitly assigned departments; department access must not be
  inferred from a display label alone.
- CEO/Super Admin: all KPI records and settings, subject to separation of duties
  for self-approval.
- Export and evidence download: apply the same row and field-level privacy as
  HTML views.

Use dedicated Django permissions for template management, assignment management,
review, final approval, unlock, bonus approval, payment-status change, export,
settings, audit viewing, and private evidence access.

Record denied approval, unlock, bonus, export, and private-evidence attempts in
the KPI audit log without logging secrets or file contents.

## Recommended File Changes

Do not edit these until the repository and migrations are reconciled.

### Existing files likely to change

- `crm/models.py`: import KPI models following the established split-model
  pattern.
- `crm/urls.py`: include a small isolated KPI URL module.
- Canonical employee profile view/template files from the confirmed upstream
  baseline: add a Performance tab and partial without moving existing content.
- Relevant shared navigation or permission-seeding migration only if required
  after review.

The final paths must be read again from the clean implementation branch. The
current checkout is not authoritative enough to approve edits.

### Recommended new files

- `crm/models_kpi.py`
- `crm/kpi_urls.py`
- `crm/views_kpi.py`
- `crm/forms_kpi.py`
- `crm/services/kpi_permissions.py`
- `crm/services/kpi_scoring.py`
- `crm/services/kpi_workflow.py`
- `crm/services/kpi_dashboard.py`
- `crm/services/kpi_evidence.py`
- `crm/templates/crm/kpi/` templates and employee Performance partial
- `crm/tests/test_kpi_models.py`
- `crm/tests/test_kpi_scoring.py`
- `crm/tests/test_kpi_permissions.py`
- `crm/tests/test_kpi_workflow.py`
- `crm/tests/test_kpi_bonus.py`
- `crm/tests/test_kpi_dashboard.py`
- `crm/tests/test_kpi_evidence.py`

This list is deliberately consolidated by responsibility. Do not create a
separate page, template, or service per employee or KPI role.

Protected lead, email, marketing, WhatsApp, production, financial, deployment,
systemd, and nginx files are not recommended for KPI changes.

## Migration Plan

### Gate 0 - Reconcile the baseline

1. Identify and preserve the owner and purpose of every current local change.
2. Create a Git checkpoint for approved local work without staging unrelated
   files.
3. Fetch the remote only after approval and confirm the true target branch.
4. Compare local migrations `0165` through `0190` with the confirmed remote
   graph and resolve numbering/dependency conflicts without rewriting applied
   production history.
5. Obtain the production migration ledger and schema inventory through the
   approved access path.
6. Reconcile the notification table and UserAccess schema drift.
7. Create a clean KPI branch/worktree from the confirmed baseline. Do not
   implement KPI work in the current dirty `main` checkout.

### Gate 1 - Backup and restore proof

1. Identify the real database engine, host, backup method, retention location,
   and restore owner. Do not infer production from local SQLite.
2. Create a timestamped production backup using the approved operational
   process.
3. Restore that backup into an isolated development/staging database.
4. Record row counts, migration ledger, constraints, and checksums for protected
   employee and financial tables.
5. Prove the restore can be opened and queried before applying any KPI migration.

### Gate 2 - Additive schema migration

1. Generate the next migration number from the reconciled graph.
2. Create only new KPI tables, indexes, constraints, and permissions.
3. Do not remove or rename any existing table, field, index, role, ID, or record.
4. Run `sqlmigrate` and review every statement.
5. Apply to the restored development/staging database.
6. Run `manage.py check`, model tests, migration tests, protected-module
   regression tests, and row-count comparisons.
7. Test reverse migration before any KPI data is created.

### Gate 3 - Seed templates separately

1. Use an idempotent, versioned seed command or reviewed data migration after
   Merchandiser and Pattern Master definitions are approved.
2. Validate every role template total is exactly 100 percent before publication.
3. Do not assign employees or alter auth roles during template seeding.
4. If a data migration is used, provide a meaningful reverse that removes only
   the exact unreferenced seed version created by that migration.

### Later-stage migration rules

- Role assignment, review, evidence, approval, bonus, and audit schema should be
  added in focused additive migrations as their stages are implemented.
- No stage may automatically create employee assignments, reviews, bonuses, or
  approvals.
- No migration may update existing commissions, payroll bonuses, invoices,
  payments, employee IDs, auth roles, or historical records.
- Approved review snapshots must remain unchanged when templates change.

## Rollback Plan

Stage 1 rollback is deletion of this documentation file only; there is no
runtime or database rollback.

For future KPI stages:

1. Stop new KPI writes through a KPI-specific feature flag or route disablement.
2. Preserve and export KPI audit/history data before any reverse operation.
3. Roll back application code to the exact pre-KPI commit.
4. Reverse only the verified KPI migration range in staging first.
5. Confirm no existing CRM tables or row counts changed.
6. Restore the verified pre-migration backup if schema or data checks fail.
7. Re-run login, employee profile, permission, lead, opportunity, accounting,
   sampling, production, quotation, invoice, payment, dashboard, upload,
   notification, email, automation, and WhatsApp checks.
8. Record the rollback actor, commands, timestamps, results, and incident reason.

Once approved KPI history exists, destructive reverse migration is not an
acceptable normal rollback. Prefer disabling KPI routes and reverting
application code while retaining additive tables until a separately approved
data-retention decision is made.

## Stage Plan and Exit Gates

1. Stage 1: audit - completed with stop decision.
2. Stage 2: models and migrations - blocked by baseline reconciliation and
   backup/restore proof.
3. Stage 3: role templates - blocked by missing Merchandiser and Pattern Master
   definitions.
4. Stage 4: assignments - requires effective-date total rules and model tests.
5. Stage 5: Performance tab - requires confirmed canonical profile baseline and
   object policy.
6. Stage 6: entry/approval workflow - requires locked-state and private-evidence
   design.
7. Stage 7: calculations - requires Decimal and Critical Red test suite.
8. Stage 8: bonus - blocked by monetary and team/company source rules.
9. Stage 9: permissions/audit - requires object-level policy and append-only
   audit enforcement.
10. Stage 10: dashboard/reports/notifications - requires bounded-query tests and
    reconciled notification schema.
11. Stage 11: all tests - requires full security, regression, performance,
    migration, and rollback coverage.
12. Stage 12: deployment preparation - requires confirmed remote/branch, host,
    project folder, service, backup, monitoring, and rollback procedure.

Complete and report each stage before starting the next.

## Performance Baseline

Stage 1 changes documentation only and introduces no endpoint, query, template,
or runtime code.

- KPI query count before: not applicable; no KPI endpoint exists.
- KPI query count after: not applicable; no KPI endpoint was added.
- Query-count delta: 0 runtime queries.
- Warm response time: not applicable.
- Cold response time: not applicable.
- N+1 result: no runtime path changed; KPI N+1 behavior is not yet testable.

Required implementation budgets:

- Employee KPI detail/Performance data: no more than 8 queries.
- KPI dashboard: no more than 10 queries.
- KPI search/filter results: no more than 6 queries.
- KPI notifications: no more than 5 queries.

Use `select_related`, bounded `Prefetch`, conditional aggregates, pagination, and
query-count tests against multiple employees, assignments, roles, periods, and
evidence records. Query counts must remain constant as result counts grow.

## Stage 1 Verification

### Passed

- Read-only audit of employee/profile systems.
- Read-only audit of role and permission systems.
- Read-only audit of authentication.
- Read-only audit of payroll bonus, sales commission, costing commission, and
  currency behavior.
- Read-only audit of dashboard, notification, migration, audit, upload, and test
  systems.
- Local SQLite integrity check.
- Django system check.
- Django migration model-state check.
- Focused 17-test regression run for current employee/accounting UI, accounting
  RBAC, main dashboard, and shipment notifications.
- Confirmed no KPI runtime or database change was made.

### Failed

- Database migration ledger and actual schema are not aligned.
- Current branch and local migration chain are not aligned with cached
  `origin/main`.
- Production backup, restore, schema, migration ledger, and rollback have not
  been verified.

### Not tested

- Production or staging database.
- Production authentication, media protection, notifications, dashboards, or
  financial workflows.
- Full CRM regression suite.
- KPI behavior, because Stage 2 through Stage 11 have not been implemented.
- Migration application or rollback, because the baseline is unsafe.
- Remote Git state, AWS host, project folder, service name, deployment, and
  monitoring.

## Required Conditions to Resume Stage 2

All of the following are required:

1. Preserve and reconcile the current dirty working tree.
2. Confirm the latest remote target branch and migration graph.
3. Establish a clean KPI implementation branch/worktree.
4. Confirm development, staging, and production migration ledgers and schemas.
5. Repair or formally account for all schema drift.
6. Verify a production backup and an isolated restore.
7. Prove the pre-KPI migration plan and rollback on the restored database.
8. Approve the two missing template definitions.
9. Approve bonus monetary and team/company score rules.
10. Confirm private evidence storage and retention rules.

Until these conditions are met, the correct status is:

**NOT SAFE TO DEPLOY**
