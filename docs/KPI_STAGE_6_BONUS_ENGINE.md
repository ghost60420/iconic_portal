# KPI Stage 6 Bonus Engine

## Scope

Stage 6 adds a service-only Bonus and Incentive Engine. It evaluates approved
Stage 5 review snapshots and may store a separate immutable estimated bonus
record. It does not expose a route, API, admin page, employee view, payroll
integration, approval command, payment command, or commission relationship.

## Architecture

### `KPIBonusWeightProfile`

A published, effective-dated version defines:

- Individual, team, and company weights totaling exactly `100.00`
- A configurable team scope code and name
- Version, status, creator, publisher, and effective dates

Team scopes such as department, factory, sales, or executive are configuration
records. The engine contains no department-name switch or hard-coded team list.

### `KPIBonusRuleSet`

A published, effective-dated version defines:

- Bonus enabled state and minimum score
- Default, minimum, and maximum attendance multiplier
- Critical Red behavior: blocked, not eligible, or allowed
- Bonus floor, bonus cap, currency, and approval requirement
- Eligible employee statuses
- The published weight-profile version

### `KPIBonusCalculation`

One approved employee review may create one immutable historical calculation.
The record references its review, optional team/company source reviews, rule
set, weight profile, employee, and manager. It also freezes the department,
team scope, review date, eligibility reasons, source digests, formula versions,
and complete JSON result with a SHA-256 digest.

## Evaluation Pipeline

1. Load the selected rule and all source reviews in bounded queries.
2. Require approved or locked reviews and verify their Stage 5 digests.
3. Read scores and Critical Red only from approved Stage 4 result snapshots.
4. Resolve each frozen assignment version through append-only Stage 3 history.
5. Exclude roles whose historical assignment version was not bonus eligible.
6. Apply configured individual, team, and company weights.
7. Apply minimum score, employee status, Critical Red, and attendance rules.
8. Estimate the amount from the supplied base amount without conversion.
9. Apply the configured floor and cap.
10. Store a separate immutable result; create no payment or approval.

Pending reviews return a structured evaluation but cannot create a final stored
calculation. Existing stored calculations are verified and returned unchanged.

## Performance

Evaluation with individual, team, and company sources uses three queries:

1. Published rule and weight profile
2. All requested review snapshots and employee metadata
3. All required assignment-history versions

The query count does not grow with the number of roles or source reviews.

## Migration and Rollback

Migration `crm.0195_kpi_bonus_engine` creates only Stage 6 tables. It was tested
on a fresh database and a populated copy containing an approved Stage 5 review.
Rollback to `crm.0194` and reapplication pass.

After historical bonus records exist, schema rollback would delete those
records. Operational rollback should retain the schema and revert application
code after a verified database backup.
