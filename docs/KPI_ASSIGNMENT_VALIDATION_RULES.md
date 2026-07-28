# KPI Assignment Validation Rules

## Row Rules

1. Employee and KPI template are required existing records.
2. New active assignments cannot target archived employees.
3. New assignments require an active KPI template.
4. Role weight must be `0.01-100.00`.
5. End date must be empty or on/after start date.
6. Archived assignments must be inactive.
7. Assignment version must be at least one.
8. Existing employee/template identity is immutable.

The database enforces the required/existing relationships in rule 1 through
foreign keys and rules 4-7 through constraints where applicable.
Employee/template active and archive state is enforced by the model and
service.

## Timeline Rules

Only rows meeting all conditions contribute on a date:

- `is_active=True`
- `is_archived=False`
- `start_date <= date`
- `end_date` is empty or `end_date >= date`

If no row contributes, the employee has no KPI assignment set and the total is
`0.00`. If any row contributes, the total must be exactly `100.00`. Totals such
as `90.00` and `110.00` are rejected. The same KPI template cannot overlap
itself for one employee.

Timeline validation evaluates every interval boundary, including future and
historical ranges. Atomic plural services validate the final proposed schedule
before any row is written.

## Manager Rules

1. Manager may be empty.
2. Manager must be an existing active user.
3. Manager must have an existing, unarchived Active or On Leave employee
   profile.
4. Employee and manager user IDs must differ.
5. Manager assignment does not alter authentication or visibility.
6. Manager changes require an actor and create an old/new history snapshot.

## History Rules

- Create starts at assignment version one.
- Each update increments the version by one.
- Deactivation and archival use distinct history change types.
- Weight, manager, dates, status, eligibility, notes, actor, reason, and time
  are retained.
- Assignment deletion and queryset bulk mutation are blocked.
- History update and deletion are blocked.
- Employee/template changes require archive-and-recreate.

## Bonus Rules

Bonus eligibility does not affect effective performance weight. The
bonus-eligible service filters assignments for future calculation only. Stage 3
does not calculate, approve, expose, or pay bonuses and does not touch
`SalesCommission` or payroll bonus data.

## Service and Database Boundary

Cross-row exact-total validation belongs to the transactional service/model
layer; no fragile cross-row database constraint is used. Per-row limits,
ordered dates, archive state, required foreign keys, and positive versions are
database constrained.

No form, template, route, or browser state is trusted for these rules.
