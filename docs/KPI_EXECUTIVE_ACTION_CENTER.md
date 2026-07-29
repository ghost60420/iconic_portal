# KPI Executive Action Center

## Purpose

The Action Center converts authorized Red and Yellow intelligence into a
specific next step without automating the decision. Examples include opening
an employee review, a department dashboard, a review queue, a bonus result, or
an employee Performance tab.

## Link Security

Action links contain a signed payload with:

- Action kind
- Source object identifier
- Limited navigation parameters
- Stage 8 signing salt

The action endpoint validates the signature and age, reloads the target through
existing Stage 5/7 scope services, and confirms that the current user may see
the target. A valid signature is not authorization by itself. Employees cannot
open another employee, managers cannot cross team scope, and Directors cannot
cross department scope.

No link is generated when the current audience cannot access the destination.
There are no public or client-side-only action routes.

## Audit

The existing CRM audit log records Critical alert views and action-link use.
It records the actor, event, source identifier, label, timestamp, and target
path without copying private review or bonus content into normal logs.

## Limits

Stage 8 does not submit, approve, reject, lock, edit, notify, pay, or otherwise
change a source record. Stage 9 may add notifications and automation only after
separate approval and must preserve these server authorization checks.
