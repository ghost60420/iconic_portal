# KPI Stage 8 Executive Intelligence

## Scope

Stage 8 adds one authenticated Intelligence Center at
`/kpi/intelligence/`. It turns approved KPI evidence into role-scoped health,
alert, trend, performance, readiness, and action summaries. It does not replace
human review, update a KPI score, recalculate an approved review, approve a
bonus, create a payment, or connect payroll.

The page extends the Stage 7 dashboard pattern: a compact shell emits
authorized widget placeholders and each section loads independently from an
authenticated fragment route. The same filters, organization scope, approved
snapshot verification, currency formatting, and audience detection are reused.

## Data Sources

`crm.services.kpi_intelligence` reads only:

1. Digest-verified approved or locked Stage 5 review snapshots.
2. Immutable Stage 6 bonus calculation records.
3. Stage 7 scoped review, employee, filter, and audience services.
4. Existing employee department, manager, and location relationships needed
   to apply authorization and organize summaries.

The Stage 4 engine is never invoked. Approved historical scores, statuses,
roles, item results, comments, template versions, assignment versions, and
formula versions are read from their stored snapshot.

## Intelligence Sections

- Company or authorized-scope health
- Critical Red alerts
- Yellow attention items
- Green success items
- Department performance
- Manager performance and workload
- Employee performance
- Immutable bonus readiness
- Review completion
- Monthly, quarterly, and annual trends
- Canada and Bangladesh comparison for executives
- Recommended actions
- Data-quality warnings

Employees receive a smaller registry containing only their own performance,
trends, attention items, success items, and data-quality warnings. Managers,
Directors, HR, CEO, and Super Admin receive only the widgets allowed for their
existing CRM audience and organizational scope.

## Structured Results

Insights include type, status, severity, title, summary, current and previous
values, change, employee, manager, department, location, review period, source,
recommended action, signed action link, generation time, and intelligence
version. Trend results include current and previous values, difference,
percentage change, direction, status, period count, and an explicit data
quality warning.

Missing data is not converted to zero. Trends with less than the configured
history requirement return `Insufficient History`.

## Performance

The shell is bounded to ten queries and contains no intelligence aggregation.
Widgets use database aggregation and preloaded Stage 7 snapshot rows. Cache
keys include the user, audience, normalized filters, widget, page, rule
generation, and intelligence version. A repeated cached service widget uses
zero database queries. Alert and employee lists are paginated.

Measured on the populated validation copy:

| Measurement | Result |
| --- | --- |
| Cold shell | 6 queries, 56.498 ms |
| Warm shell | 5 queries, 6.763 ms |
| Company-health service widget | 4 queries, 10.029 ms |
| Cached service widget | 0 queries, 0.133 ms |
| Row-driven query growth | None |

## Limits

- There is no rule-settings page. Versioned rules are administered through
  server-side services by CEO or Super Admin only.
- This stage does not send notifications or automate actions.
- Report exports are generated on demand and are not stored.
- Currency values are never combined or converted.
- Production configuration remains unseeded until company policy is approved.
