# KPI Notification Permission Matrix

| Capability | Employee | Manager | Director | HR | CEO | Super Admin |
| --- | --- | --- | --- | --- | --- | --- |
| View KPI notifications | Own only | Own assigned alerts | Own department alerts | Own HR alerts | Authorized company alerts | Authorized company alerts |
| View another employee detail | No | Assigned only | Department only | Existing HR scope | All authorized | All authorized |
| View bonus amount in notification | No | No | No | No | No | No |
| Open destination | Existing own scope | Existing team scope | Existing department scope | Existing HR scope | All authorized | All authorized |
| Mark read | Own only | Own only | Own only | Own only | Own only | Own only |
| Dismiss | Own only | Own only | Own only | Own only | Own only | Own only |
| View retained history | Own only | Own only | Own only | Own only | Own only | Own only |
| Publish notification policy | No | No | No | No | Yes | Yes |
| Run command interactively | No UI | No UI | No UI | No UI | No UI | No UI |

## Enforcement

- Notification Center routes require authentication.
- Read, open, dismiss, and history queries filter by the assigned recipient.
- State-changing routes require POST and Django CSRF protection.
- Source destination pages independently recheck current authorization.
- Director recipients are resolved only for the related department.
- HR does not inherit financial bonus-amount access.
- Existing groups, `UserAccess`, middleware, and permissions are unchanged.
