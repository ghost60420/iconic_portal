# KPI Stage 4 Calculation Engine

## Scope

Stage 4 adds the read-only KPI calculation service. It adds no model,
migration, page, route, API, dashboard, notification, permission, review
workflow, bonus calculation, or production deployment.

`crm.services.kpi_calculation_engine.KPICalculationEngine` is the only public
calculation implementation. Future KPI pages, reviews, dashboards, reports,
exports, notifications, and bonus review services must call this engine rather
than reproduce its formulas.

## Architecture

The engine has two calculation paths:

1. Pure calculations consume immutable input dataclasses and perform no query.
2. The model adapter loads active settings, effective Stage 3 assignments, and
   effective immutable Stage 2 template versions before calling the same pure
   calculations.

All arithmetic uses `Decimal`. Internal results are retained to six decimal
places. Display rounding is intentionally deferred to a future presentation
layer and must show no more than two decimal places.

The service returns frozen structured results for:

- One KPI item
- One KPI template version
- One employee role assignment
- One employee with one or several roles
- One historical review date
- A reusable weighted average

Every result includes score, weight, weighted score, calculated status, final
status, Critical Red state, calculation time, engine version, formula version,
and review date. Role and employee results also carry exact template and
assignment version references.

## Calculation Pipeline

The database-backed employee flow is:

1. Load the one active `KPISettings` version.
2. Load assignments effective on the review date.
3. Require assignment weights to total exactly `100`.
4. Bulk-load effective published or retired template versions and their items.
5. Require exactly one effective template version for every assigned role.
6. Validate the supplied metric IDs and reject inactive or unrelated items.
7. Score items through the measurement registry.
8. Weight item scores into template scores.
9. Weight template scores by Stage 3 assignment weights.
10. Apply aggregate status ranges and any Critical Red override.
11. Return an immutable, JSON-ready score snapshot.

No step writes to the database.

## Public Service

The primary APIs are:

- `KPICalculationEngine.score_item`
- `KPICalculationEngine.score_template`
- `KPICalculationEngine.score_role`
- `KPICalculationEngine.score_employee`
- `KPICalculationEngine.score_historical_review`
- `KPICalculationEngine.calculate_weighted_average`
- `KPICalculationEngine.status_for`
- `KPICalculationEngine.detect_critical_red`
- `KPICalculationEngine.score_employee_from_models`
- `calculate_employee_kpi_score`

The model adapter accepts a server-validated mapping of `KPIItemMetric`
instances keyed by `KPIItemDefinition` ID. It rejects missing, inactive,
unknown, and non-effective item IDs.

## Critical Red

An item marked Critical Red must include a reason and trigger. The engine keeps
the numerical calculated score and calculated status, then changes only the
final status to Red.

Critical Red details carry:

- Reason and trigger
- Time
- Source KPI ID and name
- Affected KPI role ID and name
- Affected employee ID

Details propagate from item to template, employee role, and employee result.
The engine performs no approval or audit write in this stage.

## Historical Behavior

Historical model calculations select assignments effective on the supplied
review date and a published or retired template version whose effective dates
cover that date. An ambiguous template-version schedule is rejected instead of
guessing.

Returned results retain:

- Calculation engine version
- Formula version
- Review date
- Template version for each role
- Assignment version for each role
- KPI settings range version
- Item range source and version

Stage 4 does not add a review record table. The future review workflow must
persist the returned `as_dict()` snapshot when a review is submitted or
approved. Approved history must display that stored snapshot and must not
recalculate against current templates, assignments, settings, or formulas.

## Performance

Pure calculations execute zero database queries. The complete model-backed
employee calculation uses four queries when an `EmployeeProfile` instance is
provided:

1. Active KPI settings
2. Effective assignments with related employee, role, and manager data
3. Effective template versions with role templates
4. All KPI item definitions for those versions

The same four-query bound is tested for one role and three roles. Template
definitions are reused from a per-engine cache; after the cache is warm, the
same calculation uses two queries. No calculation path writes to the database.

Measured on a local external SQLite test database with three roles:

- Cold one-shot call: `4` queries, `6.292 ms`
- Warm reused-engine call: `2` queries, `1.806 ms` average over 100 calls
- N+1 verification: no query growth from one role to three roles

## Future Bonus Integration

No bonus amount, eligibility total, approval, payment, currency conversion, or
commission record is read or written here. A future bonus service may consume
an approved `EmployeeScoreResult`; it must not duplicate or alter the employee
score formula.

## Known Limits

- Measurement configuration is supplied as server-validated metric input
  because Stage 2 stores a descriptive measurement method, not a formula enum.
- Review result persistence, approval, locking, evidence, and audit events are
  deferred.
- Only formula version `1.0` is executable by engine version `1.0.0`.
- No authorization is implied by passing an employee or manager ID.
- No UI or public endpoint exists.
