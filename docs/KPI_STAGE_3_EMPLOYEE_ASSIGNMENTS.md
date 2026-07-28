# KPI Stage 3 Employee Assignments

## Scope

Stage 3 provides employee KPI role assignment data, effective-date behavior,
history, validation, and services. It adds no page, route, API, dashboard,
score, bonus calculation, notification, or employee assignment seed.

KPI role templates remain separate from authentication groups, `UserAccess`,
and existing employee position/department fields.

## Assignment Model

`EmployeeKPIRoleAssignment` contains:

- Existing `EmployeeProfile` reference
- Existing `KPIRoleTemplate` reference
- Decimal role weight with two decimal places
- Optional manager user
- Required start and optional end date
- Active, archived, and bonus-eligible flags
- Private notes
- Created/updated user and timestamps
- Positive assignment version

Employee and template identity cannot be changed on an existing assignment.
Archive the old row and create a new assignment instead. Employee and template
foreign keys use `PROTECT`; manager and actor foreign keys use `SET_NULL`.

## Effective Behavior

An assignment is current on a date when it is active, not archived, has
started, and has not ended. The model exposes:

- `is_current(as_of)`
- `is_future(as_of)`
- `is_expired(as_of)`
- `is_archived`
- `effective_weight(as_of)`

Future assignments do not affect earlier dates. Expired assignments remain
queryable for their historical effective dates. Inactive and archived rows
remain stored but do not contribute.

## Weight Behavior

Individual weights must be greater than `0.00` and no more than `100.00`.
Whenever an employee has one or more effective assignments on a date, their
combined weight must be exactly `100.00`.

No effective assignment is a valid unassigned state with total `0.00`. A
non-empty effective schedule totaling anything other than `100.00` is invalid.
Atomic plural create/update services allow valid `60/40`, `50/30/20`, or
decimal schedules without persisting an incomplete intermediate state.

The timeline validator checks each start date and the day after each end date.
It also rejects overlapping copies of the same KPI template.

## Manager Rules

- A manager is optional, supporting approved executive assignments.
- An employee cannot manage their own KPI assignment.
- The manager user must exist and be active.
- The manager employee profile must exist, be unarchived, and be Active or On
  Leave.
- Assigning a manager does not grant any permission.
- Manager changes increment the assignment version and preserve old/new IDs in
  history.

## Bonus Eligibility

`bonus_eligible` is stored per assignment. A non-eligible assignment remains
part of performance weight and current assignment queries but is omitted by
the bonus-eligible assignment service. No bonus amount, approval, payment, or
commission data is created or changed.

## History

Every service or direct model save creates one
`EmployeeKPIRoleAssignmentHistory` row in the same transaction. The snapshot
contains employee, template, weight, manager, dates, active/archive state,
bonus eligibility, notes, actor, reason, time, and assignment version.

History rows cannot be edited or deleted through the model/queryset layer.
Assignments cannot be deleted or bulk-updated; they must use the service and
remain available after deactivation or archival.

## Service API

`crm.services.kpi_assignments` provides:

- `assign_kpi_role`
- `assign_kpi_roles`
- `update_assignment`
- `update_assignments`
- `deactivate_assignment`
- `archive_assignment`
- `list_current_assignments`
- `list_assignments_for_date`
- `calculate_total_active_weight`
- `validate_exact_100`
- `get_assigned_manager`
- `get_bonus_eligible_assignments`
- `get_employee_kpi_template_set`
- `detect_invalid_overlaps`

Mutation services resolve database IDs, use atomic transactions, lock the
employee/assignment rows, validate the complete proposed timeline, and then
write assignment and history records together.

## Migration and Rollback

Migration `crm.0193_kpi_employee_role_assignments` creates only the assignment
and history tables, constraints, and indexes. It seeds no data.

Tested structural rollback:

```bash
python3 manage.py migrate crm 0192
python3 manage.py migrate crm 0193
```

Once real assignments exist, rolling back `0193` would remove KPI assignment
history and must not be used as a routine production rollback. Prefer an
application rollback or additive corrective migration after taking a verified
backup.

## Known Limits

- No assignment administration UI or API exists.
- No KPI view permissions exist yet.
- No review record snapshots consume assignment versions yet.
- No calculation or bonus engine exists.
- Approved role-template seed data remains a separate later-stage requirement.
