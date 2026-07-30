# KPI Private Staging Test Report

## Result

`READY FOR CEO STAGING UAT`

Actual target:

`https://kpi-staging.100-51-11-48.sslip.io/`

## Automated Tests

- KPI-focused tests: 218 passed
- Full CRM regression: 981 passed
- Deployment check: passed
- Python compilation: passed
- Migration check: passed at `crm.0198_kpi_release_governance`
- SQLite integrity: passed
- SQLite foreign keys: passed
- Bounded query and N+1 tests: passed

## Browser Workflow

Real Chrome connected through HTTPS, HTTP Basic Auth, and CRM login.

| Check | Result |
| --- | --- |
| Seven role logins | PASS |
| Permission-adaptive navigation | PASS |
| Single-role 100 percent assignment | PASS |
| Multi-role 60/25/15 assignment | PASS |
| Open review | PASS |
| Enter and save results | PASS |
| Submit | PASS |
| Start review and reject | PASS |
| Correct and resubmit | PASS |
| Approve and lock | PASS |
| Historical snapshot verification | PASS |
| Dashboard update | PASS |
| Intelligence update | PASS |
| Bonus readiness | PASS |
| CRM notification | PASS |
| PDF / XLSX / CSV / Print | PASS |
| Desktop / tablet / mobile | PASS |

The only browser console `403` was the intentional Accounts request to KPI
setup. There were no unexpected console errors.

## Regression

Each route was exercised through the actual staging URL with an authenticated
browser. Main create/edit/filter/workflow behavior is covered by the 981-test
full CRM suite; results are not based on page load alone.

| Module | Browser | Main action regression | Result |
| --- | ---: | --- | --- |
| Main Dashboard | 200 | dashboard data and navigation | PASS |
| Employees | 200 | profile, permissions, performance | PASS |
| Leads | 200 | search, create/edit, ownership workflow | PASS |
| Opportunities | 200 | create/edit and stage workflow | PASS |
| Accounts | 200 | ledger/payment audit behavior | PASS |
| Sampling | 200 | sampling filter and production actions | PASS |
| Production | 200 | order and status workflow | PASS |
| Quotations | 200 | costing and approval workflow | PASS |
| Invoices | 200 | invoice/payment workflow | PASS |
| Payments | 200 | payment audit and deletion controls | PASS |
| Payroll | 200 | staff-month workflow | PASS |
| Commissions | 200 | salesperson profile and calculations | PASS |
| Marketing | 200 | local dashboard; provider actions disabled | PASS |
| FedEx | 200 | shipment/tracking workflow tests | PASS |
| Notifications | 200 | CRM notification visibility/actions | PASS |
| File uploads | 200 | upload validation/storage tests | PASS |

## Performance

Query counts were measured under staging settings against the staging database.
Response times below are real HTTPS timings. First request followed a cache
clear; warm is the immediate second request.

| Page | Cold / warm queries | First / warm HTTPS | Cache | N+1 | Timeout |
| --- | ---: | ---: | --- | --- | --- |
| KPI Dashboard | 6 / 5 | 104.38 / 100.08 ms | PASS | PASS | No |
| Employee Performance | 14 / 8 | 110.45 / 103.26 ms | PASS | PASS | No |
| Review Queue | 11 / 10 | 115.40 / 113.89 ms | PASS | PASS | No |
| Executive Dashboard | 6 / 5 | 243.46 / 97.30 ms | PASS | PASS | No |
| Intelligence Center | 6 / 5 | 98.94 / 99.89 ms | PASS | PASS | No |
| Notification Center | 7 / 5 | 101.17 / 100.85 ms | PASS | PASS | No |
| Reports | 18 / 6 | 157.19 / 89.69 ms | PASS | PASS | No |

The staging infrastructure changed no KPI query logic. Before-deployment local
final counts and after-deployment staging counts are unchanged: Dashboard 6/5,
Employee Performance 14/8, Review Queue 11/10, Executive Dashboard 6/5,
Intelligence 6/5, and Notifications 7/5.

## Security

- Production code and database were not used for staging.
- Staging database, settings, secret, service, static, media, logs, and backups
  are separate.
- DEBUG is false.
- Allowed hosts and trusted origins contain only the staging hostname.
- Secure cookies and HTTPS redirect are enabled.
- Valid HTTPS and HTTP Basic Auth are active.
- Only seven sanitized UAT users are active.
- External provider flags and credentials are disabled.
- Email uses Django's in-memory backend.
- No staging cron, KPI timer, or background worker exists.
- No external/customer notification was sent.
- Server permissions enforce manager, employee, HR, bonus, setup, and export
  scope.
- No secret or SQLite database is tracked in Git.
- Staging actual `5xx` count is zero.

## Evidence

Owner-only evidence:

`/Users/hossain/CRM Production Backups/kpi-private-staging-20260730T005500Z`

It contains browser JSON, performance JSON, exports, checksums, the UAT
automation script, and desktop/tablet/mobile screenshots. Credential files in
that directory are mode `0600` and must not be attached to bug reports.
