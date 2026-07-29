# KPI Review Workflow

## States

| State | Editable | Allowed transition |
| --- | --- | --- |
| Draft | Assigned manager, Director, CEO, Super Admin | Submitted |
| Submitted | No | Under Review |
| Under Review | No | Approved or Rejected |
| Rejected | No persistent edit window | Returned to Draft |
| Approved | No | Locked |
| Locked | No | None in Stage 5 |

A rejection records both `Rejected` and `Returned to Draft` history events in
one transaction. A written rejection comment is required. No transition may
skip a state.

## Authorization

View functions fail closed before invoking services. Services lock the review
row inside a transaction and repeat permission and current-state validation.
Managers cannot approve. No actor can approve or lock their own review.

## Approval Snapshot

Approval stores:

- Frozen role assignment IDs, versions, dates, managers, and weights
- Frozen template and KPI item definitions and versions
- Formula, calculation engine, range, template, and assignment versions
- KPI values, item comments, Critical Red reason and trigger
- Role results, calculated score, final status, and override state
- Manager and approval comments
- Approver ID, display name, and timestamp

The canonical JSON snapshot is hashed with SHA-256. Approved and locked model
saves reject changes to the snapshot or digest. The UI verifies the digest and
renders historical definitions and values from the snapshot, not live template
relationships.

## Audit

Every create, draft save, submit, review start, approval, rejection,
return-to-draft, and lock creates:

1. An append-only `KPIReviewTransition`
2. An existing `CRMAuditLog` entry in module `kpi_performance`

Transition update, delete, bulk create, and non-service creation are blocked.
Review, entry, and transition deletion are blocked at the model layer.

## Rollback

Migration `crm.0194_kpi_performance_reviews` is structurally reversible to
`crm.0193_kpi_employee_role_assignments`. Rollback deletes Stage 5 review data,
so after real reviews exist the operational rollback is application-code
rollback with the schema retained. A database backup is required before any
schema rollback.
