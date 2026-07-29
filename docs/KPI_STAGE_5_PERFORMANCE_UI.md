# KPI Stage 5 Performance UI

## Scope

Stage 5 adds the Employee Performance tab and manager review workflow. It uses
the Stage 4 calculation engine as the only scoring source. No bonus, payroll,
commission, dashboard, notification, public API, or AWS work is included.

## Pages

### Employee Performance

`/employees/<user_id>/performance/` extends the existing employee profile with
a Performance tab. It shows current Stage 3 assignments, the latest stored
approved result, role breakdown, manager, review/version metadata, and paginated
approved or locked history.

Employees can open only their own approved history. Authorized managers,
Directors, HR, CEO, and Super Admin users can inspect employees within their
defined scope. The page never invokes the calculation engine on GET.

### Performance Reviews

`/performance/reviews/` reuses the People styling and provides a paginated,
filterable review queue. Authorized managers can create reviews only for
employees assigned to them on the review date. Directors are department-scoped;
HR is read only; CEO and Super Admin have full non-self access.

`/performance/reviews/<id>/` displays the draft entry form or a read-only review.
Workflow commands are isolated POST routes with CSRF protection and repeat all
authorization checks in the service layer.

## Data Flow

1. Review creation freezes effective assignments, template versions, KPI items,
   weights, ranges, manager, and review date.
2. Managers enter actuals, comments, and optional Critical Red evidence in Draft.
3. Draft save and submit call `KPICalculationEngine`.
4. Director, CEO, or Super Admin review and approve or reject.
5. Approval writes an immutable snapshot and SHA-256 digest.
6. Approved and locked pages display only stored snapshot values.

## Performance

- Employee Performance: 8 warm queries, 6.7 ms measured response
- Review detail: 8 warm queries, 7.4 ms measured response
- Manager queue: 10 warm queries, 8.3 ms measured response
- History page size: 20
- Manager queue page size: 25
- N+1 check: no row-dependent query growth in review/transition rendering

Cold local test-client measurements were 16 queries/57.7 ms, 9 queries/11.7 ms,
and 11 queries/13.8 ms respectively. The cold counts include session, user,
permission, and first-use authorization-cache queries.

## Limits

- Evidence files are not introduced in Stage 5.
- Approved unlock is not introduced; locked reviews remain immutable.
- Employees cannot edit or submit reviews.
- Bonus amounts are neither calculated nor displayed.
- No review route is public or exposed as an API.
