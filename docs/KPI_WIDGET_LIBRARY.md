# KPI Widget Library

## Contract

All KPI dashboard widgets are registered in
`crm.services.kpi_dashboard.WIDGETS` and built through
`dashboard_widget_payload()`. A payload always includes:

- `slug`
- `kind`
- `audience`
- `generated_at`
- `snapshot_only: true`

The shared partial selects presentation by `kind`. Future dashboard consumers
must extend this registry and partial rather than duplicating aggregate logic.

## Widgets

| Slug | Presentation | Audience | Purpose |
| --- | --- | --- | --- |
| `employee-score` | Gauge and score card | Employee | Current own KPI, completion, comparison, manager, review, and eligibility |
| `role-progress` | Progress bars | Employee | Approved role score, role weight, and weighted result |
| `improvement` | Risk list | Employee | Own Yellow, Red, and Critical Red KPI items |
| `team-summary` | Summary cards | Manager, HR | Average, completion, status distribution, and eligibility counts |
| `executive-summary` | Summary cards | Director, Executive | Department or company KPI |
| `trend` | Lazy canvas charts | All | Monthly, quarterly, and annual stored-snapshot averages |
| `leaderboard` | Ranked lists | Manager, Executive | Top and bottom visible employees |
| `review-queue` | Queue metrics and table | Manager, HR, Executive | Pending, completed, missing, late, upcoming, and workload |
| `department-summary` | Bar chart | Director, HR, Executive | Visible department ranking |
| `location-summary` | Score cards | Executive | Canada and Bangladesh summary |
| `risk` | Critical alert list | Manager, Director, HR, Executive | Red and Critical Red reviews |
| `bonus-summary` | Progress ring and counts | Manager | Stage 6 eligibility states without payout amounts |
| `bonus-forecast` | Forecast cards | Executive | Immutable Stage 6 estimates by stored currency |
| `manager-summary` | Bar chart | Executive | Manager average and review volume |
| `health` | Health cards | Director, Executive | Factory, sales, production, sampling, marketing, quality, and finance readiness |

## Reuse Rules

1. Read approved or locked review snapshots only.
2. Verify snapshot digests before including a result.
3. Never call or duplicate the Stage 4 calculation formulas.
4. Reuse `DashboardFilters` and scoped Stage 5 permission querysets.
5. Return data payloads from services; keep HTML and charts presentational.
6. Register access by audience before exposing a widget URL.
7. Keep bonus amounts executive-only.
8. Cache only per-user, per-audience, per-filter results.
9. Add bounded-query tests for new widgets.
10. Preserve mobile dimensions and empty/error states.

## Loading and Cache

The shell emits widget placeholders. The browser loads a widget only as it
approaches the viewport, then initializes icons and canvases inside that
fragment. A 60-second server cache reduces repeated reads. Browser responses
remain private and `no-store`; the shared cache key contains the user ID.

## Charts

Trend charts use a small local canvas renderer rather than a calculation or
analytics library. It plots values already aggregated by the service. Canvas
labels and points do not derive or change KPI scores.
