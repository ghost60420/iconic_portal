# KPI Formula Versioning

## Current Versions

- Calculation engine: `1.0.0`
- Formula: `1.0`

The engine rejects an unsupported formula version. Silent fallback to a
different formula is not allowed.

## Snapshot Contract

Every calculation result contains:

- `calculation_version`
- `formula_version`
- `calculated_at`
- `review_date`
- Numerical score and weighted score
- Calculated status and final status
- Critical Red metadata

Item and template results contain the KPI template version. Employee role
results contain both template and assignment versions. Employee results contain
the complete list of template and assignment version references.

Status results also retain the range source and source version. This
distinguishes global KPI settings from versioned item thresholds.

## Historical Accuracy

The model adapter selects definitions by the requested review date, not the
current date. Published and retired Stage 2 template versions are immutable,
and Stage 3 assignments carry an incrementing assignment version.

Calculating a historical date returns a complete immutable snapshot. A future
review record must save the output of `as_dict()` at submission and approval.
After approval, consumers must read that stored result rather than call the
current formula again.

Stage 4 intentionally creates no review table and performs no database write.
This keeps the engine independent of the later entry, approval, and locking
workflow.

## Change Policy

A formula behavior change requires:

1. A new formula version.
2. A compatible engine-version change.
3. Tests for the old and new result contracts.
4. An explicit effective-date or review-version selection rule.
5. No recalculation of approved historical snapshots.
6. Documentation of numerical differences.

Renaming a function or refactoring without changing output does not require a
formula version change, but it still requires regression tests.

Future engines may retain old formula implementations for authorized historical
reproduction. If they do not, the stored approved snapshot remains the source
of truth.

## Consumer Rule

All future KPI consumers must use
`crm.services.kpi_calculation_engine.KPICalculationEngine` or
`calculate_employee_kpi_score`.

The following must not duplicate formulas:

- Employee and manager pages
- Dashboards
- Reports and exports
- Bonus review calculations
- Notifications
- APIs

Consumers may format results, enforce permissions, or add workflow state, but
they must not recalculate weights or statuses independently.
