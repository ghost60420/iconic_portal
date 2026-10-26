Tarango media security — tested, awaiting final deployment approval

1. **Exact Nginx changes required.** The complete candidate is [docs/nginx/iconiccrm.tarango-media.conf](docs/nginx/iconiccrm.tarango-media.conf). Only `/etc/nginx/conf.d/iconiccrm.conf` would change on AWS. Its two CRM media locations (IP/HTTP and domain/HTTPS) gain an `auth_request` check against the existing Django media handler. The added internal location sends a body-free `HEAD` request to `http://127.0.0.1:8000$request_uri`, forwards the session cookie, disables authorization caching, and maps login redirects/not-found responses to denial. Direct requests to that internal location are denied.

   Nginx retains its original media alias, content types and range delivery. Media responses gain `Cache-Control: private, no-store, no-cache, max-age=0`, `Vary: Cookie`, `nosniff`, and symlink protection. The internal check is marked HTTPS so public images on the existing HTTP/IP vhost do not fail Django's SSL redirect. The three existing financial-media denials and every non-media setting are unchanged. Replacing the two new media fragments with their originals reproduces the original configuration byte-for-byte.

2. **Files affected in this phase.**

   - `crm/views_production_files.py`: preserve unrelated anonymous/public media; recognize attachments linked through production comments as protected production files. No model writes added.
   - `crm/tests/test_restricted_production_files.py`: public-media compatibility and production-comment attachment regressions.
   - `docs/nginx/iconiccrm.tarango-media.conf`: exact candidate configuration; not installed.
   - `TARANGO_MEDIA_SECURITY_REPORT.md`: this report.

   Existing Tarango permission work remains pending on `codex/tarango-assigned-production-access`. Previously tracked application diffs were verified unchanged. No Financial Core, invoices/payments, opportunities/quotations, shipping, stage functionality, employee role definitions, migrations or historical production records changed in this phase.

3. **Security test results.** **71 focused Django tests passed**, zero failures, 3.450 seconds. **144 real Nginx HTTP checks passed** through isolated loopback ports and a local disposable Django/database copy, across `femline.ca`, `www.femline.ca` and the existing IP vhost. The full production-layout candidate also passed the installed Nginx 1.28.0 binary's `nginx -t` with the current TLS/includes, without installation or reload.

   Passed: anonymous production-file denial; Tarango assigned-image access; unassigned/revoked assignment denial; authorized employee/CEO access; arbitrary attachment denial for Tarango; production-comment attachments; legacy/shared reference paths; encoded/direct URLs; conditional/range requests after revocation; internal-auth URL denial; symlink denial; existing financial prefix denials; public image/audio delivery and byte ranges. An existing public catalog image retained identical bytes through Nginx. All **482 existing public paths** passed authorization checks using fresh database relationships and isolated file fixtures. No unexpected HTTP 5xx or application exceptions occurred in the rehearsal.

   Inventory: **654 physical files = 152 protected production files + 20 existing financial-denied files + 482 public files**. Protected scope includes styles, progress photos, order attachments, linked reference images, and production-comment attachments; **62 protected files are outside the standard production folders**. No existing media symlinks were found.

   `manage.py check`: no issues. Changed Python compilation and `git diff --check`: passed. Production journal inspection observed one calendar-invite SMTP timeout at **2026-10-26 20:09:19 UTC**, before the media rehearsal; no application code had been deployed. That separate issue was not modified.

4. **Fresh backup and recovery.** Created **2026-10-26 20:08:49 UTC** at `/home/ec2-user/iconic_portal/backups/tarango-media-security-20261026/`. Backed up the live database using SQLite's read-only `.backup`, committed code, complete Nginx configuration and media. Recovery passed database integrity/foreign-key checks and full logical dump comparison; code, Nginx and media were extracted and verified, including original ownership/permissions. Production writes/services stayed active.

   Database SHA-256: `18654d625391b0a5666ce5c407b1eba5f44f832a6b3804904c8a535b10535be3`. Database and configuration snapshots/checksums were also copied locally. Full checksums, recovery results, exact diff and test logs are in `/Users/hossain/iconic_final_integration_backups/tarango-media-security-20261026/`.

5. **Restricted access confirmation and performance.** Tarango's restriction, assignment revocation and file access worked through actual Nginx in the disposable rehearsal. The live identity remains User ID 16, Tarango Hossain, superuser enabled, zero assigned orders; no live account change was made. The rehearsal database was restored byte-identically afterward.

   | File authorization | Queries before → after | First request in fresh benchmark process | Warm median, five requests |
   | --- | --- | --- | --- |
   | Public image | 0 → 1 | 574.45 ms | 4.44 ms |
   | Tarango assigned image | 0 → 4 | 553.08 ms | 5.12 ms |

   These are measured local Django authorization timings; first requests include URL/application initialization. Nginx delivery/network time and before-change latency were not measured. Before-change static delivery had zero database queries. N+1 check passed: adding 20 assigned orders did not increase the per-file query count. Existing detail/search query-budget tests also passed.

6. **Remaining blockers and rollback.** No remaining media implementation/test blocker was found. **Final deployment approval is still required. The live media bypass remains until the tested release is installed.** The existing Tarango application changes and this media correction must be released together: verify the Django handler first, then apply/test the Nginx candidate, verify real public/authenticated URLs, and activate Tarango's restriction last. Installing the Nginx check against the old application alone would deny public media. Take/verify a new backup again if deployment occurs later after further business activity.

   No live file/config/account rollback is currently needed: nothing was deployed. Two original application files were saved locally for reverting this phase. For a later production rollback, keep Tarango disabled and protected production media denied while restoring the approved code/config backup; do not restore a historical database over newer business activity. Existing previously downloaded copies cannot be revoked by a new access rule.

Stopped after testing. No push, application deployment, Nginx installation/reload, public service restart or live Tarango permission change was performed.
