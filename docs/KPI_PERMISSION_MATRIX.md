# KPI Performance Permission Matrix

| Capability | Employee | Manager | Director | HR | CEO | Super Admin |
| --- | --- | --- | --- | --- | --- | --- |
| View own approved/locked history | Yes | Yes | Yes | Yes | Yes | Yes |
| View another employee | No | Assigned only | Department only | All | All | All |
| Open review queue | No | Yes | Yes | Yes | Yes | Yes |
| Create review | No | Assigned only | Department only | No | All non-self | All non-self |
| Edit Draft | No | Assigned only | Department only | No | All non-self | All non-self |
| Submit Draft | No | Assigned only | Department only | No | All non-self | All non-self |
| Start approval review | No | No | Department only | No | All non-self | All non-self |
| Approve or reject | No | No | Department only | No | All non-self | All non-self |
| Lock approved review | No | No | Department only | No | All non-self | All non-self |
| Edit approved history | No | No | No | No | No | No |
| Edit templates/formulas/assignments | No Stage 5 route | No Stage 5 route | No Stage 5 route | No Stage 5 route | No Stage 5 route | No Stage 5 route |

## Enforcement

- Login is required for every Stage 5 route.
- State-changing routes require POST and Django CSRF validation.
- Querysets and direct-object views are scoped independently.
- Workflow services recheck actor scope and self-approval restrictions.
- Existing CRM roles are read through the current operations-permission service.
- Stage 5 adds no permission fields, groups, middleware rules, or public API.
- Manager assignment grants access only to that assigned employee review.
