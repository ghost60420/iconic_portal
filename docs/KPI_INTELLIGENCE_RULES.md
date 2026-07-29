# KPI Intelligence Rules

## Versioned Policy

`KPIIntelligenceRuleSet` stores configurable intelligence policy separately
from KPI calculation and bonus formulas. Each row has a stable code, positive
version, draft/published/retired state, effective dates, creator, publisher,
and publication timestamp.

Published rules are immutable and cannot be deleted. A future policy change
requires a new version. CEO or Super Admin publication is enforced in the
service and recorded in the existing CRM audit log.

## Configurable Values

- Minimum score
- Company-health Green threshold
- Critical Red alert count
- Decline percentage
- Trend history periods
- Overdue days
- Review completion target
- Improvement threshold
- Department risk threshold
- Manager workload threshold
- Critical, Red, and Yellow alert severities

Company health also has configurable weights for approved overall KPI, review
completion, immutable bonus readiness, improvement, and risk control. These
five component weights must total exactly `100.00`.

Database and model validation enforce the total. Other percentage values must
remain in `0.00-100.00`, trend periods in `2-24`, and effective end cannot
precede effective start.

## Evaluation

The effective published rule for the requested review date is loaded before an
intelligence widget is built. Missing or ambiguous effective rules stop the
calculation rather than silently applying a hard-coded policy.

Health and alerts are intelligence summaries only. They never overwrite the
stored KPI score or Green/Yellow/Red status in an approved Stage 5 snapshot.
Critical Red preserves the source score while elevating attention and action
severity.

## Cache Invalidation

Publishing a rule increments the intelligence rule generation key. Widget
cache keys include that generation so older cached policy results are no
longer read after publication.

Stage 8 has no settings page and seeds no company policy. A published rule must
be created through an approved administrative process before the center is
enabled outside development.
