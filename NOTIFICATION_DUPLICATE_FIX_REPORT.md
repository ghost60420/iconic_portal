# Notification Duplicate Fix Report

Generated: 2026-07-22

## Scope

Investigated duplicate Notification Center rows for production-ready events such as:

`PO-007-125 is ready for production planning.`

No production data was modified. No deployment was performed.

## Root Cause

The CRM Notification Center uses `AutomationNotification`, not a separate `Notification` model. `AutomationNotification.source_key` is already a unique stored notification key.

The duplicate visible rows were caused by recipient fan-out plus superuser visibility:

- Production-ready notifications are stored once per recipient.
- `create_operations_notification()` creates unique rows like `...:user:{id}` or `...:role:{role}`.
- `visible_notifications()` previously returned every unresolved notification row for superusers.
- Result: a superuser saw every recipient's row for the same production event, so one PO appeared multiple times.

Secondary risk found:

- Production-ready events were keyed by production order id: `operations:production_created:{order.id}`.
- If the same opportunity had duplicate or legacy production-order paths, the same business event could be re-emitted under multiple source keys.

## Notification Creation Paths Found

- `crm/services/operations_notifications.py`
  - `create_operations_notification()`
  - `notify_quotation_waiting_approval()`
  - `notify_quotation_decision()`
  - `notify_production_order_created()`
  - `notify_task_event()`
  - `notify_comment_added()`
  - `sync_operations_notifications()`
- `crm/signals.py`
  - `post_save(CostingHeader)` quotation submission and decision notifications
  - `post_save(QuickCosting)` quick costing submission and decision notifications
  - `post_save(ProductionOrder)` production-ready notification
  - `post_save(LeadTask)` and `post_save(OpportunityTask)` task notifications
  - `post_save(LeadComment)` owner comment notification
- `crm/services/chatter_mentions.py`
  - mention notifications through `AutomationNotification.objects.bulk_create(..., ignore_conflicts=True)`
- `crm/services/opportunity_stage_audit.py`
  - audit summary notification through `AutomationNotification.objects.update_or_create()`

No live code path using `Notification.objects.create()` or a separate `create_notification()` helper was found.

## Files Changed

- `crm/services/operations_notifications.py`
- `crm/management/commands/cleanup_duplicate_notifications.py`
- `crm/tests/test_operations_control_center.py`

## Migration Requirement

No migration is required.

Reason: the existing `AutomationNotification.source_key` field is already `unique=True` and stores the notification key.

## Fix Implemented

- Superusers now use the same recipient-scoped visibility rule as other users.
- Superusers still retain all operations roles through `operations_role_names()`, but no longer see other users' direct notification rows.
- Production-ready notifications now use an opportunity-level business key when possible:
  - `operations:production_ready:opportunity:{opportunity.id}:user:{user.id}`
  - fallback: `operations:production_ready:order:{order.id}:user:{user.id}`
- The shared notification creator now resolves stale recipient rows for the same source event.
- Legacy production-created source keys are resolved when a production-ready event is re-emitted.
- Quick Costing notification visibility was included for users with quotation access.

## Cleanup Command

Created:

`python3 manage.py cleanup_duplicate_notifications`

Default behavior is dry-run only.

Apply mode:

`python3 manage.py cleanup_duplicate_notifications --apply`

Rules:

- groups duplicates by event and recipient
- keeps the oldest row
- deletes later duplicate rows only with `--apply`
- preserves unread status when any duplicate was unread
- preserves active status when any duplicate was still active

Local copied DB dry-run result:

```text
DRY RUN: duplicate notification groups found: 0
DRY RUN: duplicate notification rows to delete: 0
No notifications deleted. Re-run with --apply after review.
```

## Before And After

Local copied DB evidence:

- Duplicate `source_key` rows: 0
- Same active production event per same user/role: 0 duplicate groups
- Production order 125 had 8 active per-recipient rows for the same production-ready message.
- Old superuser visibility returned all recipient rows for a superuser.
- New superuser visibility returns only the row addressed to that superuser or applicable role/broadcast rows.

Example verification after the fix:

```text
order125 visible rows [{'id': 626, 'source_key': 'operations:production_created:125:user:11', 'assigned_user_id': 11, 'assigned_role': ''}]
```

## Performance

Measured locally against the copied DB using the Notification Center route.

Before, with the old superuser branch monkeypatched:

- cold response: 15 queries, 100.43 ms
- warm response: 5 queries, 33.05 ms
- unread count for Hossain superuser: 147

After:

- cold response: 7 queries, 16.95 ms
- warm response: 5 queries, 14.46 ms
- unread count for Hossain superuser: 17

N+1 verification:

- `test_notification_page_query_count_is_bounded` passed.
- Full `crm.tests` regression passed.

## Test Results

```text
python3 manage.py check
System check identified no issues (0 silenced).
```

```text
python3 manage.py makemigrations --check --dry-run
No changes detected
```

```text
python3 -m py_compile crm/services/operations_notifications.py crm/management/commands/cleanup_duplicate_notifications.py crm/tests/test_operations_control_center.py
PASS
```

```text
python3 manage.py test crm.tests.test_operations_control_center.NotificationCenterTests --verbosity 2
Ran 16 tests in 23.933s
OK
```

```text
python3 manage.py test crm.tests --verbosity 1
Ran 558 tests in 282.139s
OK
```

```text
git diff --check
PASS
```

## Deployment Recommendation

Safe to review for deployment. No migration is needed.

Recommended production order:

1. Deploy code.
2. Run `python3 manage.py check`.
3. Run `python3 manage.py cleanup_duplicate_notifications --limit 50`.
4. Review dry-run output.
5. Run `python3 manage.py cleanup_duplicate_notifications --apply` only if duplicate groups are confirmed and a fresh DB backup exists.
6. Verify the Notification Center as a superuser and as a production user.

Rollback:

- Revert the code changes in the three files listed above.
- No database rollback is required for the code-only fix.
- If cleanup `--apply` was run and needs reversal, restore from the pre-cleanup database backup because duplicate rows are deleted by design.
