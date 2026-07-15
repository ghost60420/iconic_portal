# Marketing Intelligence Completion Report

Date: 2026-07-15
Branch: `codex/google-business-api-diagnosis`
Status: Ready for review, not deployed.

## Scope

Implemented Marketing Intelligence Center changes only.

Changed files:

- `marketing/services/intelligence.py`
- `marketing/views_intelligence.py`
- `marketing/templates/marketing/intelligence.html`
- `marketing/tests_intelligence.py`
- `marketing/tests_intelligence_phase2.py`
- `marketing/tests_operations_center.py`

No changes were made to CRM core modules, database schema, models, migrations, cron jobs, sync commands, GA4, GSC, Meta, LinkedIn, TikTok, Leads, Production, Finance, Accounting, Invoices, or WhatsApp.

## Google Business API Entitlement Finding

Production diagnostics already confirmed:

- OAuth client belongs to project number `256768893225`.
- Access token audience matches the deployed OAuth client.
- Token includes `https://www.googleapis.com/auth/business.manage`.
- Account discovery works.
- Location discovery works.
- Business Information API works.
- Performance API works.
- Token refresh works.
- Only `mybusiness.googleapis.com` returns `403 SERVICE_DISABLED`.

Official documentation still lists Google Business reviews and local posts on the legacy `mybusiness.googleapis.com/v4` service:

- Reviews: `GET https://mybusiness.googleapis.com/v4/{parent=accounts/*/locations/*}/reviews`
- Local posts: `GET https://mybusiness.googleapis.com/v4/{parent=accounts/*/locations/*}/localPosts`
- Both accept `https://www.googleapis.com/auth/business.manage`.

Service Usage verification is blocked from the CRM OAuth credential because the saved production token does not include Cloud Service Usage scope. `gcloud` was not available in the local or production shell during diagnosis. Final entitlement confirmation requires a Google Cloud principal with permission to call Service Usage for project `256768893225`.

Recommended external check:

```bash
gcloud config set project iconic-apparel-house-crm
gcloud services describe mybusiness.googleapis.com --project=iconic-apparel-house-crm
gcloud services list --enabled --project=iconic-apparel-house-crm | grep mybusiness
```

If `mybusiness.googleapis.com` is not enabled or not visible, this is an entitlement or approval issue for the legacy Google My Business API, not a CRM code issue.

## Intelligence Center Changes

Removed the old planning-placeholder presentation from the Intelligence Center.

Implemented real score calculations from stored production data rows:

- Content Score:
  - synced Facebook, Instagram, and YouTube post frequency
  - synced engagement
  - synced post count
  - posting-day consistency
  - schedule adherence

- Website Score:
  - GA4 visitor trend
  - conversions
  - engaged-session rate
  - landing-page coverage

- Google Business Score:
  - Google Business analytics rows
  - impressions
  - reach
  - clicks
  - engagement actions
  - reviews/posts are not required for this score while `mybusiness.googleapis.com` is blocked

- Social Score:
  - Facebook, Instagram, and YouTube metrics only
  - impressions/views
  - engagement
  - post trend

- Consistency Score:
  - posting-day gaps
  - schedule adherence
  - overdue content penalty

- Overall Marketing Health:
  - weighted average of available live or partial metrics only
  - unavailable sections are excluded rather than treated as zero

## Source Labels

The page now explicitly labels sections as:

- `Live Data`
- `Partial Data`
- `Unavailable Data`

Unavailable sources display `No data`, `No GA4 visitor data available`, `No GSC keyword data available`, or equivalent messages instead of misleading zero-value scores.

## Recommendation Engine

Recommendations now use stored rows from:

- synced top-performing social posts
- synced low-performing social posts
- GA4 visitor trend
- GSC keyword performance
- Google Business analytics
- content schedule gaps
- landing-page coverage gaps
- video coverage gaps

Template-only “AI planning” wording was removed from the Intelligence Center. The page no longer presents static text as live AI intelligence.

## Google Trends

The Google Trends placeholder is no longer shown as a working feature in the Intelligence Center source cards.

Manual market observations remain available as internal notes, but they are labelled as unavailable external-provider data and are excluded from live scoring.

## Performance

Measured against an isolated temporary test database:

- Cold render: `19` queries, `114.51 ms`
- Warm render: `9` queries, `35.50 ms`

Focused query-budget regression passed:

- Warm Marketing Intelligence render remains at or under the existing 10-query page budget.
- Populated data does not increase query count beyond baseline.
- No N+1 behavior detected in the focused tests.

## Verification

Commands run:

```bash
python3 -m py_compile marketing/services/intelligence.py marketing/views_intelligence.py
DJANGO_SECRET_KEY=local-test-secret python3 manage.py check
DJANGO_SECRET_KEY=local-test-secret python3 manage.py makemigrations --check --dry-run
DJANGO_SECRET_KEY=local-test-secret python3 manage.py test marketing.tests_intelligence marketing.tests_intelligence_phase2
DJANGO_SECRET_KEY=local-test-secret python3 manage.py test crm.tests
DJANGO_SECRET_KEY=local-test-secret python3 manage.py test marketing
git diff --check
git diff --cached --check
```

Results:

- `py_compile`: passed
- `manage.py check`: passed
- `makemigrations --check --dry-run`: passed, no changes detected
- Focused Marketing Intelligence tests: `23` tests passed
- Full CRM regression: `480` tests passed
- Full Marketing tests: `119` tests passed
- `git diff --check`: passed
- `git diff --cached --check`: passed

## Remaining External Limitation

Google Business reviews and local posts remain blocked by Google service entitlement:

- Failing service: `mybusiness.googleapis.com`
- Known status from production API response: `403 SERVICE_DISABLED`
- Working services remain working:
  - account discovery
  - location discovery
  - profile sync
  - analytics/performance sync
  - token refresh

The CRM should display Google Business as partially connected until Google grants or exposes the legacy `mybusiness.googleapis.com` service for project `256768893225`.

## Deployment Status

Not deployed.

No database records were intentionally changed by this implementation work.

Recommended next step: review the six-file Marketing Intelligence diff, then deploy as a marketing-only UI/service update if approved.
