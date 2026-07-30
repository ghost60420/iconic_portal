# KPI CEO UAT Bug Tracker

## Control Rules

- Log only verified issues found on the KPI staging environment.
- Do not develop new UAT features.
- Fix only verified defects that block or impair approved KPI behavior.
- Reproduce every issue before changing code.
- Apply and verify every fix in staging only.
- Do not deploy, merge, migrate, restart, or modify production.
- Link each failed checklist item to a bug ID.
- Do not include passwords, access-gate credentials, customer private data, or
  unmasked employee data in this tracker or its screenshots.

## Severity

| Severity | Definition |
| --- | --- |
| Critical | Staging is unavailable, data/security is compromised, or KPI UAT cannot continue. |
| High | A required KPI workflow or approval is blocked with no acceptable workaround. |
| Medium | The workflow completes but has a material correctness or usability defect. |
| Low | Minor visual, wording, or usability defect with a clear workaround. |

Severity is based on business impact, not implementation effort. A requested
enhancement is not a bug and must be recorded separately for post-UAT review.

## Status

Use only these status values:

| Status | Meaning |
| --- | --- |
| New | Reported but not yet reproduced. |
| Confirmed | Reproduced in staging with evidence. |
| In Progress | An approved staging-only fix is being prepared. |
| Fixed | The fix passed developer tests but is not yet available for CEO retest. |
| Ready for Retest | The verified fix is deployed to staging with evidence. |
| Reopened | CEO retest failed or the verified issue remains. |
| Closed | CEO confirmed the expected result in staging after retest. |
| Deferred | CEO accepted the issue for a later release. |

## Bug Register

| Bug ID | Page | User role | Test account | Steps to reproduce | Expected result | Actual result | Screenshot | Severity | Status | Assigned developer | Fix commit | Retest result | CEO approval |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

No CEO-reported bugs are currently recorded. Add each issue using the next
sequential `KPI-UAT-NNN` identifier.

## Detailed Bug Record

Copy this section for issues that need details beyond the register row.

### KPI-UAT-NNN: Short Title

- Page:
- Staging URL:
- User role:
- Test account:
- Date and time:
- Browser/device:
- Severity: Critical / High / Medium / Low
- Status: New / Confirmed / In Progress / Fixed / Ready for Retest / Reopened /
  Closed / Deferred

Steps to reproduce:

1.
2.
3.

Expected result:

Actual result:

Screenshot:

Related checklist item:

Reproduction result:

Affected files:

Assigned developer:

Fix commit:

Regression tests:

Retest result:

CEO approval:

## UAT Fix Gate

A code change may begin only when all fields below are complete:

- [ ] The issue is reproduced on the official staging URL.
- [ ] Expected behavior is supported by the approved KPI scope or policy.
- [ ] Severity is assigned.
- [ ] Status is `Confirmed`.
- [ ] The proposed change is a defect fix, not a new feature.
- [ ] The affected permissions, workflow, exports, and regression risk are known.
- [ ] A staging-only test and rollback approach is defined.
- [ ] Staging database backup, checksum, current commit, disk space, and rollback
  instructions are recorded before the fix group is deployed.

Critical defects that appear to require new behavior must be escalated to the CEO
for explicit scope approval before implementation.

## Retest Process

1. Reproduce the reported issue on the official staging URL.
2. Record the cause, affected files, severity, and `Confirmed` status.
3. Make the smallest safe fix and add or update focused tests.
4. Run focused, KPI, permission, browser, responsive, CRM regression, and
   migration checks as applicable.
5. Deploy the passing fix to staging only and record its commit.
6. Set status to `Ready for Retest`; do not set it to `Closed`.
7. CEO repeats the original steps and records the Retest result.
8. Set status to `Closed` only after explicit CEO approval. Use `Reopened` when
   retesting fails.

## Closure Gate

- [ ] Fix exists only on the staging deployment branch.
- [ ] Targeted tests pass.
- [ ] Full required regression passes.
- [ ] Query counts and response time have not regressed.
- [ ] Browser retest passes on the official staging URL.
- [ ] Relevant mobile/tablet/desktop checks pass.
- [ ] Security and role-scope checks pass.
- [ ] CEO confirms the expected result after manual retest.
- [ ] CEO approval is recorded.
- [ ] Status is `Closed` or `Deferred`.

Closed UAT bugs may be included in the production deployment package only after
overall written CEO approval. Preparing that package does not authorize a
production deployment.
