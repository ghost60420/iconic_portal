from datetime import timedelta
import uuid
import json
import re

from django.conf import settings
from django.contrib import messages
from django.db.models import Avg, Count, Sum, Q
from django.db.models.functions import Coalesce
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date

from crm.models import Lead, Opportunity
from marketing.forms import (
    CampaignForm,
    CSVUploadForm,
    ContactListForm,
    OutreachCampaignForm,
    OutreachMessageTemplateForm,
    TrackedLinkForm,
    SocialAccountConnectForm,
    MarketingCompetitorForm,
    MarketingCompetitorAccountForm,
    MarketingCompetitorPostForm,
)
from marketing.models import (
    SocialAccount,
    SocialAudienceDaily,
    AccountMetricDaily,
    AdCampaign,
    AdMetricDaily,
    BestPracticeLibrary,
    Campaign,
    ContactList,
    InsightItem,
    OutreachCampaign,
    OutreachSendLog,
    SeoPageDaily,
    SeoQueryDaily,
    SocialContent,
    SocialMetricDaily,
    UnsubscribeEvent,
    Contact,
    OAuthCredential,
    OAuthConnectionRequest,
    MarketingCompetitor,
    MarketingCompetitorAccount,
    MarketingCompetitorPost,
    MarketingCompetitorInsight,
)
from marketing.services.metrics import calc_engagement_total, calc_engagement_rate, calc_engagement_score
from marketing.services.oauth_meta import build_meta_oauth_url
from marketing.utils.importer import import_contacts_from_csv
from marketing.utils.activity import log_marketing_activity
from marketing.utils.templates import seed_default_templates


PLATFORM_CARD_CONFIG = [
    {"key": "instagram", "label": "Instagram", "platforms": ["instagram"]},
    {"key": "facebook", "label": "Facebook", "platforms": ["facebook", "meta_business"]},
    {"key": "linkedin", "label": "LinkedIn", "platforms": ["linkedin"]},
    {"key": "tiktok", "label": "TikTok", "platforms": ["tiktok"]},
    {"key": "youtube", "label": "YouTube", "platforms": ["youtube"]},
    {"key": "google_business", "label": "Google Business", "platforms": ["google_business"]},
]


def _require_enabled(flag_name: str | None = None):
    if not getattr(settings, "MARKETING_ENABLED", False):
        raise Http404("Marketing disabled")
    if flag_name and not getattr(settings, flag_name, False):
        raise Http404("Marketing feature disabled")


def connect_accounts(request):
    _require_enabled()
    meta_redirect_uri = getattr(settings, "MARKETING_META_REDIRECT_URI", "")
    meta_app_id = getattr(settings, "MARKETING_META_APP_ID", "")
    meta_app_secret = getattr(settings, "MARKETING_META_APP_SECRET", "")
    accounts = SocialAccount.objects.all().prefetch_related("platform_credentials").order_by("platform", "display_name")
    connected_rows = []
    for account in accounts:
        cred = account.platform_credentials.first()
        connected_rows.append(
            {
                "account": account,
                "has_token": bool(cred and cred.get_access_token()),
                "has_refresh": bool(cred and cred.get_refresh_token()),
            }
        )

    if request.method == "POST" and request.POST.get("disconnect"):
        account = get_object_or_404(SocialAccount, pk=request.POST.get("disconnect"))
        OAuthCredential.objects.filter(platform_account=account).update(
            encrypted_access_token="",
            encrypted_refresh_token="",
            expires_at=None,
            scopes="",
        )
        account.is_active = False
        account.save(update_fields=["is_active", "updated_at"])
        log_marketing_activity(
            user=request.user,
            action="account_disconnect",
            model_label="marketing.SocialAccount",
            object_id=account.pk,
            message=f"Disconnected {account.get_platform_display()} account",
        )
        messages.success(request, "Account disconnected.")
        return redirect("marketing_connect")

    form = SocialAccountConnectForm(request.POST or None)
    if request.method == "POST" and not request.POST.get("disconnect"):
        if form.is_valid():
            data = form.cleaned_data
            account, _ = SocialAccount.objects.update_or_create(
                platform=data["platform"],
                external_account_id=data["external_account_id"],
                defaults={
                    "display_name": data["display_name"],
                    "timezone": data.get("timezone") or "",
                    "is_active": True,
                },
            )
            cred = OAuthCredential.objects.filter(platform_account=account).first()
            if not cred:
                cred = OAuthCredential(platform=data["platform"], platform_account=account)
            cred.set_tokens(
                access_token=data.get("access_token") or "",
                refresh_token=data.get("refresh_token") or "",
                expires_at=data.get("expires_at"),
            )
            cred.scopes = data.get("scopes") or ""
            cred.save()
            log_marketing_activity(
                user=request.user,
                action="account_connect",
                model_label="marketing.SocialAccount",
                object_id=account.pk,
                message=f"Connected {account.get_platform_display()} account",
            )
            if data.get("access_token"):
                messages.success(request, "Account connected.")
            else:
                messages.warning(request, "Account saved without access token. Add a token to sync data.")
            return redirect("marketing_connect")

    oauth_requests = OAuthConnectionRequest.objects.filter(platform="meta").order_by("-created_at")[:10]

    return render(
        request,
        "marketing/connect_accounts.html",
        {
            "form": form,
            "connected_rows": connected_rows,
            "oauth_requests": oauth_requests,
            "meta_redirect_uri": meta_redirect_uri,
            "meta_configured": bool(meta_app_id and meta_app_secret),
        },
    )


def meta_oauth_start(request):
    _require_enabled("MARKETING_SOCIAL_ENABLED")
    app_id = getattr(settings, "MARKETING_META_APP_ID", "")
    app_secret = getattr(settings, "MARKETING_META_APP_SECRET", "")
    redirect_uri = getattr(settings, "MARKETING_META_REDIRECT_URI", "")
    if not app_id or not app_secret or not redirect_uri:
        messages.error(request, "Meta app is not configured. Add app ID, secret, and redirect URL.")
        return redirect("marketing_connect")

    scopes = getattr(
        settings,
        "MARKETING_META_SCOPES",
        [
            "pages_show_list",
            "pages_read_engagement",
            "read_insights",
            "instagram_basic",
            "instagram_manage_insights",
            "business_management",
        ],
    )
    state = uuid.uuid4().hex
    OAuthConnectionRequest.objects.create(
        platform="meta",
        user=request.user,
        state=state,
        status="initiated",
    )
    url = build_meta_oauth_url(app_id=app_id, redirect_uri=redirect_uri, state=state, scopes=scopes)
    return redirect(url)


def meta_oauth_callback(request):
    _require_enabled("MARKETING_SOCIAL_ENABLED")
    state = request.GET.get("state", "")
    code = request.GET.get("code", "")
    error = request.GET.get("error_description") or request.GET.get("error")

    if not state:
        messages.error(request, "Missing OAuth state.")
        return redirect("marketing_connect")

    conn = OAuthConnectionRequest.objects.filter(platform="meta", state=state).first()
    if not conn:
        messages.error(request, "OAuth request not found.")
        return redirect("marketing_connect")
    if conn.user and conn.user != request.user:
        conn.status = "error"
        conn.error_message = "User mismatch during OAuth callback."
        conn.save(update_fields=["status", "error_message", "updated_at"])
        messages.error(request, "OAuth user mismatch. Please try again.")
        return redirect("marketing_connect")

    if error:
        conn.status = "error"
        conn.error_message = error
        conn.save(update_fields=["status", "error_message", "updated_at"])
        messages.error(request, f"Meta authorization failed: {error}")
        return redirect("marketing_connect")

    if not code:
        conn.status = "error"
        conn.error_message = "Missing code."
        conn.save(update_fields=["status", "error_message", "updated_at"])
        messages.error(request, "Meta authorization failed: missing code.")
        return redirect("marketing_connect")

    conn.code = code
    conn.status = "received"
    conn.error_message = ""
    conn.save(update_fields=["code", "status", "error_message", "updated_at"])
    messages.success(request, "Meta authorization received. Run the OAuth processor to finish connection.")
    return redirect("marketing_connect")


def _date_window(range_key: str):
    today = timezone.localdate()
    normalized = (range_key or "30").strip().lower()
    day_map = {
        "today": 1,
        "7": 7,
        "30": 30,
        "90": 90,
    }
    days = day_map.get(normalized, 30)
    start = today - timedelta(days=days - 1)
    end = today
    previous_end = start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=days - 1)
    return {
        "key": normalized if normalized in day_map else "30",
        "days": days,
        "start": start,
        "end": end,
        "previous_start": previous_start,
        "previous_end": previous_end,
        "label": "Today" if days == 1 else f"Last {days} Days",
    }


def _percent_change(current: int | float, previous: int | float) -> float:
    current_val = float(current or 0)
    previous_val = float(previous or 0)
    if previous_val <= 0:
        return 100.0 if current_val > 0 else 0.0
    return ((current_val - previous_val) / previous_val) * 100.0


def _metric_totals(start_date, end_date):
    totals = SocialMetricDaily.objects.filter(date__gte=start_date, date__lte=end_date).aggregate(
        impressions=Sum("impressions"),
        reach=Sum("reach"),
        views=Sum("views"),
        clicks=Sum("clicks"),
        likes=Sum("likes"),
        comments=Sum("comments"),
        shares=Sum("shares"),
        saves=Sum("saves"),
    )
    impressions = totals.get("impressions") or 0
    reach = totals.get("reach") or 0
    views = totals.get("views") or 0
    clicks = totals.get("clicks") or 0
    likes = totals.get("likes") or 0
    comments = totals.get("comments") or 0
    shares = totals.get("shares") or 0
    saves = totals.get("saves") or 0
    engagement_total = calc_engagement_total(
        likes=likes,
        comments=comments,
        shares=shares,
        saves=saves,
    )
    engagement_score = calc_engagement_score(
        likes=likes,
        comments=comments,
        shares=shares,
        saves=saves,
        clicks=clicks,
    )
    engagement_rate = calc_engagement_rate(
        impressions=impressions,
        reach=reach,
        views=views,
        engagement_total=engagement_total,
    ) * 100
    return {
        "impressions": impressions,
        "reach": reach,
        "views": views,
        "clicks": clicks,
        "engagement_total": engagement_total,
        "engagement_score": engagement_score,
        "engagement_rate": engagement_rate,
    }


def _collect_content_metrics(content_qs, start_date=None, end_date=None):
    metric_filter = Q()
    if start_date:
        metric_filter &= Q(daily_metrics__date__gte=start_date)
    if end_date:
        metric_filter &= Q(daily_metrics__date__lte=end_date)

    annotated = content_qs.annotate(
        impressions=Coalesce(Sum("daily_metrics__impressions", filter=metric_filter), 0),
        reach=Coalesce(Sum("daily_metrics__reach", filter=metric_filter), 0),
        views=Coalesce(Sum("daily_metrics__views", filter=metric_filter), 0),
        clicks=Coalesce(Sum("daily_metrics__clicks", filter=metric_filter), 0),
        likes=Coalesce(Sum("daily_metrics__likes", filter=metric_filter), 0),
        comments=Coalesce(Sum("daily_metrics__comments", filter=metric_filter), 0),
        shares=Coalesce(Sum("daily_metrics__shares", filter=metric_filter), 0),
        saves=Coalesce(Sum("daily_metrics__saves", filter=metric_filter), 0),
    ).select_related("account")

    rows = []
    for item in annotated:
        engagement_total = calc_engagement_total(
            likes=item.likes,
            comments=item.comments,
            shares=item.shares,
            saves=item.saves,
        )
        engagement_score = calc_engagement_score(
            likes=item.likes,
            comments=item.comments,
            shares=item.shares,
            saves=item.saves,
            clicks=item.clicks,
        )
        engagement_rate = calc_engagement_rate(
            impressions=item.impressions,
            reach=item.reach,
            views=item.views,
            engagement_total=engagement_total,
        ) * 100
        rows.append(
            {
                "content": item,
                "impressions": item.impressions,
                "reach": item.reach,
                "views": item.views,
                "clicks": item.clicks,
                "likes": item.likes,
                "comments": item.comments,
                "shares": item.shares,
                "saves": item.saves,
                "engagement_total": engagement_total,
                "engagement_score": engagement_score,
                "engagement_rate": engagement_rate,
            }
        )
    return rows


def _marketing_lead_count(start_date, end_date):
    return (
        Lead.objects.filter(
            created_date__gte=start_date,
            created_date__lte=end_date,
            utm_source__isnull=False,
        )
        .exclude(utm_source="")
        .count()
    )


def _ad_conversions_count(start_date, end_date):
    total = (
        AdMetricDaily.objects.filter(date__gte=start_date, date__lte=end_date).aggregate(
            conversions=Coalesce(Sum("conversions"), 0)
        )
    ).get("conversions") or 0
    return int(total)


def _build_kpi_cards(period):
    current_metrics = _metric_totals(period["start"], period["end"])
    previous_metrics = _metric_totals(period["previous_start"], period["previous_end"])
    current_leads = _marketing_lead_count(period["start"], period["end"])
    previous_leads = _marketing_lead_count(period["previous_start"], period["previous_end"])
    current_conversions = _ad_conversions_count(period["start"], period["end"])
    previous_conversions = _ad_conversions_count(period["previous_start"], period["previous_end"])
    current_lead_conversion_total = current_leads + current_conversions
    previous_lead_conversion_total = previous_leads + previous_conversions

    cards = [
        {
            "title": "Total Reach",
            "value": current_metrics["reach"],
            "change_pct": _percent_change(current_metrics["reach"], previous_metrics["reach"]),
            "label": "vs previous period",
        },
        {
            "title": "Total Views",
            "value": current_metrics["views"],
            "change_pct": _percent_change(current_metrics["views"], previous_metrics["views"]),
            "label": "vs previous period",
        },
        {
            "title": "Total Engagement",
            "value": current_metrics["engagement_total"],
            "change_pct": _percent_change(current_metrics["engagement_total"], previous_metrics["engagement_total"]),
            "label": "vs previous period",
        },
        {
            "title": "Total Clicks",
            "value": current_metrics["clicks"],
            "change_pct": _percent_change(current_metrics["clicks"], previous_metrics["clicks"]),
            "label": "vs previous period",
        },
        {
            "title": "Leads / Conversions",
            "value": current_lead_conversion_total,
            "change_pct": _percent_change(current_lead_conversion_total, previous_lead_conversion_total),
            "label": "vs previous period",
            "detail": f"{current_leads} leads, {current_conversions} conversions",
        },
    ]

    return {
        "cards": cards,
        "current_metrics": current_metrics,
        "previous_metrics": previous_metrics,
        "current_leads": current_leads,
        "current_conversions": current_conversions,
    }


def _platform_comparison(start_date, end_date):
    metric_rows = (
        SocialMetricDaily.objects.filter(date__gte=start_date, date__lte=end_date)
        .values("content__platform")
        .annotate(
            impressions=Coalesce(Sum("impressions"), 0),
            reach=Coalesce(Sum("reach"), 0),
            views=Coalesce(Sum("views"), 0),
            clicks=Coalesce(Sum("clicks"), 0),
            likes=Coalesce(Sum("likes"), 0),
            comments=Coalesce(Sum("comments"), 0),
            shares=Coalesce(Sum("shares"), 0),
            saves=Coalesce(Sum("saves"), 0),
        )
    )
    metric_map = {row["content__platform"]: row for row in metric_rows}

    follower_rows = (
        AccountMetricDaily.objects.filter(date__gte=start_date, date__lte=end_date)
        .values("account__platform")
        .annotate(
            followers_change=Coalesce(Sum("followers_change"), 0),
            row_count=Count("id"),
        )
    )
    follower_map = {row["account__platform"]: row for row in follower_rows}

    cards = []
    for config in PLATFORM_CARD_CONFIG:
        totals = {
            "impressions": 0,
            "reach": 0,
            "views": 0,
            "clicks": 0,
            "likes": 0,
            "comments": 0,
            "shares": 0,
            "saves": 0,
        }
        followers_change = 0
        follower_data_points = 0

        for platform_key in config["platforms"]:
            row = metric_map.get(platform_key)
            if row:
                for key in totals:
                    totals[key] += row.get(key) or 0
            follower_row = follower_map.get(platform_key)
            if follower_row:
                followers_change += follower_row.get("followers_change") or 0
                follower_data_points += follower_row.get("row_count") or 0

        engagement_total = calc_engagement_total(
            likes=totals["likes"],
            comments=totals["comments"],
            shares=totals["shares"],
            saves=totals["saves"],
        )
        engagement_score = calc_engagement_score(
            likes=totals["likes"],
            comments=totals["comments"],
            shares=totals["shares"],
            saves=totals["saves"],
            clicks=totals["clicks"],
        )
        engagement_rate = calc_engagement_rate(
            impressions=totals["impressions"],
            reach=totals["reach"],
            views=totals["views"],
            engagement_total=engagement_total,
        ) * 100
        has_activity = any(totals.values()) or follower_data_points > 0

        cards.append(
            {
                "key": config["key"],
                "label": config["label"],
                "platform": config["key"],
                "impressions": totals["impressions"],
                "reach": totals["reach"],
                "views": totals["views"],
                "clicks": totals["clicks"],
                "engagement_total": engagement_total,
                "engagement_score": engagement_score,
                "engagement_rate": engagement_rate,
                "followers_change": followers_change,
                "follower_change_available": follower_data_points > 0,
                "has_activity": has_activity,
            }
        )

    active_rates = [card["engagement_rate"] for card in cards if card["has_activity"]]
    average_rate = sum(active_rates) / max(len(active_rates), 1) if active_rates else 0.0
    best_key = ""
    if cards:
        best_key = max(cards, key=lambda item: (item["engagement_rate"], item["engagement_score"], item["clicks"]))["key"]

    for card in cards:
        if not card["has_activity"]:
            card["status"] = "No Data"
            card["status_tone"] = "stable"
        elif card["engagement_rate"] > average_rate:
            card["status"] = "Strong"
            card["status_tone"] = "good"
        elif average_rate and card["engagement_rate"] >= (average_rate * 0.85):
            card["status"] = "Stable"
            card["status_tone"] = "stable"
        else:
            card["status"] = "Needs Attention"
            card["status_tone"] = "warn"
        card["is_best_platform"] = card["key"] == best_key and card["has_activity"]

    return cards


def _content_type_rollups(rows):
    label_map = dict(SocialContent.CONTENT_CHOICES)
    totals = {}

    for row in rows:
        if not (row["impressions"] or row["views"] or row["engagement_score"]):
            continue
        content_type = row["content"].content_type or "post"
        bucket = totals.setdefault(
            content_type,
            {
                "key": content_type,
                "label": label_map.get(content_type, content_type.replace("_", " ").title()),
                "count": 0,
                "impressions": 0,
                "views": 0,
                "clicks": 0,
                "engagement_score": 0,
                "engagement_rate_total": 0.0,
            },
        )
        bucket["count"] += 1
        bucket["impressions"] += row["impressions"]
        bucket["views"] += row["views"]
        bucket["clicks"] += row["clicks"]
        bucket["engagement_score"] += row["engagement_score"]
        bucket["engagement_rate_total"] += row["engagement_rate"]

    rollups = []
    for item in totals.values():
        item["avg_engagement_rate"] = item["engagement_rate_total"] / max(item["count"], 1)
        rollups.append(item)
    return rollups


def _extract_topic(rows):
    stopwords = {
        "the", "and", "for", "with", "your", "from", "this", "that", "have", "has", "are", "our", "you",
        "into", "about", "more", "less", "what", "when", "where", "which", "their", "they", "them", "will",
        "just", "than", "how", "why", "can", "new", "all", "one", "two", "too", "its", "it's", "iconic",
        "apparel", "house", "brand", "brands", "post", "video", "reel", "factory",
    }
    scores = {}
    for row in rows:
        text = " ".join(
            [
                row["content"].title or "",
                row["content"].message_text or "",
            ]
        ).lower()
        for token in re.findall(r"[a-z0-9]{4,}", text):
            if token in stopwords:
                continue
            scores[token] = scores.get(token, 0) + max(row["engagement_score"], 1)
    if not scores:
        return ""
    return max(scores.items(), key=lambda item: item[1])[0].replace("_", " ").title()


def _best_posting_window(rows):
    day_scores = {}
    hour_scores = {}

    for row in rows:
        published_at = row["content"].published_at
        if not published_at or row["engagement_score"] <= 0:
            continue
        day = published_at.strftime("%A")
        hour = published_at.strftime("%H:00")
        day_scores[day] = day_scores.get(day, 0) + row["engagement_score"]
        hour_scores[hour] = hour_scores.get(hour, 0) + row["engagement_score"]

    best_day = max(day_scores.items(), key=lambda item: item[1]) if day_scores else None
    best_hour = max(hour_scores.items(), key=lambda item: item[1]) if hour_scores else None
    return best_day, best_hour


def _performance_drivers(platform_cards, content_rows):
    active_platforms = [card for card in platform_cards if card["has_activity"]]
    best_platform = (
        max(active_platforms, key=lambda item: (item["engagement_rate"], item["engagement_score"], item["clicks"]))
        if active_platforms
        else None
    )
    weakest_platform = (
        min(active_platforms, key=lambda item: (item["engagement_rate"], item["engagement_score"]))
        if len(active_platforms) > 1
        else None
    )

    content_types = _content_type_rollups(content_rows)
    best_content_type = (
        max(content_types, key=lambda item: (item["avg_engagement_rate"], item["engagement_score"], item["count"]))
        if content_types
        else None
    )
    weakest_content_type = (
        min(content_types, key=lambda item: (item["avg_engagement_rate"], item["engagement_score"]))
        if len(content_types) > 1
        else None
    )

    best_day, best_hour = _best_posting_window(content_rows)
    highest_click_platform = (
        max(active_platforms, key=lambda item: (item["clicks"], item["engagement_score"], item["engagement_rate"]))
        if active_platforms
        else None
    )
    best_topic = _extract_topic(sorted(content_rows, key=lambda row: row["engagement_score"], reverse=True)[:10])

    chips = [
        {
            "label": "Best content type",
            "value": best_content_type["label"] if best_content_type else "Not enough data yet",
        },
        {
            "label": "Best posting day",
            "value": best_day[0] if best_day else "Not enough data yet",
        },
        {
            "label": "Best posting hour",
            "value": best_hour[0] if best_hour else "Not enough data yet",
        },
        {
            "label": "Best topic",
            "value": best_topic or "Not enough data yet",
        },
        {
            "label": "Best platform",
            "value": best_platform["label"] if best_platform else "Not enough data yet",
        },
        {
            "label": "Highest click source",
            "value": highest_click_platform["label"] if highest_click_platform else "Not enough data yet",
        },
    ]

    return {
        "best_platform": best_platform,
        "weakest_platform": weakest_platform,
        "best_content_type": best_content_type,
        "weakest_content_type": weakest_content_type,
        "best_day": best_day,
        "best_hour": best_hour,
        "highest_click_platform": highest_click_platform,
        "best_topic": best_topic,
        "chips": chips,
    }


def _why_it_worked_note(row):
    if row["clicks"] >= max(row["likes"], row["comments"], 1):
        return "Strong CTA and clear click intent."
    if row["shares"] > 0 or row["saves"] > 0:
        return "Shareable or reusable format performed well."
    if row["comments"] > 0:
        return "This post generated conversation."
    if row["views"] > row["reach"]:
        return "The hook likely kept viewers watching."
    return "Consistent creative and timing helped this post."


def _suggested_fix(row, drivers):
    best_hour = drivers.get("best_hour")
    if best_hour and row["content"].published_at and row["content"].published_at.strftime("%H:00") != best_hour[0]:
        return "Change posting time to the stronger hour."
    if row["clicks"] == 0:
        return "Try a stronger hook and clearer CTA."
    if row["shares"] == 0 and row["saves"] == 0:
        return "Use a more useful or shareable angle."
    if row["content"].content_type not in {"reel", "short", "video"}:
        return "Test video instead of a static format."
    return "Tighten caption length and simplify the message."


def _prepare_post_rows(rows, drivers, note_key: str):
    prepared = []
    for row in rows:
        prepared.append(
            {
                **row,
                "display_title": (row["content"].title or row["content"].external_content_id or "Untitled")[:60],
                "published_date": row["content"].published_at,
                note_key: _why_it_worked_note(row) if note_key == "why_note" else _suggested_fix(row, drivers),
            }
        )
    return prepared


def _rule_based_ai_insights(drivers, platform_cards, top_posts, weak_posts):
    insights = []
    if drivers["best_platform"]:
        best = drivers["best_platform"]
        insights.append(
            {
                "priority": "High",
                "title": f"{best['label']} is your strongest platform",
                "reason": f"It led the period with {best['engagement_rate']:.2f}% engagement.",
                "action": f"Create more content tailored to {best['label']} next week.",
            }
        )
    if drivers["weakest_platform"]:
        weak = drivers["weakest_platform"]
        insights.append(
            {
                "priority": "High",
                "title": f"{weak['label']} needs attention",
                "reason": f"It is below your average engagement benchmark at {weak['engagement_rate']:.2f}%.",
                "action": f"Test a different hook and content angle on {weak['label']}.",
            }
        )
    if drivers["best_content_type"]:
        content_type = drivers["best_content_type"]
        insights.append(
            {
                "priority": "Medium",
                "title": f"{content_type['label']} is the best content type",
                "reason": f"It delivered the best average engagement rate of {content_type['avg_engagement_rate']:.2f}%.",
                "action": f"Prioritize more {content_type['label'].lower()} content in the next batch.",
            }
        )
    if drivers["best_day"] and drivers["best_hour"]:
        insights.append(
            {
                "priority": "Medium",
                "title": "A stronger posting window is visible",
                "reason": f"Your best results clustered on {drivers['best_day'][0]} around {drivers['best_hour'][0]}.",
                "action": "Test that posting window again this week.",
            }
        )
    if top_posts and weak_posts and top_posts[0]["engagement_score"] > max(weak_posts[0]["engagement_score"], 0):
        gap = top_posts[0]["engagement_score"] - weak_posts[0]["engagement_score"]
        insights.append(
            {
                "priority": "Low",
                "title": "The gap between best and weakest posts is wide",
                "reason": f"Top content outscored the weakest posts by {gap} engagement points.",
                "action": "Reuse the winning creative structure and avoid repeating the weakest format unchanged.",
            }
        )
    return insights[:5]


def _weekly_action_plan(drivers, top_posts):
    best_platform = drivers.get("best_platform")
    weakest_platform = drivers.get("weakest_platform")
    best_content_type = drivers.get("best_content_type")
    top_post = top_posts[0] if top_posts else None
    best_hour = drivers.get("best_hour")

    actions = [
        {
            "action": f"Post more of your strongest {best_content_type['label'].lower()} content." if best_content_type else "Post more of your strongest content type.",
            "platform": best_platform["label"] if best_platform else "All platforms",
            "priority": "High",
            "status": "Planned",
        },
        {
            "action": f"Improve the weakest platform with one new creative test." if weakest_platform else "Improve the weakest platform with one creative test.",
            "platform": weakest_platform["label"] if weakest_platform else "TBD",
            "priority": "High",
            "status": "Planned",
        },
        {
            "action": "Reuse the format and hook from your top post." if top_post else "Reuse the format from your best recent post.",
            "platform": top_post["content"].get_platform_display() if top_post else (best_platform["label"] if best_platform else "All platforms"),
            "priority": "Medium",
            "status": "Planned",
        },
        {
            "action": f"Test posting around {best_hour[0]}." if best_hour else "Test one new posting time this week.",
            "platform": best_platform["label"] if best_platform else "All platforms",
            "priority": "Medium",
            "status": "Planned",
        },
        {
            "action": "Create one competitor-inspired content idea from market review.",
            "platform": "All platforms",
            "priority": "Low",
            "status": "Planned",
        },
    ]
    return actions


def _priority_bucket(priority_score: int) -> str:
    if int(priority_score or 0) >= 80:
        return "High Priority"
    if int(priority_score or 0) >= 60:
        return "Medium Priority"
    return "Low Priority"


def _top_priority_dashboard_insights(fallback_insights):
    items = list(
        InsightItem.objects.filter(status="open")
        .order_by("-priority_score", "-updated_at", "-created_at")[:5]
    )
    if items:
        return [
            {
                "priority": _priority_bucket(item.priority_score).replace(" Priority", ""),
                "title": item.title,
                "reason": item.reason,
                "action": item.recommended_action,
                "platform": item.platform,
                "priority_score": item.priority_score,
            }
            for item in items
        ]
    return fallback_insights[:5]


def _competitor_dashboard_snapshot():
    competitors = list(
        MarketingCompetitor.objects.prefetch_related("accounts__posts").all()
    )
    if not competitors:
        return {}

    total_competitors = len(competitors)
    active_competitors = len([item for item in competitors if item.is_active])

    highest_engagement_competitor = None
    most_active_competitor = None
    highest_engagement_value = -1.0
    most_active_count = -1
    top_post = None

    for competitor in competitors:
        posts = []
        for account in competitor.accounts.all():
            posts.extend(list(account.posts.all()))
        if posts:
            avg_engagement = sum(float(post.engagement_rate or 0) for post in posts) / max(len(posts), 1)
            if avg_engagement > highest_engagement_value:
                highest_engagement_value = avg_engagement
                highest_engagement_competitor = competitor
            if len(posts) > most_active_count:
                most_active_count = len(posts)
                most_active_competitor = competitor
            candidate_post = max(posts, key=lambda post: (post.engagement_score, float(post.engagement_rate or 0), post.views))
            if not top_post or (candidate_post.engagement_score, float(candidate_post.engagement_rate or 0), candidate_post.views) > (
                top_post.engagement_score,
                float(top_post.engagement_rate or 0),
                top_post.views,
            ):
                top_post = candidate_post

    return {
        "total_competitors": total_competitors,
        "active_competitors": active_competitors,
        "highest_engagement_competitor": highest_engagement_competitor,
        "most_active_competitor": most_active_competitor,
        "top_post": top_post,
    }


def _competitor_opportunities(competitor):
    recent_since = timezone.now() - timedelta(days=30)
    accounts = list(competitor.accounts.prefetch_related("posts").all())
    if not accounts:
        return []

    competitor_posts = []
    platforms = []
    for account in accounts:
        platforms.append(account.platform)
        competitor_posts.extend([post for post in account.posts.all() if not post.published_at or post.published_at >= recent_since])

    opportunities = []
    our_post_count = SocialContent.objects.filter(
        platform__in=platforms,
        published_at__gte=recent_since,
    ).count()
    if len(competitor_posts) > our_post_count:
        opportunities.append(
            "This competitor is posting more often. Consider increasing posting consistency."
        )

    video_types = {"reel", "short", "video", "long_video"}
    video_posts = [post for post in competitor_posts if post.content_type in video_types]
    non_video_posts = [post for post in competitor_posts if post.content_type not in video_types]
    if video_posts:
        video_avg = sum(float(post.engagement_rate or 0) for post in video_posts) / max(len(video_posts), 1)
        non_video_avg = sum(float(post.engagement_rate or 0) for post in non_video_posts) / max(len(non_video_posts), 1) if non_video_posts else 0.0
        if video_avg > non_video_avg:
            opportunities.append(
                "Their video content is getting stronger engagement. Test short factory videos."
            )

    avg_comments = sum(post.comments for post in competitor_posts) / max(len(competitor_posts), 1) if competitor_posts else 0
    if avg_comments >= 5:
        opportunities.append(
            "Their audience is responding in comments. Review the content theme and create your own version."
        )

    theme_counts = {}
    for post in competitor_posts:
        theme = (post.detected_theme or "").strip()
        if theme:
            theme_counts[theme] = theme_counts.get(theme, 0) + 1
    if theme_counts:
        top_theme = max(theme_counts.items(), key=lambda item: item[1])[0]
        opportunities.append(
            f"They repeat the theme '{top_theme}' often. Test your own distinctive version of that topic."
        )

    return opportunities[:4]


def _followers_growth(days: int = 30):
    today = timezone.localdate()
    start = today - timedelta(days=days - 1)
    labels = [(start + timedelta(days=i)) for i in range(days)]
    label_map = {d: idx for idx, d in enumerate(labels)}

    rows = (
        AccountMetricDaily.objects.filter(date__gte=start)
        .values("account__platform", "date")
        .annotate(total=Sum("followers_change"))
    )

    platforms = [p[0] for p in SocialAccount.PLATFORM_CHOICES]
    series = {p: [0 for _ in labels] for p in platforms}
    for row in rows:
        platform = row.get("account__platform")
        date_val = row.get("date")
        if platform in series and date_val in label_map:
            series[platform][label_map[date_val]] = row.get("total") or 0

    platform_labels = {p[0]: p[1] for p in SocialAccount.PLATFORM_CHOICES}
    return labels, series, platform_labels


def _can_edit_marketing(user) -> bool:
    return bool(user and user.is_authenticated and (user.is_superuser or user.groups.filter(name="Marketing Manager").exists()))


def marketing_home(request):
    return redirect("marketing_dashboard")


def dashboard(request):
    _require_enabled()

    period = _date_window(request.GET.get("range") or "30")
    period_summary = _build_kpi_cards(period)
    content_rows = _collect_content_metrics(
        SocialContent.objects.all(),
        start_date=period["start"],
        end_date=period["end"],
    )

    active_rows = [
        row
        for row in content_rows
        if row["reach"] or row["views"] or row["clicks"] or row["engagement_score"]
    ]
    top_posts_raw = sorted(
        active_rows,
        key=lambda row: (row["engagement_score"], row["engagement_rate"], row["clicks"]),
        reverse=True,
    )[:5]

    weak_posts_raw = sorted(
        [row for row in active_rows if row["reach"] > 0 or row["views"] > 0],
        key=lambda row: (row["engagement_score"], row["engagement_rate"], -row["impressions"]),
    )[:5]

    platform_summary = _platform_comparison(period["start"], period["end"])
    performance_drivers = _performance_drivers(platform_summary, content_rows)
    top_posts = _prepare_post_rows(top_posts_raw, performance_drivers, "why_note")
    weak_posts = _prepare_post_rows(weak_posts_raw, performance_drivers, "suggested_fix")
    ai_insight_fallback = _rule_based_ai_insights(performance_drivers, platform_summary, top_posts, weak_posts)
    ai_insights = _top_priority_dashboard_insights(ai_insight_fallback)
    weekly_action_plan = _weekly_action_plan(performance_drivers, top_posts)
    competitor_snapshot = _competitor_dashboard_snapshot()

    context = {
        "page_title": "Marketing Control Center",
        "page_subtitle": "All social, ads, SEO, and campaign performance in one place.",
        "period": period,
        "range_key": period["key"],
        "kpi_cards": period_summary["cards"],
        "platform_summary": platform_summary,
        "top_posts": top_posts,
        "weak_posts": weak_posts,
        "performance_drivers": performance_drivers,
        "ai_insights": ai_insights,
        "weekly_action_plan": weekly_action_plan,
        "competitor_snapshot": competitor_snapshot,
    }
    return render(request, "marketing/dashboard.html", context)


def insight_update(request, pk: int):
    _require_enabled()
    if request.method == "POST":
        status = request.POST.get("status")
        insight = InsightItem.objects.filter(pk=pk).first()
        if insight and status in {"open", "done", "snoozed"}:
            insight.status = status
            insight.save(update_fields=["status", "updated_at"])
            log_marketing_activity(
                user=request.user,
                action=f"insight_{status}",
                message=insight.title,
                model_label="InsightItem",
                object_id=insight.pk,
            )
    return redirect("marketing_dashboard")


def insights_list(request):
    _require_enabled()

    qs = InsightItem.objects.all().order_by("-priority_score", "-created_at")
    status = (request.GET.get("status") or "").strip()
    platform = (request.GET.get("platform") or "").strip()
    priority = (request.GET.get("priority") or "").strip()

    if status:
        qs = qs.filter(status=status)
    if platform:
        qs = qs.filter(platform=platform)
    if priority.isdigit():
        qs = qs.filter(priority_score__gte=int(priority))

    if request.method == "POST":
        if not _can_edit_marketing(request.user):
            return HttpResponseForbidden("No access")

        action = (request.POST.get("action") or "").strip()
        insight_id = request.POST.get("insight_id")
        insight = InsightItem.objects.filter(pk=insight_id).first()
        if insight:
            if action in {"done", "snoozed", "open"}:
                insight.status = action
                insight.save(update_fields=["status", "updated_at"])
                log_marketing_activity(
                    user=request.user,
                    action=f"insight_{action}",
                    message=insight.title,
                    model_label="InsightItem",
                    object_id=insight.pk,
                )
            elif action == "note":
                note = (request.POST.get("note") or "").strip()
                insight.note = note
                insight.save(update_fields=["note", "updated_at"])
                log_marketing_activity(
                    user=request.user,
                    action="insight_note",
                    message=insight.title,
                    model_label="InsightItem",
                    object_id=insight.pk,
                )

        return redirect("marketing_insights")

    insight_rows = list(qs[:200])
    insight_groups = [
        ("High Priority", [item for item in insight_rows if item.priority_score >= 80]),
        ("Medium Priority", [item for item in insight_rows if 60 <= item.priority_score < 80]),
        ("Low Priority", [item for item in insight_rows if item.priority_score < 60]),
    ]

    return render(
        request,
        "marketing/insights_list.html",
        {
            "insights": insight_rows,
            "insight_groups": insight_groups,
            "platform_choices": SocialAccount.PLATFORM_CHOICES,
            "status": status,
            "platform": platform,
            "priority": priority,
            "can_edit": _can_edit_marketing(request.user),
        },
    )


def competitors_list(request):
    _require_enabled()

    competitors = MarketingCompetitor.objects.prefetch_related("accounts__posts").order_by("name")
    rows = []
    for competitor in competitors:
        accounts = list(competitor.accounts.all())
        posts = [post for account in accounts for post in account.posts.all()]
        avg_engagement = sum(float(post.engagement_rate or 0) for post in posts) / max(len(posts), 1) if posts else 0.0
        rows.append(
            {
                "competitor": competitor,
                "accounts_count": len(accounts),
                "posts_count": len(posts),
                "avg_engagement": avg_engagement,
            }
        )

    return render(
        request,
        "marketing/competitors_list.html",
        {"rows": rows},
    )


def competitor_add(request):
    _require_enabled()
    form = MarketingCompetitorForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        competitor = form.save()
        messages.success(request, "Competitor saved.")
        return redirect("marketing_competitor_detail", pk=competitor.pk)

    return render(
        request,
        "marketing/competitor_form.html",
        {"form": form, "page_title": "Add Competitor", "submit_label": "Save Competitor"},
    )


def competitor_edit(request, pk: int):
    _require_enabled()
    competitor = get_object_or_404(MarketingCompetitor, pk=pk)
    form = MarketingCompetitorForm(request.POST or None, instance=competitor)
    if request.method == "POST" and form.is_valid():
        competitor = form.save()
        messages.success(request, "Competitor updated.")
        return redirect("marketing_competitor_detail", pk=competitor.pk)

    return render(
        request,
        "marketing/competitor_form.html",
        {
            "form": form,
            "competitor": competitor,
            "page_title": "Edit Competitor",
            "submit_label": "Update Competitor",
        },
    )


def competitor_account_add(request, pk: int):
    _require_enabled()
    competitor = get_object_or_404(MarketingCompetitor, pk=pk)
    form = MarketingCompetitorAccountForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        account = form.save(commit=False)
        account.competitor = competitor
        account.save()
        messages.success(request, "Competitor account saved.")
        return redirect("marketing_competitor_detail", pk=competitor.pk)

    return render(
        request,
        "marketing/competitor_account_form.html",
        {
            "form": form,
            "competitor": competitor,
            "page_title": "Add Competitor Account",
            "submit_label": "Save Account",
        },
    )


def competitor_post_add(request, pk: int):
    _require_enabled()
    account = get_object_or_404(MarketingCompetitorAccount.objects.select_related("competitor"), pk=pk)
    form = MarketingCompetitorPostForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        post = form.save(commit=False)
        post.competitor_account = account
        post.save()
        messages.success(request, "Competitor post saved.")
        return redirect("marketing_competitor_detail", pk=account.competitor_id)

    return render(
        request,
        "marketing/competitor_post_form.html",
        {
            "form": form,
            "account": account,
            "competitor": account.competitor,
            "page_title": "Add Competitor Post",
            "submit_label": "Save Post",
        },
    )


def competitor_detail(request, pk: int):
    _require_enabled()
    competitor = get_object_or_404(
        MarketingCompetitor.objects.prefetch_related("accounts__posts", "insights"),
        pk=pk,
    )
    accounts = list(competitor.accounts.all())
    all_posts = [post for account in accounts for post in account.posts.all()]
    top_posts = sorted(
        all_posts,
        key=lambda post: (post.engagement_score, float(post.engagement_rate or 0), post.views),
        reverse=True,
    )[:5]
    weak_posts = sorted(
        [post for post in all_posts if post.views > 0],
        key=lambda post: (post.engagement_score, float(post.engagement_rate or 0), -post.views),
    )[:5]

    avg_engagement = sum(float(post.engagement_rate or 0) for post in all_posts) / max(len(all_posts), 1) if all_posts else 0.0
    total_followers = sum(account.followers_count for account in accounts)
    opportunities = _competitor_opportunities(competitor)

    return render(
        request,
        "marketing/competitor_detail.html",
        {
            "competitor": competitor,
            "accounts": accounts,
            "top_posts": top_posts,
            "weak_posts": weak_posts,
            "opportunities": opportunities,
            "total_followers": total_followers,
            "avg_engagement": avg_engagement,
            "stored_insights": competitor.insights.all()[:10],
        },
    )


def platform_detail(request, platform: str):
    _require_enabled("MARKETING_SOCIAL_ENABLED")

    platform_values = {p[0] for p in SocialAccount.PLATFORM_CHOICES}
    if platform not in platform_values:
        raise Http404("Unknown platform")

    accounts = SocialAccount.objects.filter(platform=platform, is_active=True)
    since = timezone.localdate() - timedelta(days=30)

    account_totals = AccountMetricDaily.objects.filter(account__platform=platform, date__gte=since).aggregate(
        followers_total=Sum("followers_total"),
        followers_change=Sum("followers_change"),
        impressions=Sum("impressions"),
        reach=Sum("reach"),
        views=Sum("views"),
        clicks=Sum("clicks"),
        engagement_total=Sum("engagement_total"),
    )

    start_date = parse_date(request.GET.get("start") or "") or since
    end_date = parse_date(request.GET.get("end") or "") or timezone.localdate()
    content_type = (request.GET.get("content_type") or "").strip()
    sort_by = (request.GET.get("sort") or "engagement").strip()

    content_qs = SocialContent.objects.filter(platform=platform)
    if content_type:
        content_qs = content_qs.filter(content_type=content_type)

    rows = _collect_content_metrics(content_qs, start_date=start_date, end_date=end_date)

    def _sort_key(row):
        if sort_by == "views":
            return row["views"]
        if sort_by == "clicks":
            return row["clicks"]
        if sort_by == "impressions":
            return row["impressions"]
        return row["engagement_rate"]

    rows = sorted(rows, key=_sort_key, reverse=True)

    audience_rows = []
    for acct in accounts:
        snapshot = SocialAudienceDaily.objects.filter(account=acct).order_by("-date").first()
        audience_rows.append({"account": acct, "snapshot": snapshot})

    day_scores = {}
    hour_scores = {}
    for row in rows:
        published_at = row["content"].published_at
        if not published_at:
            continue
        day = published_at.strftime("%A")
        hour = published_at.strftime("%H:00")
        day_scores[day] = day_scores.get(day, 0) + row["engagement_total"]
        hour_scores[hour] = hour_scores.get(hour, 0) + row["engagement_total"]

    best_days = sorted(day_scores.items(), key=lambda x: x[1], reverse=True)[:3]
    best_hours = sorted(hour_scores.items(), key=lambda x: x[1], reverse=True)[:3]

    context = {
        "platform": platform,
        "platform_label": dict(SocialAccount.PLATFORM_CHOICES).get(platform, platform),
        "accounts": accounts,
        "account_totals": account_totals,
        "audience_rows": audience_rows,
        "rows": rows[:50],
        "content_type_choices": SocialContent.CONTENT_CHOICES,
        "content_type": content_type,
        "sort_by": sort_by,
        "start_date": start_date,
        "end_date": end_date,
        "best_days": best_days,
        "best_hours": best_hours,
    }
    return render(request, "marketing/platform_detail.html", context)


def content_library(request):
    _require_enabled("MARKETING_SOCIAL_ENABLED")

    platform = (request.GET.get("platform") or "").strip()
    content_type = (request.GET.get("content_type") or "").strip()
    start_date = parse_date(request.GET.get("start") or "")
    end_date = parse_date(request.GET.get("end") or "")

    content_qs = SocialContent.objects.all()
    if platform:
        content_qs = content_qs.filter(platform=platform)
    if content_type:
        content_qs = content_qs.filter(content_type=content_type)
    if start_date:
        content_qs = content_qs.filter(published_at__date__gte=start_date)
    if end_date:
        content_qs = content_qs.filter(published_at__date__lte=end_date)

    rows = _collect_content_metrics(content_qs, start_date=start_date, end_date=end_date)

    rates = sorted([r["engagement_rate"] for r in rows]) if rows else [0]
    impressions = sorted([r["impressions"] for r in rows]) if rows else [0]
    top_quartile = rates[int(0.75 * (len(rates) - 1))] if rows else 0
    bottom_quartile = rates[int(0.25 * (len(rates) - 1))] if rows else 0
    median_impressions = impressions[len(impressions) // 2] if rows else 0

    for row in rows:
        row["is_winner"] = row["engagement_rate"] >= top_quartile and row["impressions"] >= median_impressions
        row["is_weak"] = row["engagement_rate"] <= bottom_quartile and row["impressions"] >= median_impressions

    rows = sorted(rows, key=lambda r: r["engagement_rate"], reverse=True)

    return render(
        request,
        "marketing/content_list.html",
        {
            "rows": rows[:200],
            "platform_choices": SocialAccount.PLATFORM_CHOICES,
            "content_type_choices": SocialContent.CONTENT_CHOICES,
            "platform": platform,
            "content_type": content_type,
            "start_date": start_date,
            "end_date": end_date,
        },
    )


def content_detail(request, pk: int):
    _require_enabled("MARKETING_SOCIAL_ENABLED")
    content = get_object_or_404(SocialContent, pk=pk)
    metrics = SocialMetricDaily.objects.filter(content=content).order_by("date")

    labels = [m.date.isoformat() for m in metrics]
    views = [m.views for m in metrics]
    clicks = [m.clicks for m in metrics]
    engagement = [
        calc_engagement_total(likes=m.likes, comments=m.comments, shares=m.shares, saves=m.saves)
        for m in metrics
    ]

    return render(
        request,
        "marketing/content_detail.html",
        {
            "content": content,
            "metrics": metrics,
            "labels_json": json.dumps(labels),
            "views_json": json.dumps(views),
            "clicks_json": json.dumps(clicks),
            "engagement_json": json.dumps(engagement),
        },
    )


def ads_overview(request):
    _require_enabled("MARKETING_ADS_ENABLED")

    since = timezone.localdate() - timedelta(days=30)
    daily_rows = (
        AdMetricDaily.objects.filter(date__gte=since)
        .values("date")
        .annotate(
            spend=Sum("spend"),
            impressions=Sum("impressions"),
            clicks=Sum("clicks"),
            conversions=Sum("conversions"),
        )
        .order_by("date")
    )

    chart_labels = []
    chart_spend = []
    chart_conversions = []
    for row in daily_rows:
        chart_labels.append(row["date"].isoformat())
        chart_spend.append(float(row["spend"] or 0))
        chart_conversions.append(int(row["conversions"] or 0))

    campaign_rows = (
        AdCampaign.objects.all()
        .annotate(
            spend=Coalesce(Sum("daily_metrics__spend", filter=Q(daily_metrics__date__gte=since)), 0),
            impressions=Coalesce(Sum("daily_metrics__impressions", filter=Q(daily_metrics__date__gte=since)), 0),
            clicks=Coalesce(Sum("daily_metrics__clicks", filter=Q(daily_metrics__date__gte=since)), 0),
            conversions=Coalesce(Sum("daily_metrics__conversions", filter=Q(daily_metrics__date__gte=since)), 0),
        )
        .select_related("ad_account")
    )

    campaigns = []
    for c in campaign_rows:
        conversions = int(c.conversions or 0)
        spend = float(c.spend or 0)
        cost_per_conversion = (spend / conversions) if conversions else 0
        campaigns.append(
            {
                "campaign": c,
                "spend": spend,
                "impressions": int(c.impressions or 0),
                "clicks": int(c.clicks or 0),
                "conversions": conversions,
                "cost_per_conversion": cost_per_conversion,
            }
        )
    campaigns = sorted(campaigns, key=lambda x: (x["cost_per_conversion"] or 0, -x["conversions"]))[:20]

    return render(
        request,
        "marketing/ads_overview.html",
        {
            "chart_labels_json": json.dumps(chart_labels),
            "chart_spend_json": json.dumps(chart_spend),
            "chart_conversions_json": json.dumps(chart_conversions),
            "campaigns": campaigns,
        },
    )


def best_practices(request):
    _require_enabled()

    platform = (request.GET.get("platform") or "").strip()
    qs = BestPracticeLibrary.objects.all().order_by("-created_at")
    if platform:
        qs = qs.filter(platform=platform)

    if request.method == "POST":
        if not _can_edit_marketing(request.user):
            return HttpResponseForbidden("No access")

        action = (request.POST.get("action") or "").strip()
        title = (request.POST.get("title") or "").strip()
        body = (request.POST.get("body") or "").strip()
        category = (request.POST.get("category") or "").strip() or "hooks"
        platform_val = (request.POST.get("platform") or "").strip()
        examples = (request.POST.get("examples") or "").strip()
        examples_list = [x.strip() for x in examples.splitlines() if x.strip()]

        if action == "update":
            practice_id = request.POST.get("practice_id")
            practice = BestPracticeLibrary.objects.filter(pk=practice_id).first()
            if practice:
                practice.title = title or practice.title
                practice.body = body or practice.body
                practice.category = category or practice.category
                practice.platform = platform_val or practice.platform
                practice.examples_json = examples_list
                practice.save()
                log_marketing_activity(
                    user=request.user,
                    action="best_practice_update",
                    message=practice.title,
                    model_label="BestPracticeLibrary",
                    object_id=practice.pk,
                )
        else:
            if title and platform_val:
                practice = BestPracticeLibrary.objects.create(
                    title=title,
                    body=body,
                    category=category,
                    platform=platform_val,
                    examples_json=examples_list,
                    created_by=request.user,
                )
                log_marketing_activity(
                    user=request.user,
                    action="best_practice_create",
                    message=practice.title,
                    model_label="BestPracticeLibrary",
                    object_id=practice.pk,
                )

        return redirect("marketing_best_practices")

    return render(
        request,
        "marketing/best_practices.html",
        {
            "practices": qs[:200],
            "platform_choices": SocialAccount.PLATFORM_CHOICES,
            "category_choices": BestPracticeLibrary.CATEGORY_CHOICES,
            "platform": platform,
            "can_edit": _can_edit_marketing(request.user),
        },
    )


def weekly_workflow(request):
    _require_enabled()
    return render(request, "marketing/workflow.html")


def seo_overview(request):
    _require_enabled("MARKETING_SEO_ENABLED")
    since = timezone.localdate() - timedelta(days=30)

    query_rows = (
        SeoQueryDaily.objects.filter(date__gte=since)
        .values("query")
        .annotate(clicks=Sum("clicks"), impressions=Sum("impressions"))
        .order_by("-impressions")[:50]
    )

    page_rows = (
        SeoPageDaily.objects.filter(date__gte=since)
        .values("page")
        .annotate(clicks=Sum("clicks"), impressions=Sum("impressions"))
        .order_by("-impressions")[:50]
    )

    return render(request, "marketing/seo_overview.html", {"query_rows": query_rows, "page_rows": page_rows})


def social_overview(request):
    _require_enabled("MARKETING_SOCIAL_ENABLED")

    top_content = (
        SocialContent.objects.all()
        .annotate(total_views=Sum("daily_metrics__views"))
        .order_by("-total_views")[:20]
    )

    return render(request, "marketing/social_overview.html", {"top_content": top_content})


def campaigns_list(request):
    _require_enabled()

    if request.method == "POST":
        form = CampaignForm(request.POST)
        if form.is_valid():
            campaign = form.save(commit=False)
            campaign.owner = request.user
            campaign.save()
            log_marketing_activity(
                user=request.user,
                action="campaign_create",
                message=campaign.name,
                model_label="Campaign",
                object_id=campaign.pk,
            )
            messages.success(request, "Campaign created.")
            return redirect("marketing_campaigns")
        messages.error(request, "Please fix the errors below.")
    else:
        form = CampaignForm()

    campaigns = Campaign.objects.all()

    return render(request, "marketing/campaigns_list.html", {"form": form, "campaigns": campaigns})


def campaign_detail(request, pk: int):
    _require_enabled()

    campaign = get_object_or_404(Campaign, pk=pk)

    if request.method == "POST":
        link_form = TrackedLinkForm(request.POST)
        if link_form.is_valid():
            link = link_form.save(commit=False)
            link.campaign = campaign
            link.save()
            messages.success(request, "Tracked link saved.")
            return redirect("marketing_campaign_detail", pk=campaign.pk)
        messages.error(request, "Fix errors in tracked link form.")
    else:
        link_form = TrackedLinkForm()

    utm_values = [v for v in campaign.links.values_list("utm_campaign", flat=True) if v]
    lead_qs = Lead.objects.filter(utm_campaign__in=utm_values) if utm_values else Lead.objects.none()
    opp_qs = Opportunity.objects.filter(lead__in=lead_qs) if utm_values else Opportunity.objects.none()

    context = {
        "campaign": campaign,
        "links": campaign.links.all(),
        "link_form": link_form,
        "leads_count": lead_qs.count(),
        "opps_count": opp_qs.count(),
    }
    return render(request, "marketing/campaign_detail.html", context)


def outreach_dashboard(request):
    _require_enabled("MARKETING_OUTREACH_ENABLED")

    list_form = ContactListForm()
    upload_form = CSVUploadForm()
    campaign_form = OutreachCampaignForm()
    template_form = OutreachMessageTemplateForm()

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create_list":
            list_form = ContactListForm(request.POST)
            if list_form.is_valid():
                obj = list_form.save(commit=False)
                obj.created_by = request.user
                obj.save()
                log_marketing_activity(
                    user=request.user,
                    action="contact_list_create",
                    message=obj.name,
                    model_label="ContactList",
                    object_id=obj.pk,
                )
                messages.success(request, "Contact list created.")
                return redirect("marketing_outreach")
            messages.error(request, "Fix errors in list form.")

        elif action == "import_csv":
            upload_form = CSVUploadForm(request.POST, request.FILES)
            if upload_form.is_valid():
                contact_list = upload_form.cleaned_data.get("contact_list")
                stats = import_contacts_from_csv(upload_form.cleaned_data["csv_file"], contact_list=contact_list)
                log_marketing_activity(
                    user=request.user,
                    action="contacts_import",
                    message=f"Created {stats['created']} Updated {stats['updated']}",
                    model_label="Contact",
                )
                messages.success(
                    request,
                    f"Import done. Created {stats['created']} | Updated {stats['updated']} | Skipped {stats['skipped']}",
                )
                return redirect("marketing_outreach")
            messages.error(request, "Upload failed. Check the file.")

        elif action == "create_campaign":
            campaign_form = OutreachCampaignForm(request.POST)
            if campaign_form.is_valid():
                obj = campaign_form.save(commit=False)
                obj.created_by = request.user
                obj.save()
                seed_default_templates(obj)
                log_marketing_activity(
                    user=request.user,
                    action="outreach_campaign_create",
                    message=obj.name,
                    model_label="OutreachCampaign",
                    object_id=obj.pk,
                )
                messages.success(request, "Outreach campaign created.")
                return redirect("marketing_outreach")
            messages.error(request, "Fix errors in campaign form.")

        elif action == "create_template":
            template_form = OutreachMessageTemplateForm(request.POST)
            campaign_id = request.POST.get("campaign_id")
            campaign = OutreachCampaign.objects.filter(pk=campaign_id).first()
            if template_form.is_valid() and campaign:
                obj = template_form.save(commit=False)
                obj.campaign = campaign
                obj.save()
                log_marketing_activity(
                    user=request.user,
                    action="outreach_template_create",
                    message=obj.subject_template,
                    model_label="OutreachMessageTemplate",
                    object_id=obj.pk,
                )
                messages.success(request, "Template saved.")
                return redirect("marketing_outreach")
            messages.error(request, "Fix errors in template form.")

        elif action == "update_campaign_status":
            campaign_id = request.POST.get("campaign_id")
            status = request.POST.get("status")
            campaign = OutreachCampaign.objects.filter(pk=campaign_id).first()
            if campaign and status in {"draft", "active", "paused", "completed"}:
                campaign.status = status
                campaign.save(update_fields=["status"])
                log_marketing_activity(
                    user=request.user,
                    action="outreach_campaign_status",
                    message=f"{campaign.name} -> {status}",
                    model_label="OutreachCampaign",
                    object_id=campaign.pk,
                )
                messages.success(request, "Campaign status updated.")
                return redirect("marketing_outreach")

    lists = ContactList.objects.all().order_by("-created_at")
    campaigns = OutreachCampaign.objects.all().order_by("-created_at")
    recent_sends = OutreachSendLog.objects.select_related("campaign", "contact").order_by("-queued_at")[:20]

    return render(
        request,
        "marketing/outreach_dashboard.html",
        {
            "list_form": list_form,
            "upload_form": upload_form,
            "campaign_form": campaign_form,
            "template_form": template_form,
            "lists": lists,
            "campaigns": campaigns,
            "recent_sends": recent_sends,
        },
    )


def calls_queue(request):
    _require_enabled("MARKETING_OUTREACH_ENABLED")

    if request.method == "POST":
        task_id = request.POST.get("task_id")
        status = request.POST.get("status")
        notes = request.POST.get("notes", "")
        if task_id and status:
            from marketing.models import CallTask
            task = CallTask.objects.filter(pk=task_id).first()
            if task:
                task.status = status
                task.notes = notes
                if status in {"callback", "meeting_booked", "interested"}:
                    task.next_call_at = timezone.now() + timedelta(days=1)
                task.save()
                messages.success(request, "Call outcome saved.")
        return redirect("marketing_calls")

    from marketing.models import CallTask

    tasks = CallTask.objects.select_related("contact", "campaign").order_by("-priority_score", "created_at")[:100]
    call_script = (
        "Opening: Hi {first_name}, this is Refat from Iconic Apparel House. Did I catch you at a bad time?\\n"
        "Core pitch: Canadian owned, ethical production in Bangladesh. We can share MOQ, pricing, and timelines.\\n"
        "Qualify: What product are you working on? Target quantity? Deadline? Do you have a tech pack or reference photo?\\n"
        "Close: I can email a pricing outline or book a quick call."
    )
    return render(request, "marketing/calls_queue.html", {"tasks": tasks, "call_script": call_script})


def unsubscribe(request, token):
    contact = Contact.objects.filter(unsubscribe_token=token).first()
    if contact:
        contact.consent_status = "opted_out"
        contact.do_not_contact = True
        contact.save(update_fields=["consent_status", "do_not_contact"])
        UnsubscribeEvent.objects.get_or_create(contact=contact, channel="email")
    return render(request, "marketing/unsubscribe.html")
