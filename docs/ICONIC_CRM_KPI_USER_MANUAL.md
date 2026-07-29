# Iconic CRM KPI User Manual

## 1. Purpose

The KPI system records clear work targets, reviews results, protects approved
history, and shows what needs attention. It does not replace manager judgment.
It does not pay bonuses and is not connected to payroll or sales commission.

## 2. Green, Yellow, And Red

- **Green Light, 85.00-100.00:** target achieved and no unresolved serious
  issue.
- **Yellow Light, 70.00-84.99:** improvement and manager attention are needed.
- **Red Light, 0.00-69.99:** important targets were missed and action is
  required.

A Critical Red event forces the displayed review status to Red. The calculated
score is still stored so the record shows both the formula result and override.

These values are versioned policies. Draft policies do not affect live records.

## 3. KPI Roles

KPI roles are performance templates, not login roles. They do not change what
a user may access in the CRM. The initial draft set covers CEO, Director, North
America Sales Representative, Project Manager, Factory Manager, Merchandiser,
Production Manager, Pattern Master, Sample Development, Quality Control
Inspector, Accounts, Marketing, Administration, Production Support, and
Bangladesh Local Sales.

Each role contains named KPI items, descriptions, targets, measurement
methods, evidence rules, status ranges, manager approval rules, Critical Red
rules, bonus eligibility, and an exact total weight of `100.00`.

## 4. Multiple Roles And Weights

One employee may have one or several KPI roles. Effective role weights must
total exactly `100.00`.

Example:

- Sales Representative: `60.00`
- Project Manager: `25.00`
- Administration: `15.00`

Inactive, archived, future, and expired assignments do not affect the current
period. Old assignments remain in history.

## 5. CEO Setup Process

1. Confirm policy effective date and currency.
2. Create the Stage 10 drafts on an approved non-production database.
3. Review every template and policy.
4. Submit one policy for review.
5. Approve it with written evidence.
6. Publish only after the deployment and policy gates allow publication.
7. Preview each employee assignment.
8. Save assignments as inactive drafts.
9. Confirm manager, dates, bonus eligibility, and exact total weight.
10. Activate with a written reason only after template publication.
11. Review audit records.

The exact administration commands are in
`docs/KPI_STAGE_10_FINAL_INTEGRATION.md`.

## 6. Employee Assignment

The assignment preview shows the employee, role, weight, manager, effective
date, bonus eligibility, and KPI items. Saving creates inactive drafts.
Activation is separate and requires confirmation. The service rejects missing
employees, missing or inactive templates, self-management, inactive managers,
duplicate templates, and totals other than `100.00`.

CEO and Super Admin may approve a managerless assignment when the role policy
allows it. A missing manager does not grant anyone broader access.

## 7. Employee Performance Tab

Open **People > My Performance** for your own record. Authorized managers may
open an employee’s Performance tab from the employee list.

The page is read only. It shows current assignments, stored approved score and
status, weighted roles, manager, review period, template and calculation
versions, and paginated approved history. It does not calculate a new result
when the page opens.

Employees cannot edit reviews, comments, templates, formulas, assignments, or
other employees’ records from this page.

## 8. Manager Review Process

Open **People > Performance Reviews**.

1. Select an assigned employee and review period.
2. Open or create the permitted review.
3. Enter allowed actual values.
4. Add factual manager comments.
5. Record Critical Red evidence only when verified.
6. Save Draft.
7. Review the calculated result supplied by the Calculation Engine.
8. Submit.

Managers cannot change template weights or formulas and cannot approve their
own review.

## 9. Approval Workflow

The sequence is:

`Draft -> Submitted -> Under Review -> Approved -> Locked`

A rejected review returns to Draft and records both actions. No state may be
skipped. Approval and locking require an authorized non-self actor. Approved
and locked reviews are read only.

## 10. Review History

Approved history is an immutable snapshot. It stores assignments, managers,
template/item definitions, values, weights, formula versions, result, Critical
Red state, comments, and approval identity. A SHA-256 digest detects changes.
Historical screens display this stored snapshot and do not recalculate it with
new rules.

## 11. Critical Red

Use Critical Red only for a verified serious failure. Record the trigger,
reason, source KPI, responsible employee, manager comment, corrective action,
and review date. The system keeps the calculated score while forcing final
status Red. Critical Red may block bonus readiness under the published bonus
policy.

## 12. Dashboards

Open `/performance/dashboard/`.

- Employees see only their own approved KPI information.
- Managers see assigned-team summaries and queues.
- Directors see authorized departments.
- HR sees permitted employee performance but not automatic bonus-money access.
- CEO and Super Admin see authorized company summaries.

Widgets load independently and use approved snapshots. Filters narrow current
scope; they cannot expand access.

## 13. Executive Intelligence

Open `/kpi/intelligence/` when authorized. The page shows company health, Red
alerts, Yellow attention, Green success, department/manager/employee analysis,
bonus readiness, completion, trends, location comparison, recommended actions,
and data-quality warnings.

An insight is guidance, not automatic management action. Action links perform
a new server permission check.

## 14. Bonus Readiness

Bonus readiness reads only immutable approved review snapshots. It may be
Eligible, Not Eligible, Pending, or Blocked and includes a reason and rule
version.

It does not create payment, modify payroll, or modify sales commission.
Employees do not see bonus amounts. HR and Accounts access does not
automatically grant bonus-money access.

## 15. Notifications

KPI alerts appear in the existing CRM Notification Center. They may cover due
reviews, overdue work, approval, Critical Red, Yellow attention, improvement,
bonus readiness, manager queues, or executive intelligence.

Open the alert to reach its authorized destination. Marking read or dismissing
does not delete KPI history. Duplicate active alerts are blocked by a stable
key. Stage 10 does not activate live schedules.

## 16. Reports And Exports

Authorized Intelligence reports support PDF, Excel, CSV, and Print. Employees
may export only their own permitted information. Managers are limited to
assigned teams. Directors, HR, CEO, and Super Admin retain their server-side
scope.

Do not add spreadsheet formulas to user-entered text. Export services escape
spreadsheet formula prefixes and HTML. Hidden private fields are excluded.

## 17. Permissions

- **Employee:** own approved performance only.
- **Manager:** assigned employees and manager workflow.
- **Director:** authorized departments and approval workflow.
- **HR:** approved employee scope without automatic bonus-money access.
- **Accounts:** existing finance scope without KPI-edit authority.
- **CEO:** controlled company access and policy administration.
- **Super Admin:** controlled full access.

Hidden buttons are not security. Views and services recheck every protected
record and action.

## 18. Employee Daily Process

1. Open My Performance.
2. Review status and manager comments.
3. Complete assigned work and keep required evidence.
4. Respond through the approved manager workflow.
5. Open only your own KPI notifications.
6. Raise incorrect or missing data to your manager; do not try to edit approved
   history.

## 19. Manager Daily Process

1. Check review queue and KPI notifications.
2. Address overdue and Critical Red items first.
3. Confirm evidence and data quality.
4. Save accurate Draft values and comments.
5. Submit complete reviews on time.
6. Follow improvement actions.
7. Escalate scope or policy problems; do not work around permissions.

## 20. Monthly Process

1. Confirm active employee assignments.
2. Open monthly reviews.
3. Record values and evidence.
4. Submit, approve or reject, then lock approved records.
5. Check missing reviews and data-quality warnings.
6. Review department and company trends.

## 21. Quarterly Process

1. Confirm all monthly reviews are approved and locked.
2. Review quarterly trends and Critical Red history.
3. Create bonus readiness only from approved snapshots.
4. Review rule version, eligibility reason, floor/cap, and currency.
5. Obtain authorized approval outside payroll.

## 22. Annual Process

1. Verify the complete approved history.
2. Review annual employee, manager, department, and company trends.
3. Confirm all policy versions used.
4. Review annual bonus readiness separately from commission and payroll.
5. Retire superseded policies only after a successor is ready.
6. Keep all old policies and snapshots for audit.

## 23. Common Errors

### Weights do not total 100

Correct all active role or KPI-item weights. Do not save an incomplete schedule.

### No current KPI result

Check assignment dates, template publication, review period, approval state,
and missing data warnings. Missing data is not treated as zero.

### Cannot approve

Confirm the review state, your role/scope, and that you are not approving your
own review.

### Notification not created

Confirm a published notification policy exists, the event is enabled, the
recipient is authorized, and deduplication has not already blocked a duplicate.

### Bonus blocked

Check approved review status, Critical Red, minimum score, assignment bonus
eligibility, employee status, and published bonus-rule version. Do not alter
the historical review.

### Export denied

The report may be outside your employee, team, department, HR, or executive
scope. Ask an authorized owner; do not guess record IDs.

## 24. Troubleshooting

Record the page, time, user role, review ID, policy version, expected result,
actual result, and sanitized screenshot. Do not put private employee comments,
bonus values, credentials, or raw report data in normal logs.

For migration or scheduler failures, stop automation, preserve the database,
record the bounded failure, and follow the deployment or rollback plan. Do not
fake migrations or delete history.

## 25. Safety And Privacy

Never share another employee’s KPI data without authorization. Never expose
bonus amounts through alerts or exports. Never change approved snapshots,
payment data, payroll, or commission records to resolve a KPI issue. Never run
policy setup, assignment activation, or automation against production without
separate approval, a verified backup, and a tested rollback.

## Screenshot Status

Stage 11 recorded synthetic desktop and mobile screenshots outside Git at
`/tmp/iconic_kpi_stage11_20260729/screenshots`. They verify automated layout
and scope behavior but do not contain human acceptance. Named UAT testers must
still record redacted screenshots, comments, role, device, and completion time
before deployment approval.

## 26. Release And Support

The KPI platform must remain inactive until the approved template, status,
bonus, intelligence, and notification policy versions are Published. Publishing
policies, activating assignments, installing scheduler entries, enabling
notifications, exposing bonus values, connecting payroll, and deploying are
separate authorized actions.

For support, record the user role, time, page, record identifier, policy and
snapshot versions, expected result, actual result, and a redacted screenshot.
Do not include credentials, private comments, full report rows, evidence files,
or bonus values in normal logs or tickets.
