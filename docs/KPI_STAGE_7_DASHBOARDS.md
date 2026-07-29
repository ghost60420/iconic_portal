# KPI Stage 7 Dashboards

## Scope

Stage 7 adds one adaptive, read-only KPI dashboard at
`/performance/dashboard/`. The same route selects an employee, manager,
director, HR, or executive widget set from existing CRM roles and Stage 5
review visibility rules. It adds no KPI editing, approval, bonus calculation,
export generation, public API, or database model.

The dashboard reads only digest-verified Stage 5 approved or locked snapshots.
It does not call the Stage 4 calculation engine, read live KPI item values to
derive scores, or change Stages 1 through 6.

## Page Decision

The Stage 5 Performance tab is an employee review-history surface, while the
existing CRM dashboards use operational data and different authorization
scopes. Mixing aggregate KPI access into either screen would couple review
permissions to protected operational modules. One adaptive KPI dashboard and
one shared widget endpoint provide the required role views without parallel
employee, manager, director, and CEO pages.

## Data Flow

1. `DashboardFilters` parses and normalizes every supported filter.
2. Existing Stage 5 permission services produce the visible employee and
   review querysets.
3. The dashboard limits score data to approved or locked reviews.
4. Every stored review snapshot digest is verified before use.
5. Widget services aggregate stored scores and statuses without recalculation.
6. Stage 6 immutable bonus results provide eligibility and executive forecast
   values where authorized.
7. Each widget is returned independently and privately cached for 60 seconds.

Invalid or tampered snapshots are excluded and counted, never recalculated.

## Role Views

- Employee: own score, assigned roles, completion, status, manager, trend,
  comparison, bonus eligibility state, and improvement areas.
- Manager: assigned-team summary, queue, ranking, trends, risk, workload,
  completion, and bonus eligibility summary.
- Director: department summary, ranking, trends, risks, and operational KPI
  health.
- HR: company review summary, queue, department ranking, trends, and risk.
- CEO and Super Admin: company and location KPI, department and employee
  rankings, Critical Red, immutable bonus forecast, trends, completion,
  manager performance, and operational KPI health.

No dashboard role grants a new permission. Manager access remains restricted to
employees assigned through the existing KPI review visibility service.

## Filters

The shared filter object supports employee, KPI role, department, configured
Canada/Bangladesh team group, review period, month, quarter, year, manager, and
stored KPI status. Invalid values fail closed to an unfiltered valid option or
the current year. Queryset filtering is centralized and reused by every widget.

## Responsive Behavior

The dashboard uses stable responsive grid tracks for desktop, tablet, and
mobile. Wide and full-width widgets collapse to a single column on narrow
screens. Tables use labelled mobile rows, chart canvases retain fixed minimum
dimensions, and long labels wrap without horizontal page overflow.

## Performance

- The shell loads filter metadata and widget placeholders only.
- `IntersectionObserver` lazy-loads every widget independently.
- Widget cache keys include user, audience, widget, and normalized filters.
- Review and employee querysets use the existing scoped services plus related
  object loading.
- Warm shell: 7 queries, 5.738 ms.
- Cold shell: 14 queries, 607.949 ms, including first-request session and
  shared navigation permission caches.
- Tested widget query ceiling: 5.
- Cached repeat widget request: 0 queries.
- N+1 result: not detected.

The dashboard meets the project warm dashboard budget of at most 10 queries.
Cold global-navigation behavior was measured but not changed because it is
outside Stage 7 and protected by existing CRM permissions.

## Export Interface

PDF, Excel, CSV, and print capabilities are advertised through an authenticated
metadata endpoint with `generation_enabled: false`. Controls are disabled.
No export file, employee data response, background job, or export engine is
implemented in Stage 7.

## Security

- Login is required for the shell, widget, and capability routes.
- Direct widget access is checked against the caller's role-specific registry.
- Querysets are scoped on the server; hidden UI is not the security boundary.
- Employee cache keys and querysets cannot cross employee identities.
- Bonus amounts are limited to the executive-only forecast widget.
- Responses use private, no-store browser cache headers.
- No write method, model mutation, public route, or permission change exists.

## Known Limits

- Export generation belongs to Stage 8.
- Operational health can show an empty state when approved snapshots do not
  contain a matching department or KPI role.
- Attendance impact is shown only when it already exists in an immutable Stage
  6 bonus result.
- Stage 7 does not add a link to protected pre-Stage-7 navigation or pages; the
  dashboard route is ready for an approved navigation change.
