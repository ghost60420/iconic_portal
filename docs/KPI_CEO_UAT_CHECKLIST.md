# KPI CEO Development UAT Checklist

## UAT Control

- Environment: `https://kpi-staging.100-51-11-48.sslip.io/`
- Purpose: usability, workflow validation, and business approval
- Scope: completed KPI integration only
- Production deployment: prohibited until written CEO approval
- Production changes: prohibited
- New features: prohibited during UAT unless a verified Critical defect cannot
  be resolved without one and the CEO explicitly approves the scope
- Bug tracker: `docs/KPI_CEO_UAT_BUG_TRACKER.md`

Use only staging test accounts and staging test data. Mark exactly one of Pass or
Fail for every test. Record a bug ID in Notes for every failure.

## Test Roles

| Role | Username |
| --- | --- |
| CEO / Super Admin | `kpi_dev_ceo` |
| Manager | `kpi_dev_manager` |
| Employee, one KPI role | `kpi_dev_employee` |
| Employee, several KPI roles | `kpi_dev_multi` |
| Director | `kpi_dev_director` |
| HR | `kpi_dev_hr` |
| Accounts | `kpi_dev_accounts` |

Passwords are distributed only through the existing secure internal method.

## 1. Dashboard

| What to test | Expected result | Pass | Fail | Notes |
| --- | --- | :---: | :---: | --- |
| Open the KPI Dashboard as CEO, Manager, Director, HR, and Employee. | The page loads without an error and shows only data permitted for the signed-in role. | [ ] | [ ] | |
| Change available period, employee, department, or scope filters. | Widgets refresh consistently and the selected filters remain visible. | [ ] | [ ] | |
| Compare headline totals with the underlying employee/review records. | Counts, statuses, scores, and trends agree with the source records. | [ ] | [ ] | |
| Refresh the page after changing filters. | The dashboard remains stable with no duplicate or missing widgets. | [ ] | [ ] | |

## 2. Employee Performance

| What to test | Expected result | Pass | Fail | Notes |
| --- | --- | :---: | :---: | --- |
| Open My Performance as the single-role employee. | The assigned role, 100 percent weight, KPIs, current review, and history are correct. | [ ] | [ ] | |
| Open My Performance as the multi-role employee. | All assigned roles appear and weights total exactly 100 percent. | [ ] | [ ] | |
| Inspect current and historical periods. | Scores and snapshots remain tied to the correct review period. | [ ] | [ ] | |
| Attempt to open another employee outside the user's scope. | Access is denied or safely redirected without exposing private KPI or bonus data. | [ ] | [ ] | |

## 3. KPI Assignment

| What to test | Expected result | Pass | Fail | Notes |
| --- | --- | :---: | :---: | --- |
| Open KPI Assignments as CEO / Super Admin. | The page loads with employee, role, weight, effective period, and assignment controls. | [ ] | [ ] | |
| Review the single-role assignment. | One active role totals 100 percent. | [ ] | [ ] | |
| Review the multi-role assignment. | All active role weights total 100 percent with no overlap or missing allocation. | [ ] | [ ] | |
| Try an invalid total in staging. | The assignment cannot be published or saved as valid until the total is 100 percent. | [ ] | [ ] | |
| Open KPI Assignments as an unauthorized role. | The menu is hidden and direct URL access returns `403` or an equivalent denial. | [ ] | [ ] | |

## 4. KPI Review

| What to test | Expected result | Pass | Fail | Notes |
| --- | --- | :---: | :---: | --- |
| Open a new staging review for an assigned employee and period. | The review uses the correct employee, assignment, policy, KPIs, and review dates. | [ ] | [ ] | |
| Enter KPI results and comments, then save a draft. | Values persist accurately and the review remains Draft. | [ ] | [ ] | |
| Submit the completed review. | Required-field validation passes, status changes correctly, and the review enters the right queue. | [ ] | [ ] | |
| Reopen the submitted review. | Submitted values, totals, comments, and audit history are unchanged. | [ ] | [ ] | |

## 5. Manager Approval

| What to test | Expected result | Pass | Fail | Notes |
| --- | --- | :---: | :---: | --- |
| Sign in as Manager and open Review Queue. | Only assigned employees and permitted reviews are visible. | [ ] | [ ] | |
| Complete manager-owned review actions. | Actions are available only at valid workflow states and require the expected inputs. | [ ] | [ ] | |
| Submit or resubmit a corrected review. | The review advances once, records the manager and timestamp, and creates no duplicate transition. | [ ] | [ ] | |
| Try to act on an employee outside manager scope. | The action is denied and no review data changes. | [ ] | [ ] | |

## 6. Director Approval

| What to test | Expected result | Pass | Fail | Notes |
| --- | --- | :---: | :---: | --- |
| Sign in as Director and open Review Queue. | Only the Director's authorized department scope is visible. | [ ] | [ ] | |
| Start a submitted review. | Status and ownership update correctly and the transition is recorded once. | [ ] | [ ] | |
| Reject with a clear reason. | The review returns to the correct correction state and the reason is visible to the manager. | [ ] | [ ] | |
| Approve a corrected resubmission. | Approval records the Director, timestamp, comments, and final values. | [ ] | [ ] | |
| Lock the approved review. | The review becomes immutable and its historical snapshot verifies successfully. | [ ] | [ ] | |

## 7. HR Review

| What to test | Expected result | Pass | Fail | Notes |
| --- | --- | :---: | :---: | --- |
| Sign in as HR and open permitted performance and review pages. | HR sees only the authorized employee/review scope. | [ ] | [ ] | |
| Inspect review history and audit information. | Workflow history is complete, ordered, and read-only where required. | [ ] | [ ] | |
| Inspect sensitive compensation information. | Bonus details follow HR privacy policy and are not exposed outside permitted scope. | [ ] | [ ] | |
| Attempt a Director-only action. | The action is unavailable or denied without changing the review. | [ ] | [ ] | |

## 8. Bonus Readiness

| What to test | Expected result | Pass | Fail | Notes |
| --- | --- | :---: | :---: | --- |
| Open Bonus Readiness after a review is approved and locked. | The employee appears with the correct eligibility/readiness result. | [ ] | [ ] | |
| Compare readiness with KPI policy thresholds and final score. | The result and reason match the published staging policy. | [ ] | [ ] | |
| Inspect an incomplete, rejected, or unlocked review. | It is clearly not ready and cannot be mistaken for an approved bonus. | [ ] | [ ] | |
| Sign in with roles that lack bonus permission. | Private bonus values remain hidden or access is denied. | [ ] | [ ] | |

## 9. Executive Dashboard

| What to test | Expected result | Pass | Fail | Notes |
| --- | --- | :---: | :---: | --- |
| Open the dashboard as CEO / Super Admin. | Company and department summaries, trends, review status, and risk indicators load. | [ ] | [ ] | |
| Compare department and company totals with underlying reviews. | Aggregates are internally consistent and exclude unauthorized or invalid records. | [ ] | [ ] | |
| Change period and department filters. | Every executive widget updates to the same selected scope. | [ ] | [ ] | |
| Inspect empty or incomplete data states. | The dashboard explains the state without showing misleading zeros or errors. | [ ] | [ ] | |

## 10. Executive Intelligence

| What to test | Expected result | Pass | Fail | Notes |
| --- | --- | :---: | :---: | --- |
| Open Intelligence Center as CEO and Director. | Insights are restricted by role and derived from the selected KPI scope. | [ ] | [ ] | |
| Review risk, trend, performance, and action indicators. | Each indicator is understandable and agrees with dashboard/review evidence. | [ ] | [ ] | |
| Change filters and refresh intelligence widgets. | Results update consistently without stale or cross-scope data. | [ ] | [ ] | |
| Inspect intelligence after the test review is locked. | The new result is included once and the prior history remains stable. | [ ] | [ ] | |

## 11. KPI Reports

| What to test | Expected result | Pass | Fail | Notes |
| --- | --- | :---: | :---: | --- |
| Select each available KPI report and authorized scope. | The report loads with the correct title, filters, period, rows, and totals. | [ ] | [ ] | |
| Compare report rows with dashboard and review detail. | Scores, statuses, employees, departments, and totals agree. | [ ] | [ ] | |
| Test a report with no matching records. | A clear empty state appears and export behavior remains safe. | [ ] | [ ] | |
| Attempt to report outside the signed-in role's scope. | Unauthorized rows and private bonus values are not returned. | [ ] | [ ] | |

## 12. PDF Export

| What to test | Expected result | Pass | Fail | Notes |
| --- | --- | :---: | :---: | --- |
| Export an authorized KPI report as PDF. | A valid PDF downloads with the selected filters and current report data. | [ ] | [ ] | |
| Open and inspect every PDF page. | Titles, columns, page breaks, totals, and dates are readable and not clipped. | [ ] | [ ] | |
| Export as a restricted role. | The PDF contains only data authorized for that role. | [ ] | [ ] | |

## 13. Excel Export

| What to test | Expected result | Pass | Fail | Notes |
| --- | --- | :---: | :---: | --- |
| Export an authorized KPI report as Excel. | A valid `.xlsx` workbook downloads and opens without repair warnings. | [ ] | [ ] | |
| Check headers, values, dates, numeric fields, and totals. | Cells contain accurate typed values suitable for sorting and analysis. | [ ] | [ ] | |
| Export as a restricted role. | The workbook contains only permitted rows and fields. | [ ] | [ ] | |

## 14. CSV Export

| What to test | Expected result | Pass | Fail | Notes |
| --- | --- | :---: | :---: | --- |
| Export an authorized KPI report as CSV. | A valid CSV downloads with the selected filters and report data. | [ ] | [ ] | |
| Open the CSV in a text editor and spreadsheet tool. | Encoding, headers, delimiters, dates, values, and line breaks are correct. | [ ] | [ ] | |
| Check comments or text beginning with spreadsheet formula characters. | Exported text cannot execute an unintended spreadsheet formula. | [ ] | [ ] | |
| Export as a restricted role. | The CSV contains only authorized data. | [ ] | [ ] | |

## 15. Notifications

| What to test | Expected result | Pass | Fail | Notes |
| --- | --- | :---: | :---: | --- |
| Complete submit, reject, resubmit, approve, and lock actions. | Each expected CRM-only KPI notification appears once for the correct recipient. | [ ] | [ ] | |
| Open a KPI notification. | It links to an authorized, relevant KPI page or review. | [ ] | [ ] | |
| Mark notifications read/unread where supported. | State changes persist and counters update correctly. | [ ] | [ ] | |
| Confirm external delivery behavior. | No email, SMS, WhatsApp, webhook, or customer-facing message is sent. | [ ] | [ ] | |

## 16. Mobile Layout

| What to test | Expected result | Pass | Fail | Notes |
| --- | --- | :---: | :---: | --- |
| Test key KPI pages at a mobile viewport and on an available phone. | Content fits without overlap, clipped controls, or unintended horizontal scrolling. | [ ] | [ ] | |
| Open navigation, filters, tables, forms, and review actions. | Controls remain readable, reachable, and usable by touch. | [ ] | [ ] | |
| Save and submit a review from mobile where the role permits it. | Validation and workflow behavior match desktop. | [ ] | [ ] | |

## 17. Tablet Layout

| What to test | Expected result | Pass | Fail | Notes |
| --- | --- | :---: | :---: | --- |
| Test key KPI pages in portrait and landscape tablet layouts. | Layout adapts without overlap, clipping, or lost actions. | [ ] | [ ] | |
| Use navigation, dashboard filters, review queue, and review detail. | All primary controls remain visible and touch-friendly. | [ ] | [ ] | |
| Rotate the device or change orientation. | The page reflows cleanly without losing entered data or filter state. | [ ] | [ ] | |

## 18. Desktop Layout

| What to test | Expected result | Pass | Fail | Notes |
| --- | --- | :---: | :---: | --- |
| Test KPI pages at standard laptop and wide desktop sizes. | Content uses available space without excessive gaps, clipping, or overlap. | [ ] | [ ] | |
| Inspect dense tables, filters, charts, modals, and action menus. | Data is scannable and actions remain aligned and predictable. | [ ] | [ ] | |
| Use browser zoom at 80, 100, 125, and 150 percent. | Core content and actions remain usable without text collision. | [ ] | [ ] | |

## 19. Role Permissions

| What to test | Expected result | Pass | Fail | Notes |
| --- | --- | :---: | :---: | --- |
| Repeat the role matrix with CEO, Manager, Employee, multi-role Employee, Director, HR, and Accounts. | Each role sees only the intended KPI menus, records, fields, and actions. | [ ] | [ ] | |
| Open hidden routes directly for each unauthorized role. | Server-side permission checks deny access; hiding a menu is not the only control. | [ ] | [ ] | |
| Test manager employee scope and Director department scope. | Records outside the assigned scope are not listed, exported, or accessible by URL. | [ ] | [ ] | |
| Test bonus and export privacy. | Sensitive values and rows remain restricted in pages and downloaded files. | [ ] | [ ] | |

## 20. Navigation

| What to test | Expected result | Pass | Fail | Notes |
| --- | --- | :---: | :---: | --- |
| Check My Performance, Dashboard, Review Queue, Intelligence, Bonus Readiness, Reports, Notifications, Assignments, and Policies. | Authorized menu items appear with clear labels and open the correct page. | [ ] | [ ] | |
| Check the same menu as every test role. | Unauthorized items are omitted while required daily-work items remain available. | [ ] | [ ] | |
| Use breadcrumbs, back navigation, links from notifications, and links from dashboard widgets. | Navigation preserves context and does not lead to broken, forbidden, or production URLs. | [ ] | [ ] | |
| Confirm the hostname throughout testing. | Every internal KPI navigation remains on the protected staging hostname. | [ ] | [ ] | |

## Final UAT Review

| Gate | Pass | Fail | Notes |
| --- | :---: | :---: | --- |
| All checklist rows have a result and evidence where needed. | [ ] | [ ] | |
| Every failed item has a bug tracker ID. | [ ] | [ ] | |
| All Critical and High bugs are closed and retested. | [ ] | [ ] | |
| Accepted Medium and Low deferrals are documented. | [ ] | [ ] | |
| No external provider or recurring automation was activated. | [ ] | [ ] | |
| Production remained untouched throughout UAT. | [ ] | [ ] | |
| Production deployment package is ready for review only. | [ ] | [ ] | |

## CEO Approval

- Overall UAT result: [ ] APPROVED  [ ] NOT APPROVED
- CEO name:
- Approval date:
- Approved bug deferrals:
- Production deployment authorized: [ ] YES  [ ] NO
- Notes:

Production deployment must remain blocked unless both `APPROVED` and
`Production deployment authorized: YES` are explicitly recorded by the CEO.
