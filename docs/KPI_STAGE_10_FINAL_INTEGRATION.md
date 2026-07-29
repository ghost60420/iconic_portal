# KPI Stage 10 Final Integration

## Scope

Stage 10 integrates the approved KPI stages without changing their formulas,
review workflow, bonus engine, dashboards, intelligence rules, or notification
engine. It adds:

1. An idempotent command for creating reviewed policy drafts.
2. A service-controlled policy approval envelope.
3. Explicit assignment preview, draft, and activation commands.
4. Release, rollback, UAT, scheduler, and user documentation.

No policy is automatically published. No employee is automatically assigned.
No automation schedule is installed or activated.

## Stage Lineage

The branch is based on Stage 9 commit
`911cb1ee32735ed360cc56e7c6b03de98956812a`. Git ancestry contains the
approved Stage 2 through Stage 9 commits. The historical reconciliation
documents remain unchanged audit evidence.

## Draft Preparation

Run only on an approved non-production database:

```bash
python3 manage.py prepare_kpi_release \
  --actor CEO_OR_SUPERADMIN_USERNAME \
  --effective-date YYYY-MM-DD \
  --currency CAD
```

The command creates 15 exact-100-percent template drafts, one inactive KPI
settings draft, bonus-weight and bonus-rule drafts, one intelligence draft,
one CRM-notification draft, escalation drafts, and 20 Draft approval records.
It is idempotent and stops when an existing same-version record differs.

## Policy Workflow

Each command changes one policy only:

```bash
python3 manage.py manage_kpi_policy --action submit \
  --policy-id ID --actor USERNAME --reason "Written review reason"
python3 manage.py manage_kpi_policy --action approve \
  --policy-id ID --actor USERNAME --reason "Written approval reason"
python3 manage.py manage_kpi_policy --action publish \
  --policy-id ID --actor USERNAME
python3 manage.py manage_kpi_policy --action retire \
  --policy-id ID --actor USERNAME --reason "Written retirement reason"
```

Published and retired records are protected. A future edit begins as a new
Draft version:

```bash
python3 manage.py manage_kpi_policy --action new-version \
  --policy-id ID --actor USERNAME --effective-date YYYY-MM-DD
```

## Assignment Administration

Preview does not write:

```bash
python3 manage.py manage_kpi_assignments \
  --action preview \
  --actor CEO_OR_SUPERADMIN_USERNAME \
  --employee-id EMPLOYEE_PROFILE_ID \
  --start-date YYYY-MM-DD \
  --role TEMPLATE_ID:WEIGHT:MANAGER_USER_ID:yes
```

Repeat `--role` for multi-role employees. Use `none` for an approved
managerless role and `yes` or `no` for bonus eligibility. The sum must be
exactly `100.00`.

Save the reviewed plan as inactive:

```bash
python3 manage.py manage_kpi_assignments \
  --action save-draft \
  --actor CEO_OR_SUPERADMIN_USERNAME \
  --employee-id EMPLOYEE_PROFILE_ID \
  --start-date YYYY-MM-DD \
  --role TEMPLATE_ID:60:MANAGER_USER_ID:yes \
  --role TEMPLATE_ID:40:MANAGER_USER_ID:no \
  --notes "Approved assignment context"
```

Activate only after template publication and confirmation:

```bash
python3 manage.py manage_kpi_assignments \
  --action activate \
  --actor CEO_OR_SUPERADMIN_USERNAME \
  --assignment-id ID \
  --assignment-id ID \
  --reason "Written activation approval" \
  --confirm
```

Activation revalidates employee, manager, dates, unique templates, exact
weight, and effective published template versions. Stage 3 history records
every created and changed assignment.

## Migration

`crm.0198_kpi_release_governance` adds only
`crm_kpipolicyapproval`. It has no seed, data conversion, protected-table
change, or employee assignment.

## Current Release Gate

Automated tests and copied-development database rehearsals pass. The release is
still blocked on human UAT, a recent sanitized production-copy rehearsal,
current production-commit confirmation, CEO policy approval, monitoring
ownership, and a selected deployment window. No release-candidate tag was
created.
