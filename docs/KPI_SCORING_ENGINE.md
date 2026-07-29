# KPI Scoring Engine

## Numeric Rules

All input is normalized to finite `Decimal` values. Scores are constrained to
`0` through `100` and stored in result objects to six decimal places.

By default, negative actuals, targets, minimums, and maximums are rejected.
Callers may explicitly allow negative source values for a future approved KPI,
but the resulting score must still remain within `0` through `100`.

Optional minimum and maximum values validate the actual result. Counts must be
whole numbers. Percentages cannot exceed `100`. Manual scores must already be
between `0` and `100`.

## Measurement Types

The engine supports:

| Type | Behavior |
| --- | --- |
| Percentage | Uses `actual / target * 100`, or actual as the normalized score when target is omitted |
| Count | Numeric target formula with whole-number validation |
| Currency | Numeric target formula; no currency conversion |
| Boolean | `100` when actual equals target, otherwise `0`; target defaults to true |
| Manual score | Uses the validated actual score directly |
| Duration | Numeric target formula, normally lower-is-better |
| Decimal | Numeric target formula |

Measurement dispatch is centralized in `MEASUREMENT_SCORERS`. Adding a future
measurement type requires one registered scorer and focused compatibility
tests; it must not create a parallel calculation path.

## Direction Rules

Numeric KPI items choose one of three explicit directions:

### Higher Is Better

```text
score = actual / target * 100
```

The target must be greater than zero. Results above target are capped at `100`.

### Lower Is Better

An actual at or below target scores `100`. Otherwise:

```text
score = target / actual * 100
```

This supports duration, defect, delay, and similar KPIs without embedding any
KPI-specific business name in the engine.

### Exact Target

An exact match scores `100`; any other result scores `0`. Boolean measurements
always use this behavior.

## Weighting

One item:

```text
item weighted score = item score * item weight / 100
```

One template:

```text
template score = sum of item weighted scores
```

Active item weights must total exactly `100`. Empty templates, inactive input
items, duplicate item IDs, duplicate item names, and invalid totals are
rejected.

One employee:

```text
role weighted score = template score * assignment role weight / 100
employee score = sum of role weighted scores
```

Effective assignment weights must total exactly `100`. Duplicate assignment
IDs or duplicate role templates for one review date are rejected.

Example:

```text
Sales:          90 * 60% = 54.00
Project:        80 * 25% = 20.00
Administration: 95 * 15% = 14.25
Employee score:             88.25
```

## Status

Aggregate ranges come from the active `KPISettings` row. No aggregate threshold
is embedded in a calculation formula.

Versioned item definitions may provide their own item status ranges. The
template, role, and employee aggregate statuses always use the active settings
snapshot supplied to the engine.

The current defaults stored by `KPISettings` are:

- Green: `85.00` through `100.00`
- Yellow: `70.00` through `84.99`
- Red: `0.00` through `69.99`

For values with more than two decimal places, the lower bound determines the
status: at least Green minimum is Green, at least Yellow minimum is Yellow,
and everything else is Red. This avoids a precision gap between displayed
two-decimal boundaries.

## Validation Failures

Calculations fail closed for:

- Missing active KPI settings
- Missing or ambiguous effective template versions
- Missing, unknown, inactive, or unrelated KPI item results
- Unsupported measurement or formula versions
- Non-finite or invalid numeric input
- Scores outside `0` through `100`
- Individual weights outside `0` through `100`
- Template or employee weight totals other than exactly `100`
- Duplicate definitions or assignments
- Assignment dates that do not cover the review date
- Critical Red without a reason and trigger

All validation occurs in the service regardless of any future form or UI
validation.
