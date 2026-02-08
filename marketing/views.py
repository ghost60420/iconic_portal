from datetime import timedelta
import json

from django.conf import settings
from django.contrib import messages
from django.db.models import Count, Sum, Q
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
)
from marketing.services.metrics import calc_engagement_total, calc_engagement_rate
from marketing.utils.importer import import_contacts_from_csv
from marketing.utils.activity import log_marketing_activity
from marketing.utils.templates import seed_default_templates


def _require_enabled(flag_name: str | None = None):
    if not getattr(settings, "MARKETING_ENABLED", False):
        raise Http404("Marketing disabled")
    if flag_name and not getattr(settings, flag_name, False):
        raise Http404("Marketing feature disabled")


def _metric_totals(days: int):
    since = timezone.localdate() - timedelta(days=days)
    totals = SocialMetricDaily.objects.filter(date__gte=since).aggregate(
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
    engagement_total = calc_engagement_total(
        likes=totals.get("likes") or 0,
        comments=totals.get("comments") or 0,
        shares=totals.get("shares") or 0,
        saves=totals.get("saves") or 0,
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
                "engagement_total": engagement_total,
                "engagement_rate": engagement_rate,
            }
        )
    return rows


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

    kpi_7 = _metric_totals(7)
    kpi_30 = _metric_totals(30)

    since_30 = timezone.localdate() - timedelta(days=30)
    content_rows = _collect_content_metrics(SocialContent.objects.all(), start_date=since_30)
    top_by_engagement = sorted(content_rows, key=lambda r: r["engagement_rate"], reverse=True)[:10]
    top_by_views = sorted(content_rows, key=lambda r: r["views"], reverse=True)[:10]
    top_by_clicks = sorted(content_rows, key=lambda r: r["clicks"], reverse=True)[:10]

    labels, series, platform_labels = _followers_growth(30)

    insights = InsightItem.objects.filter(status="open").order_by("-priority_score", "-created_at")[:8]

    context = {
        "kpi_7": kpi_7,
        "kpi_30": kpi_30,
        "top_by_engagement": top_by_engagement,
        "top_by_views": top_by_views,
        "top_by_clicks": top_by_clicks,
        "growth_labels_json": json.dumps([d.isoformat() for d in labels]),
        "growth_series_json": json.dumps(series),
        "platform_labels_json": json.dumps(platform_labels),
        "insights": insights,
        "marketing_leads": Lead.objects.exclude(utm_source="").count(),
        "marketing_opps": Opportunity.objects.filter(lead__utm_source__isnull=False).exclude(lead__utm_source="").count(),
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

    return render(
        request,
        "marketing/insights_list.html",
        {
            "insights": qs[:200],
            "platform_choices": SocialAccount.PLATFORM_CHOICES,
            "status": status,
            "platform": platform,
            "priority": priority,
            "can_edit": _can_edit_marketing(request.user),
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
