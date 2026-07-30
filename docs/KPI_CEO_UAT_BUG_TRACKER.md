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

## Priority

| Priority | Definition |
| --- | --- |
| Critical | Staging is unavailable, data/security is compromised, or KPI UAT cannot continue. |
| High | A required KPI workflow or approval is blocked with no acceptable workaround. |
| Medium | The workflow completes but has a material correctness or usability defect. |
| Low | Minor visual, wording, or usability defect with a clear workaround. |

Priority is based on business impact, not implementation effort. A requested
enhancement is not a bug and must be recorded separately for post-UAT review.

## Status

Use only these status values:

| Status | Meaning |
| --- | --- |
| New | Reported but not yet reproduced. |
| Confirmed | Reproduced in staging with evidence. |
| In progress | An approved staging-only fix is being prepared. |
| Ready for retest | Fix is deployed to staging and evidence is available. |
| Closed | CEO/tester confirmed the expected result in staging. |
| Deferred | CEO accepted the issue for a later release. |
| Not a bug | Current approved behavior is working as designed. |

## Bug Register

| Bug ID | Page | User role | Steps | Expected result | Actual result | Screenshot | Priority | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| KPI-UAT-001 | | | | | | | | New |
| KPI-UAT-002 | | | | | | | | New |
| KPI-UAT-003 | | | | | | | | New |
| KPI-UAT-004 | | | | | | | | New |
| KPI-UAT-005 | | | | | | | | New |
| KPI-UAT-006 | | | | | | | | New |
| KPI-UAT-007 | | | | | | | | New |
| KPI-UAT-008 | | | | | | | | New |
| KPI-UAT-009 | | | | | | | | New |
| KPI-UAT-010 | | | | | | | | New |

Add additional rows using the next sequential `KPI-UAT-NNN` identifier.

## Detailed Bug Record

Copy this section for issues that need details beyond the register row.

### KPI-UAT-NNN: Short Title

- Page:
- Staging URL:
- User role:
- Date and time:
- Browser/device:
- Priority: Critical / High / Medium / Low
- Status: New / Confirmed / In progress / Ready for retest / Closed / Deferred /
  Not a bug

Steps:

1.
2.
3.

Expected result:

Actual result:

Screenshot:

Related checklist item:

Reproduction result:

Staging-only fix commit:

Regression tests:

Retest result:

CEO/tester confirmation:

## UAT Fix Gate

A code change may begin only when all fields below are complete:

- [ ] The issue is reproduced on the official staging URL.
- [ ] Expected behavior is supported by the approved KPI scope or policy.
- [ ] Priority is assigned.
- [ ] Status is `Confirmed`.
- [ ] The proposed change is a defect fix, not a new feature.
- [ ] The affected permissions, workflow, exports, and regression risk are known.
- [ ] A staging-only test and rollback approach is defined.

Critical defects that appear to require new behavior must be escalated to the CEO
for explicit scope approval before implementation.

## Closure Gate

- [ ] Fix exists only on the staging deployment branch.
- [ ] Targeted tests pass.
- [ ] Full required regression passes.
- [ ] Query counts and response time have not regressed.
- [ ] Browser retest passes on the official staging URL.
- [ ] Relevant mobile/tablet/desktop checks pass.
- [ ] Security and role-scope checks pass.
- [ ] Tester confirms the expected result.
- [ ] Status is `Closed`, `Deferred`, or `Not a bug`.

Closed UAT bugs may be included in the production deployment package only after
overall written CEO approval. Preparing that package does not authorize a
production deployment.
