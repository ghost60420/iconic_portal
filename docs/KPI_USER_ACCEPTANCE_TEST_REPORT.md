# KPI User Acceptance Test Report

## Status

Human UAT is `NOT TESTED`. Automated permission and workflow tests cannot
replace named business-user acceptance.

Stage 11 completed an automated Playwright rehearsal with synthetic users on
an isolated database. It is supporting evidence only, not human acceptance.
Desktop scope passed for Employee, Manager, Director, HR, Accounts, CEO, and
Super Admin. Mobile rendering passed for Employee, Manager, and CEO at
390 x 844 with no document overflow.

## Test Data

Use only an isolated UAT database containing:

1. One employee with one `100.00` role.
2. One employee with three roles totaling `100.00`.
3. One assigned manager.
4. One Director scoped to the test department.
5. One HR user without bonus-money authority.
6. One Accounts user without KPI-edit authority.
7. One CEO.
8. One Super Admin.

Do not use real private notes, bonus values, or production credentials.

## Role Results

| Role | Human result | Automated browser result | Required human evidence |
| --- | --- | --- | --- |
| Employee, one role | NOT TESTED | PASS | Own-only Performance, history, notification, protected link |
| Employee, three roles | NOT TESTED | Automated assignment/regression tests only | Named user and browser evidence |
| Manager | NOT TESTED | PASS | Assigned-team queue, save, submit, comments, no approval |
| Director | NOT TESTED | PASS | Department scope, approve/reject/lock, no unrelated team |
| HR | NOT TESTED | PASS | Approved employee scope, no bonus money |
| Accounts | NOT TESTED | PASS | Existing finance access, no KPI policy/edit/bonus authority |
| CEO | NOT TESTED | PASS | Full authorized dashboards, reports, policy workflow |
| Super Admin | NOT TESTED | PASS | Full controlled access and audit visibility |

## Automated Evidence

The isolated Stage 11 lifecycle passed role assignment, Draft save, submit,
review, rejection, correction, resubmission, approval, locking, immutable
snapshot verification, bonus eligibility, CRM notification generation,
dashboard and Intelligence loading, PDF/Excel/CSV/print exports, and assignment
archival with history preserved.

Employee and Accounts requests for unrelated records and executive exports
returned `403`. Manager and Director unrelated-scope requests returned `403`.
Authorized executive exports returned the expected content types.

Redacted synthetic screenshots are stored outside the repository at
`/tmp/iconic_kpi_stage11_20260729/screenshots`. No screenshot, synthetic
database, password manifest, or private record was committed.

## Human Evidence Record

For each named tester record:

| Field | Value |
| --- | --- |
| Tester | |
| Role | |
| Date and completion time | |
| Browser and device | |
| Approved UAT database | |
| Result | NOT TESTED |
| Comments | |
| Redacted screenshot references | |
| Defect references | |

## Workflow Script

For each applicable role:

1. Sign in with the named UAT account.
2. Preview and save employee KPI assignments.
3. Confirm exact `100.00` total and item preview.
4. Publish only the isolated UAT template after approval.
5. Activate the assignment with a written reason.
6. Create and open a review.
7. Enter permitted KPI values and comments.
8. Save and submit.
9. Approve or reject with the authorized non-self actor.
10. Lock an approved review.
11. Verify immutable history and versions.
12. Create bonus readiness from the approved snapshot.
13. Open employee, manager, and authorized executive dashboards.
14. Open Executive Intelligence and generate permitted reports.
15. Run CRM-only notification automation against the test date.
16. Open the notification action and confirm the destination reauthorizes.
17. Try an unrelated employee, team, export, and guessed ID and confirm denial.

## Evidence Template

Record tester, user role, date/time, database copy, browser/device, steps,
expected result, actual result, screenshot path, defect ID, and PASS/FAIL.
Private employee values must be redacted from shared evidence.

## Exit Rule

Every role and workflow must pass without a Critical or High defect. Any
permission exposure, historical mutation, bonus exposure, or protected-module
regression blocks deployment.

Human UAT remains a release blocker. Synthetic automation cannot change a
Human result from `NOT TESTED` to PASS.
