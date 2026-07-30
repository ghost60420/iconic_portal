# KPI CEO Manual UAT Test Order

## Before Testing

- Open only `https://kpi-staging.100-51-11-48.sslip.io/`.
- Obtain Basic Auth and CRM credentials through the owner-only secure handoff.
- Use staging test accounts and staging test data only.
- Record failures in `docs/KPI_CEO_UAT_BUG_TRACKER.md`.
- Save screenshots without passwords, credential prompts, or private data.
- Do not approve an item based on automated test evidence.

Allowed manual statuses: `NOT TESTED`, `PASS`, `FAIL`, `NEEDS CHANGE`,
`RETEST REQUIRED`, `APPROVED`.

Every test starts as `NOT TESTED`. Only the CEO records the result.

## Test 1: Login and Navigation

| Field | CEO test entry |
| --- | --- |
| Page to open | `/accounts/login/`, then `/performance/dashboard/` |
| User account role | CEO / Super Admin (`kpi_dev_ceo`) |
| Actions to perform | Complete Basic Auth and CRM login. Open every KPI menu item. Confirm the hostname after each navigation. |
| Expected result | Login succeeds; authorized KPI navigation appears; links stay on staging; no broken or production URL opens. |
| Pass | [ ] |
| Fail | [ ] |
| Status | NOT TESTED |
| Notes | |
| Screenshot name | `CEO-UAT-T01-login-navigation.png` |

## Test 2: KPI Policies

| Field | CEO test entry |
| --- | --- |
| Page to open | `/kpi/setup/policies/` |
| User account role | CEO / Super Admin |
| Actions to perform | Review role templates, Green/Yellow/Red policy, bonus policy, intelligence policy, and notification policy. Confirm each is marked `STAGING TEST POLICY`. |
| Expected result | Policies are readable, staging-only, internally consistent, and available only to an authorized role. |
| Pass | [ ] |
| Fail | [ ] |
| Status | NOT TESTED |
| Notes | |
| Screenshot name | `CEO-UAT-T02-kpi-policies.png` |

## Test 3: KPI Assignments

| Field | CEO test entry |
| --- | --- |
| Page to open | `/kpi/setup/assignments/` |
| User account role | CEO / Super Admin |
| Actions to perform | Inspect the single-role employee and multi-role employee. Confirm role weights and try an invalid total using staging data. |
| Expected result | Single-role weight is 100 percent; multi-role weights total 100 percent; invalid totals cannot be treated as valid assignments. |
| Pass | [ ] |
| Fail | [ ] |
| Status | NOT TESTED |
| Notes | |
| Screenshot name | `CEO-UAT-T03-kpi-assignments.png` |

## Test 4: Employee Performance

| Field | CEO test entry |
| --- | --- |
| Page to open | My Performance: `/employees/<user_id>/performance/` |
| User account role | Employee, then multi-role Employee |
| Actions to perform | Sign in as each employee. Review active assignments, KPI values, current period, history, scores, and role breakdown. |
| Expected result | Each employee sees only their own accurate performance; the multi-role breakdown totals 100 percent; private data is not exposed. |
| Pass | [ ] |
| Fail | [ ] |
| Status | NOT TESTED |
| Notes | |
| Screenshot name | `CEO-UAT-T04-employee-performance.png` |

## Test 5: Manager Review Queue

| Field | CEO test entry |
| --- | --- |
| Page to open | `/performance/reviews/` |
| User account role | Manager |
| Actions to perform | Review queue filters, assigned employees, review statuses, due dates, and links to review detail. Attempt to access an out-of-scope review if available. |
| Expected result | Only assigned employees and permitted reviews appear; filters work; out-of-scope access is denied. |
| Pass | [ ] |
| Fail | [ ] |
| Status | NOT TESTED |
| Notes | |
| Screenshot name | `CEO-UAT-T05-manager-review-queue.png` |

## Test 6: Review Submission

| Field | CEO test entry |
| --- | --- |
| Page to open | `/performance/reviews/<review_id>/` |
| User account role | Manager |
| Actions to perform | Open a staging Draft review, enter all KPI results and comments, save the draft, reopen it, and submit it. |
| Expected result | Draft data persists accurately; required validation works; submission occurs once and enters the correct review state/queue. |
| Pass | [ ] |
| Fail | [ ] |
| Status | NOT TESTED |
| Notes | |
| Screenshot name | `CEO-UAT-T06-review-submission.png` |

## Test 7: Review Rejection and Correction

| Field | CEO test entry |
| --- | --- |
| Page to open | `/performance/reviews/<review_id>/` |
| User account role | Director, then Manager |
| Actions to perform | As Director, start and reject the submitted review with a reason. As Manager, confirm the reason, correct the review, save, and resubmit. |
| Expected result | Rejection and reason are recorded once; the Manager can correct the returned review; resubmission returns it to the proper queue. |
| Pass | [ ] |
| Fail | [ ] |
| Status | NOT TESTED |
| Notes | |
| Screenshot name | `CEO-UAT-T07-rejection-correction.png` |

## Test 8: Review Approval and Locking

| Field | CEO test entry |
| --- | --- |
| Page to open | `/performance/reviews/<review_id>/` |
| User account role | Director |
| Actions to perform | Start the resubmitted review, approve it, lock it, reopen it, and inspect workflow history and snapshot verification. |
| Expected result | Approval records the Director and timestamp; locking prevents edits; history is complete; the historical snapshot verifies. |
| Pass | [ ] |
| Fail | [ ] |
| Status | NOT TESTED |
| Notes | |
| Screenshot name | `CEO-UAT-T08-approval-locking.png` |

## Test 9: KPI Dashboard

| Field | CEO test entry |
| --- | --- |
| Page to open | `/performance/dashboard/` |
| User account role | Manager, Director, or HR |
| Actions to perform | Change available period, employee, department, and scope filters. Compare widgets with the completed review. |
| Expected result | Widgets update consistently, show authorized data only, and agree with the review and employee performance pages. |
| Pass | [ ] |
| Fail | [ ] |
| Status | NOT TESTED |
| Notes | |
| Screenshot name | `CEO-UAT-T09-kpi-dashboard.png` |

## Test 10: Executive Dashboard

| Field | CEO test entry |
| --- | --- |
| Page to open | `/performance/dashboard/` |
| User account role | CEO / Super Admin |
| Actions to perform | Review company/department totals, trends, status counts, risk indicators, filters, and empty states. |
| Expected result | Executive summaries are useful, readable, internally consistent, and update to the selected scope without exposing invalid records. |
| Pass | [ ] |
| Fail | [ ] |
| Status | NOT TESTED |
| Notes | |
| Screenshot name | `CEO-UAT-T10-executive-dashboard.png` |

## Test 11: Executive Intelligence

| Field | CEO test entry |
| --- | --- |
| Page to open | `/kpi/intelligence/` |
| User account role | CEO / Super Admin |
| Actions to perform | Review risk, trend, performance, and action indicators. Change filters and compare intelligence with the locked review and dashboard. |
| Expected result | Intelligence is understandable, scoped correctly, consistent with source evidence, and updated once for the locked review. |
| Pass | [ ] |
| Fail | [ ] |
| Status | NOT TESTED |
| Notes | |
| Screenshot name | `CEO-UAT-T11-executive-intelligence.png` |

## Test 12: Bonus Readiness

| Field | CEO test entry |
| --- | --- |
| Page to open | Bonus Readiness in `/kpi/intelligence/` |
| User account role | CEO / Super Admin; use HR or Accounts to check privacy |
| Actions to perform | Compare approved/locked and incomplete reviews against staging bonus policy thresholds. Check access using a restricted role. |
| Expected result | Readiness and reason match approved staging policy; incomplete reviews are not ready; private bonus values remain restricted. |
| Pass | [ ] |
| Fail | [ ] |
| Status | NOT TESTED |
| Notes | |
| Screenshot name | `CEO-UAT-T12-bonus-readiness.png` |

## Test 13: Notifications

| Field | CEO test entry |
| --- | --- |
| Page to open | `/notifications/` |
| User account role | CEO, Manager, Employee, and Director as applicable |
| Actions to perform | Inspect notifications created by submit, reject, resubmit, approve, and lock. Open links and test read/unread behavior where available. |
| Expected result | Each intended CRM-only notification appears once for the correct user and opens an authorized page; no external message is sent. |
| Pass | [ ] |
| Fail | [ ] |
| Status | NOT TESTED |
| Notes | |
| Screenshot name | `CEO-UAT-T13-notifications.png` |

## Test 14: PDF, Excel, CSV, and Print Reports

| Field | CEO test entry |
| --- | --- |
| Page to open | `/kpi/intelligence/` and `/kpi/intelligence/reports/<report>/<format>/` |
| User account role | CEO / Super Admin |
| Actions to perform | Select a report and filters. Export PDF, Excel, and CSV, then open Print view. Inspect every output and compare values with the on-screen report. |
| Expected result | All outputs open successfully, remain readable, preserve filters and accurate values, and include only authorized data. |
| Pass | [ ] |
| Fail | [ ] |
| Status | NOT TESTED |
| Notes | |
| Screenshot name | `CEO-UAT-T14-reports-exports.png` |

## Test 15: Mobile and Tablet Views

| Field | CEO test entry |
| --- | --- |
| Page to open | Dashboard, My Performance, Review Queue, review detail, Intelligence, and reports |
| User account role | CEO, Manager, Employee, and Director as applicable |
| Actions to perform | Test phone and tablet portrait/landscape layouts. Use navigation, filters, tables, review actions, and exports. |
| Expected result | Content does not overlap or clip; no unintended horizontal scrolling appears; controls remain readable, touch-friendly, and functional. |
| Pass | [ ] |
| Fail | [ ] |
| Status | NOT TESTED |
| Notes | |
| Screenshot name | `CEO-UAT-T15-mobile-tablet.png` |

## Usability Observations

Record feedback without treating it as an approved redesign.

| Area | Status | Notes / bug ID |
| --- | --- | --- |
| Navigation | NOT TESTED | |
| Page layout | NOT TESTED | |
| Green, Yellow, and Red visibility | NOT TESTED | |
| Score readability | NOT TESTED | |
| Button placement | NOT TESTED | |
| Mobile layout | NOT TESTED | |
| Dashboard usefulness | NOT TESTED | |
| Report clarity | NOT TESTED | |
| Review workflow clarity | NOT TESTED | |
| Bonus readiness clarity | NOT TESTED | |
| Intelligence alerts | NOT TESTED | |
| Overall speed | NOT TESTED | |

## Completion Rule

Do not prepare the production deployment package until the CEO has completed the
manual checklist, all blocking bugs are closed or explicitly deferred, and
written CEO approval is recorded. Preparing the package does not authorize a
production deployment.
