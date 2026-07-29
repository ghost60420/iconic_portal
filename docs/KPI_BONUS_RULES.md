# KPI Bonus Rules

## Formula

The engine consumes approved snapshot scores:

```text
Final bonus score =
  eligible individual score x individual weight
  + team snapshot score x team weight
  + company snapshot score x company weight
```

Weights are percentages and must total exactly `100.00`.

For an amount estimate:

```text
Calculated amount =
  base bonus amount x final bonus score / 100 x attendance multiplier
```

The configured floor and cap are then applied. Currency is copied from the rule
set. The engine performs no currency conversion.

## Eligibility States

- `eligible`: all required sources and configured rules pass
- `not_eligible`: score, employee status, assignment, or configured Critical
  Red policy disqualifies the employee
- `pending`: a required source review is not approved or is missing
- `blocked`: configuration is disabled or source integrity cannot be proven

Reasons are stable machine-readable codes stored in final snapshots, including:

- `bonus_disabled`
- `critical_red`
- `insufficient_score`
- `employee_inactive`
- `assignment_inactive`
- `bonus_ineligible_assignment`
- `*_review_missing`
- `*_review_not_approved`
- `*_snapshot_invalid`

## Rule Precedence

1. Source approval and digest integrity
2. Historical assignment eligibility
3. Bonus enabled state
4. Configured employee statuses
5. Critical Red behavior
6. Component weighting and minimum score
7. Attendance multiplier
8. Payout floor and cap

Blocked and pending evaluations do not produce payment or payroll data.

## Historical Assignment Eligibility

Stage 5 freezes assignment IDs and versions but not the `bonus_eligible` flag.
Stage 6 therefore resolves the exact version in
`EmployeeKPIRoleAssignmentHistory`, which is append-only. It never uses the
current assignment row to determine historical eligibility.

When only some roles are bonus eligible, the engine uses their approved Stage 4
role weighted scores and normalizes by their frozen eligible role weights.
Performance scores remain unchanged.
