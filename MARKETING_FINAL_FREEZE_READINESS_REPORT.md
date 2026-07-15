# Marketing Module Final Freeze Readiness Report

Generated: 2026-07-15

## Scope

This report covers the Marketing Module only:

- Marketing sync automation
- Platform sync health
- Marketing Operations visibility
- Manual marketing sync controls
- Intelligence Center source labelling
- Remaining external platform blockers

No CRM core modules, lead workflows, production workflows, invoice logic, finance logic, accounting logic, or customer workflows are part of this phase.

## Automation Status

Production cron has been configured with individual, non-overlapping marketing sync jobs:

| Time (UTC) | Command | Status |
| --- | --- | --- |
| 09:10 | `marketing_sync_ga4_daily` | Scheduled |
| 09:25 | `marketing_sync_gsc_daily` | Scheduled |
| 09:40 | `marketing_sync_youtube_daily` | Scheduled |
| 09:55 | `marketing_sync_meta_daily` | Scheduled |
| 10:10 | `marketing_sync_google_business_daily` | Scheduled |

LinkedIn is intentionally not scheduled until organization scopes are approved.

TikTok is intentionally not scheduled until credentials exist.

The 24-hour cron verification window is still pending. Marketing should not be called fully frozen until the next scheduled production run confirms each job exits successfully.

## New Marketing Operations Page

Added page:

- `/marketing/operations/`

The page shows:

- Platform status badge
- Last sync time
- Last successful sync
- Current row counts
- API health
- Warning cards for missing data
- Next scheduled run
- Manual sync buttons for approved platforms
- CEO-only marketing sync logs

Manual sync buttons are available for:

- Facebook
- Instagram
- GA4
- Google Search Console
- Google Business Profile
- YouTube

Manual sync is not exposed for:

- LinkedIn
- TikTok
- Meta Ads

## Platform Status Model

Badges used:

- Connected
- Partially Connected
- Waiting Approval
- Not Configured
- API Blocked

Meta Ads uses a clear health message when connected accounts/campaigns exist but Graph API returns no insights:

- Connected
- No Recent Ad Activity

## Current Production Data State

Latest audited production counts after controlled sync:

| Area | Rows / Latest Data |
| --- | --- |
| GA4 traffic rows | 695, latest 2026-07-14 |
| GA4 page rows | 1774, latest 2026-07-14 |
| GSC query rows | 51027, latest 2026-07-13 |
| GSC page rows | 5093, latest 2026-07-13 |
| Account metric rows | 72 |
| Social metric rows | 17 |
| Social audience rows | 0 |
| Social content rows | 9 |
| Ad accounts | 6 |
| Ad campaigns | 3 |
| Ad metric rows | 0 |

## Meta Ads

Meta Ads has:

- Connected Meta credential
- Active ad accounts
- Active campaigns
- Zero `AdMetricDaily` rows

Direct Graph API checks returned:

- Last 30 days: `data: []`
- Maximum date preset: `data: []`

Current interpretation:

- Not a save/upsert bug.
- Not a dashboard calculation bug.
- Graph API is returning no insight rows for the connected ad accounts.

If this continues for seven production cron runs, Meta Ads should remain connected and be labelled:

- Connected
- No Recent Ad Activity

## Google Business Profile

Current status:

- Profile Connected
- Analytics Working
- Reviews Unavailable
- Posts Unavailable

Google Business analytics is working through the Business Profile Performance API.

Reviews and local posts remain blocked by Google:

- HTTP 403
- Reason: `SERVICE_DISABLED`
- Service: `mybusiness.googleapis.com`
- Project: `256768893225`

This should not fail the full Google Business integration because profile discovery and analytics remain functional.

## Intelligence Center

The Intelligence Center now separates:

- Live synced insight
- Internal planning recommendation
- Waiting for data
- Unavailable

Google Trends is explicitly quarantined as unavailable.

Planning-based scores now disclose their source basis. Score formulas were not changed in this phase.

## Tests

Focused checks completed locally:

- `python3 -m py_compile marketing/services/operations.py marketing/views_operations.py`
- `git diff --check`
- `python3 manage.py test marketing.tests_operations_center`

Expected before freeze:

- `python3 manage.py check`
- `python3 manage.py makemigrations --check --dry-run`
- Focused marketing tests
- Full CRM regression if deployment is planned

## Remaining Blockers

Critical blockers:

- 24-hour cron verification has not completed yet.

External blockers:

- Google My Business API still blocks reviews/posts.
- LinkedIn organization scopes are not approved.
- TikTok credentials are not configured.
- Meta Ads returns no insight rows from Graph API.

Non-blocking limitations:

- Some Intelligence Center values are planning-based, now labelled as such.
- Audience rows are still empty for connected platforms.

## Freeze Recommendation

Status: **NOT READY FOR FINAL FREEZE YET**

The Marketing Module is production usable with clear partial-status labelling, but final freeze should wait until:

1. All scheduled cron jobs complete successfully during the next 24-hour cycle.
2. The Operations page is deployed and verified in production.
3. Google Business reviews/posts are either enabled by Google or accepted as external blockers.
4. Meta Ads empty insight responses are observed for seven days and then classified as no recent ad activity.

Once those conditions are met, Marketing can be considered production ready with documented external blockers.
