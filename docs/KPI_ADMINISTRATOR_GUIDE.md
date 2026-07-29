# KPI Administrator Guide

## Scope

This guide is for authorized CEO and Super Admin operators. It does not grant
permission and does not replace server-side checks.

## Policy Setup

1. Run `prepare_kpi_release` only in the approved environment and only after a
   verified backup.
2. Confirm all 15 role templates are Draft and each version totals `100.00`.
3. Review KPI status, bonus, intelligence, notification, and escalation drafts.
4. Submit each policy for review.
5. Record the independent approval and reason.
6. Publish only the approved version and effective date.
7. Never edit a Published version. Create a successor version.
8. Retire only after the successor is ready. Keep retired history.

Draft policies and inactive KPI settings must not influence live reviews,
bonuses, intelligence, or automation.

## Employee Assignments

1. Select the employee and effective date.
2. Add one or more KPI role templates.
3. Set role weights totaling exactly `100.00`.
4. Select an active manager who is not the employee.
5. Set bonus eligibility independently from performance participation.
6. Preview the effective template items.
7. Save as Draft and obtain confirmation.
8. Activate with actor, date, and reason.

Do not connect KPI roles to login security roles. Deactivate or archive old
assignments; do not delete history.

## Review Administration

The permitted sequence is Draft, Submitted, Under Review, Approved, and
Locked. Rejection returns a review to Draft. Approval must be performed by an
authorized non-self reviewer. Lock only after the immutable approved snapshot
passes its digest check.

Never edit approved snapshot JSON, migration ledger rows, audit records, or
bonus calculations directly.

## Bonus Administration

Bonus calculations read only approved review snapshots. Keep KPI bonus
separate from sales commission, payroll, and payment. Verify rule version,
currency, eligibility reasons, Critical Red behavior, floor, cap, and approval
requirements. Stage 11 does not authorize employee bonus visibility.

## Reports And Notifications

Exports inherit the same employee, team, department, HR, and executive scope
as the page. Do not share exported files beyond that scope. Publish a
notification policy before automation can run, then activate scheduler entries
only under separate approval.

## Audit And Recovery

Record every policy transition, assignment change, review decision, bonus
review, report export, notification action, and automation run. On any
integrity, scope, or snapshot failure, stop the affected action, preserve
evidence, disable only the KPI schedule if active, and follow the production
rollback guide.
