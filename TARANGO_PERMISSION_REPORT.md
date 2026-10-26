Tarango assigned-production access — local completion, October 26, 2026

Checkout: `/Users/hossain/iconic_final_crm_integration`, branch `codex/tarango-assigned-production-access`, baseline `0f298e79a6338247bc0190cddb9a8c1969452d4c`. Resumed the existing implementation using the October 24 `REVIEW.md`. No push, deployment, live account change, or live database connection was made during completion.

1. **Files changed** — 22 implementation/test files plus this report:

   ```text
   crm/context_processors.py
   crm/middleware.py
   crm/middleware_platform.py
   crm/permissions.py
   crm/services/operations_permissions.py
   crm/services/restricted_production.py
   crm/services/restricted_production_views.py
   crm/views.py
   crm/views_production_files.py
   crm/management/commands/restrict_tarango_production.py
   crm/templates/crm/base.html
   crm/templates/crm/production_list.html
   crm/templates/crm/production_detail.html
   crm/templates/crm/production_stage_edit.html
   crm/templates/crm/partials/production_stages.html
   crm/templates/crm/partials/restricted_production_list.html
   crm/templates/crm/partials/restricted_production_detail.html
   crm/tests/test_restricted_production_access.py
   crm/tests/test_restricted_production_files.py
   crm/tests/test_restricted_production_ui.py
   iconic_site/settings.py
   iconic_site/urls.py
   TARANGO_PERMISSION_REPORT.md
   ```

2. **Permissions changed and rehearsed locally.** The verified October 25 database backup confirms User ID 16 is `Tarango`, full name Tarango Hossain, with superuser access and zero assigned orders. The identity-guarded `restrict_tarango_production` command removes superuser/staff access, broad groups and direct permissions; retains only `can_production`; and adds the account-specific `Assigned Production Only` group. It previews by default and applies atomically with an authorized actor and a `SystemActivityLog` entry.

   Server checks restrict this account to assigned orders, manufacturing stages, planned stage dates, QC, production notes and photos. Assignment revocation takes effect on the next request. Customer, finance, invoice/payment, costing, shipping, inventory, marketing, administration, global search/chatter, exports, assignment changes and production deletion are blocked. Historical sales chatter, commercial fields and unclassified attachments are excluded. The existing assignment form remains available to authorized employees; no orders are automatically assigned.

   Existing screens, stage forms, audit behavior and tables are reused. Isolated projections prevent employee views from assembling sensitive linked data. The existing `/media/` URL gains a protected handler because direct static delivery has no server-side account/assignment check. No new page, model, field or migration; no Financial Core, invoice/payment, Move to Production, email-sync or automation code changes.

3. **Verification.** All 120 selected tests passed in 31.268 seconds; zero failures. Coverage: assigned/unassigned access, restricted module reads/writes, privilege restoration attempts, active sessions, assignment revocation, stage/QC/dates/notes/photos, CSRF, stale updates, audit/history preservation, media traversal/symlinks/cache controls, and existing employee roles/file access. `manage.py check`: no issues. `py_compile`: all 15 changed Python files passed. `git diff --check`: passed. Desktop/mobile browser scenarios passed for all 10 permitted stage popups, planned dates, timestamps, duplicate-submit prevention, notes and photo delivery.

   The account-change rehearsal compared every table: other users, production records/history and financial tables were unchanged. Recovery restored all 210 tables and 1,038 code files; database recovery was byte-identical. The new local checkpoint uses the verified October 25 production snapshot; it is **not a fresh production backup**.

   Measured local performance on disposable copies, using a synthetic assigned order:

   | Screen | Warm queries before → after | Cold ms before → after | Warm median ms before → after |
   | --- | --- | --- | --- |
   | Tarango order detail | 34 → 8 | 114.58 → 43.84 | 34.33 → 5.95 |
   | Tarango assigned search | 8 → 5 | 20.85 → 10.07 | 12.63 → 3.12 |

   Cold means the first page request after application-cache clearing; warm is the median of five repeats. These are local Django-client measurements, not live/network timings. N+1 check passed: adding 20 assigned orders kept search at five queries; detail has an eight-query regression bound. The comparison employee detail remained at 38 warm queries before/after; its existing budget excess is outside this account-only change.

4. **Remaining security issue.** The October 24 review established that live Nginx directly serves `/media/`, bypassing Django. Before activation, media requests must reach the tested protected handler, including legacy paths and shared reference images. Anonymous and unassigned access must then be verified through the actual public URL. Arbitrary production attachments/PDF exports remain denied to Tarango because they have no reviewed manufacturing-only classification.

5. **Deployment readiness.** Application implementation and local checks are complete. **Not deployed; production activation is not ready until the media bypass is closed.** A fresh verified production backup and approved release/media-routing plan are required before live activation. The live account has not been changed by this completion work. With zero assignments in the snapshot, its first restricted list is empty until an authorized employee assigns orders.

   Recovery evidence and logs: `/Users/hossain/iconic_final_integration_backups/tarango-permissions-20261026-completion/`. Original review and baseline code archive remain in the October 24/25 task backup folders. Local rollback/recovery was rehearsed on disposable copies. For a later production rollback, disable Tarango first, retain protected media delivery, and restore only the approved release/account changes; do not replace an active production database with this historical test snapshot.

Stopped after local verification, awaiting the owner's approval for any deployment work.
