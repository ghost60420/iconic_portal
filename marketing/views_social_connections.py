from django.conf import settings
from django.contrib import messages
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.http import Http404, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST
from datetime import timedelta
from decimal import Decimal
import uuid

from marketing.forms_social_connections import MarketingSocialConnectionForm
from crm.models import SystemActivityLog
from marketing.models import (
    AdAccount,
    AdMetricDaily,
    AccountMetricDaily,
    OAuthCredential,
    OAuthConnectionRequest,
    SeoProperty,
    SeoQueryDaily,
    SocialAccount,
    SocialContent,
    WebsiteTrafficDaily,
)
from marketing.services.errors import MarketingServiceError
from marketing.services.ga4_default import (
    ga4_property_queryset,
    ga4_reporting_queryset,
    get_default_ga4_property,
    set_default_ga4_property,
)
from marketing.services.google_oauth import (
    exchange_code_for_tokens,
    get_valid_access_token,
    google_oauth_configured,
    sync_google_properties,
)
from marketing.services.oauth_connections import (
    GOOGLE_OAUTH_PLATFORMS,
    META_OAUTH_PLATFORMS,
    build_oauth_authorization_url,
    complete_google_oauth,
    complete_meta_oauth_request,
    exchange_direct_oauth_code,
    meta_scope_modes,
    normalize_oauth_platform,
)
from marketing.services.social_connections import (
    SOCIAL_CONNECTION_CONFIG,
    SOCIAL_CONNECTION_PLATFORM_KEYS,
    build_connection_cards,
    run_social_platform_sync,
    run_social_connection_sync,
    save_social_connection,
    social_connection_queryset,
)


def _require_enabled():
    if not getattr(settings, "MARKETING_ENABLED", False):
        raise Http404("Marketing disabled")


def _editable_connection_from_request(request):
    connection_id = request.POST.get("connection_id") or request.GET.get("edit")
    if not connection_id:
        return None
    return social_connection_queryset().filter(pk=connection_id).first()


def _can_connect_marketing_oauth(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or user.groups.filter(name="Marketing Manager").exists():
        return True
    access = getattr(user, "access", None)
    return bool(access and access.can_marketing)


def _google_connection_status():
    credential = OAuthCredential.objects.filter(platform="google", is_active=True).order_by("-updated_at").first()
    if not credential:
        return {
            "label": "Reconnect Required",
            "tone": "warn",
            "message": "No Google account is connected.",
            "credential": None,
        }

    last_error = (credential.last_error or "").lower()
    if credential.last_sync_status == "error" and ("refresh" in last_error or "reconnect" in last_error):
        return {
            "label": "Reconnect Required",
            "tone": "warn",
            "message": credential.last_error or "Google token refresh failed.",
            "credential": credential,
        }

    if credential.expires_at and credential.expires_at <= timezone.now():
        return {
            "label": "Token Expired",
            "tone": "warn",
            "message": "The saved access token is expired. The next sync will refresh it when a refresh token is available.",
            "credential": credential,
        }

    if credential.has_access_token:
        return {
            "label": "Google Connected",
            "tone": "good",
            "message": "Google OAuth is connected and tokens are encrypted at rest.",
            "credential": credential,
        }

    return {
        "label": "Reconnect Required",
        "tone": "warn",
        "message": "The Google connection is missing an access token.",
        "credential": credential,
    }


def _credential_status(credential: OAuthCredential | None, api_error: str = "") -> dict:
    if api_error:
        return {"label": "API Error", "tone": "warn"}
    if not credential:
        return {"label": "Reconnect Required", "tone": "warn"}
    if credential.last_sync_status == "error" or credential.last_error:
        return {"label": "API Error", "tone": "warn"}
    if credential.expires_at and credential.expires_at <= timezone.now():
        return {"label": "Token Expired", "tone": "warn"}
    if credential.has_access_token:
        return {"label": "Connected", "tone": "good"}
    return {"label": "Reconnect Required", "tone": "warn"}


def _latest_account_metrics(platform: str) -> dict:
    accounts = list(SocialAccount.objects.filter(platform=platform, is_active=True))
    latest_rows = []
    for account in accounts:
        latest = account.account_days.order_by("-date").first()
        if latest:
            latest_rows.append(latest)
    return {
        "accounts": len(accounts),
        "followers_total": sum(row.followers_total or 0 for row in latest_rows),
        "views": sum(row.views or 0 for row in latest_rows),
        "videos": sum(row.engagement_total or 0 for row in latest_rows),
    }


def _diagnostic_metric_snapshot(platform: str) -> list[dict]:
    since = timezone.localdate() - timedelta(days=30)
    if platform == "ga4":
        totals = WebsiteTrafficDaily.objects.filter(date__gte=since, property__in=ga4_reporting_queryset()).aggregate(
            users=Coalesce(Sum("visitors"), 0),
            sessions=Coalesce(Sum("sessions"), 0),
            pageviews=Coalesce(Sum("page_views"), 0),
        )
        return [
            {"label": "Users", "value": totals["users"]},
            {"label": "Sessions", "value": totals["sessions"]},
            {"label": "Pageviews", "value": totals["pageviews"]},
        ]
    if platform == "gsc":
        totals = SeoQueryDaily.objects.filter(date__gte=since).aggregate(
            clicks=Coalesce(Sum("clicks"), 0),
            impressions=Coalesce(Sum("impressions"), 0),
        )
        top_keyword = (
            SeoQueryDaily.objects.filter(date__gte=since)
            .exclude(query="")
            .values("query")
            .annotate(clicks=Coalesce(Sum("clicks"), 0), impressions=Coalesce(Sum("impressions"), 0))
            .order_by("-clicks", "-impressions")
            .first()
        )
        return [
            {"label": "Clicks", "value": totals["clicks"]},
            {"label": "Impressions", "value": totals["impressions"]},
            {"label": "Top Keyword", "value": (top_keyword or {}).get("query") or "-"},
        ]
    if platform == "youtube":
        totals = _latest_account_metrics("youtube")
        return [
            {"label": "Subscribers", "value": totals["followers_total"]},
            {"label": "Views", "value": totals["views"]},
            {"label": "Videos", "value": totals["videos"]},
        ]
    if platform == "google_business":
        totals = AccountMetricDaily.objects.filter(account__platform="google_business", date__gte=since).aggregate(
            calls=Coalesce(Sum("engagement_total"), 0),
            website_clicks=Coalesce(Sum("clicks"), 0),
            direction_requests=Coalesce(Sum("reach"), 0),
            profile_views=Coalesce(Sum("impressions"), 0),
        )
        return [
            {"label": "Calls", "value": totals["calls"]},
            {"label": "Website Clicks", "value": totals["website_clicks"]},
            {"label": "Directions", "value": totals["direction_requests"]},
            {"label": "Profile Views", "value": totals["profile_views"]},
        ]
    if platform == "meta_ads":
        totals = AdMetricDaily.objects.filter(date__gte=since).aggregate(
            spend=Coalesce(Sum("spend"), Decimal("0")),
            impressions=Coalesce(Sum("impressions"), 0),
            clicks=Coalesce(Sum("clicks"), 0),
            conversions=Coalesce(Sum("conversions"), 0),
        )
        return [
            {"label": "Ad Accounts", "value": AdAccount.objects.filter(is_active=True).count()},
            {"label": "Spend", "value": f"{float(totals['spend'] or 0):,.2f}"},
            {"label": "Impressions", "value": totals["impressions"]},
            {"label": "Clicks", "value": totals["clicks"]},
            {"label": "Conversions", "value": totals["conversions"]},
        ]
    totals = AccountMetricDaily.objects.filter(account__platform=platform, date__gte=since).aggregate(
        impressions=Coalesce(Sum("impressions"), 0),
        reach=Coalesce(Sum("reach"), 0),
        clicks=Coalesce(Sum("clicks"), 0),
    )
    return [
        {"label": "Accounts", "value": SocialAccount.objects.filter(platform=platform, is_active=True).count()},
        {"label": "Posts", "value": SocialContent.objects.filter(platform=platform).count()},
        {"label": "Impressions", "value": totals["impressions"]},
        {"label": "Reach", "value": totals["reach"]},
        {"label": "Clicks", "value": totals["clicks"]},
    ]


def _google_api_error_for(platform: str) -> str:
    if platform == "meta_ads":
        credential = OAuthCredential.objects.filter(platform="meta", last_sync_status="error").order_by("-updated_at").first()
        return credential.last_error if credential else ""
    if platform in {"ga4", "gsc"}:
        filters = {"last_sync_status": "error"}
        if platform == "ga4":
            filters["ga4_property_id__gt"] = ""
        else:
            filters["gsc_site_url__gt"] = ""
        prop = SeoProperty.objects.filter(**filters).exclude(last_sync_message="").order_by("-updated_at").first()
        return prop.last_sync_message if prop else ""
    account = SocialAccount.objects.filter(platform=platform).exclude(last_sync_message="").order_by("-updated_at").first()
    return account.last_sync_message if account else ""


def _last_sync_for(platform: str, credential: OAuthCredential | None):
    if platform in {"ga4", "gsc"}:
        qs = SeoProperty.objects.exclude(last_sync_at=None)
        if platform == "ga4":
            qs = qs.exclude(ga4_property_id="")
        else:
            qs = qs.exclude(gsc_site_url="")
        prop = qs.order_by("-last_sync_at").first()
        return prop.last_sync_at if prop else (credential.last_synced_at if credential else None)
    account = SocialAccount.objects.filter(platform=platform).exclude(last_successful_sync=None).order_by("-last_successful_sync").first()
    return account.last_successful_sync if account else (credential.last_synced_at if credential else None)


def _diagnostic_rows():
    google_credential = OAuthCredential.objects.filter(platform="google", is_active=True).order_by("-updated_at").first()
    rows = []
    if google_credential:
        rows.append(
            {
                "platform": "Google OAuth",
                "key": "google",
                "status": _credential_status(google_credential),
                "account_email": google_credential.account_name,
                "last_sync": google_credential.last_synced_at,
                "token_expiry": google_credential.expires_at,
                "api_error": google_credential.last_error,
                "metrics": [],
                "sync_url": reverse("marketing_social_connection_sync", args=[google_credential.pk]),
            }
        )

    credentials = {item.platform: item for item in social_connection_queryset().order_by("-updated_at")}
    for config in SOCIAL_CONNECTION_CONFIG:
        storage_platform = "google" if config["provider"] == "google" else ("meta" if config["provider"] == "meta" else config["key"])
        credential = credentials.get(storage_platform)
        if config["provider"] == "google":
            credential = google_credential or credential
        api_error = _google_api_error_for(config["key"])
        rows.append(
            {
                "platform": config["label"],
                "key": config["key"],
                "status": _credential_status(credential, api_error=api_error),
                "account_email": credential.account_name if credential else "",
                "last_sync": _last_sync_for(config["key"], credential),
                "token_expiry": credential.expires_at if credential else None,
                "api_error": api_error or (credential.last_error if credential else ""),
                "metrics": _diagnostic_metric_snapshot(config["key"]),
                "sync_url": reverse("marketing_social_platform_sync", args=[config["key"]]),
            }
        )
    return rows


@require_http_methods(["GET", "POST"])
def social_connections(request):
    _require_enabled()

    editable = _editable_connection_from_request(request)
    selected_platform = request.GET.get("platform", "").strip()
    if selected_platform not in SOCIAL_CONNECTION_PLATFORM_KEYS:
        selected_platform = ""

    if request.method == "POST":
        if request.POST.get("form_name") == "default_ga4_property":
            if not _can_connect_marketing_oauth(request.user):
                return HttpResponseForbidden("Only marketing admins can update the default GA4 property.")
            try:
                prop = set_default_ga4_property(request.POST.get("default_ga4_property_id", ""))
            except SeoProperty.DoesNotExist:
                messages.error(request, "Select a discovered GA4 property before saving.")
            else:
                messages.success(request, f"Default GA4 property saved: {prop.name} ({prop.ga4_property_id}).")
            return redirect("marketing_connection_settings")

        form = MarketingSocialConnectionForm(request.POST, connection=editable)
        if form.is_valid():
            connection = save_social_connection(cleaned_data=form.cleaned_data, existing=editable)
            messages.success(
                request,
                f"{connection.get_platform_display()} connection saved. Tokens remain encrypted at rest.",
            )
            return redirect(f"{reverse('marketing_connect')}?edit={connection.pk}")
        messages.error(request, "Please fix the connection form errors below.")
    else:
        form = MarketingSocialConnectionForm(connection=editable, platform=selected_platform or None)

    connections = list(social_connection_queryset().order_by("platform", "-is_active", "account_name", "account_id"))
    cards = build_connection_cards()
    oauth_start_urls = {
        "facebook": reverse("marketing_meta_oauth_start_api_slash"),
        "instagram": reverse("marketing_meta_oauth_start_api_slash"),
        "meta_ads": reverse("marketing_meta_oauth_start_api_slash"),
        "linkedin": reverse("marketing_linkedin_oauth_start_api_slash"),
        "tiktok": reverse("marketing_tiktok_oauth_start_api_slash"),
    }
    for card in cards:
        card["oauth_start_url"] = oauth_start_urls.get(card["config"]["key"], "")
    meta_app_id = getattr(settings, "MARKETING_META_APP_ID", "")
    meta_app_secret = getattr(settings, "MARKETING_META_APP_SECRET", "")
    meta_redirect_uri = getattr(settings, "MARKETING_META_REDIRECT_URI", "")
    google_redirect_uri = getattr(settings, "MARKETING_GOOGLE_REDIRECT_URI", "")
    google_status = _google_connection_status()
    manual_setup_open = bool(request.method == "POST" or request.GET.get("manual_setup") == "1")
    ga4_properties = list(ga4_property_queryset())
    default_ga4_property = get_default_ga4_property()

    return render(
        request,
        "marketing/social_connections.html",
        {
            "cards": cards,
            "connections": connections,
            "form": form,
            "editable_connection": editable,
            "page_platforms": SOCIAL_CONNECTION_CONFIG,
            "meta_redirect_uri": meta_redirect_uri,
            "meta_configured": bool(meta_app_id and meta_app_secret and meta_redirect_uri),
            "google_redirect_uri": google_redirect_uri,
            "google_configured": google_oauth_configured(),
            "google_connection": google_status["credential"],
            "google_status": google_status,
            "google_oauth_start_url": reverse("marketing_google_oauth_start_api_slash"),
            "manual_setup_open": manual_setup_open,
            "seo_properties": SeoProperty.objects.filter(is_active=True).order_by("name"),
            "ga4_properties": ga4_properties,
            "default_ga4_property": default_ga4_property,
        },
    )


def connection_diagnostics(request):
    _require_enabled()
    recent_logs = SystemActivityLog.objects.filter(
        area="marketing",
        action="marketing_sync_failure",
    ).order_by("-created_at")[:30]
    return render(
        request,
        "marketing/connection_diagnostics.html",
        {
            "rows": _diagnostic_rows(),
            "recent_logs": recent_logs,
        },
    )


@require_POST
def social_connection_sync(request, pk: int):
    _require_enabled()
    connection = get_object_or_404(social_connection_queryset(), pk=pk)
    try:
        output = run_social_connection_sync(connection)
    except MarketingServiceError as exc:
        messages.error(request, f"{connection.get_platform_display()} sync failed: {exc}")
    else:
        if output:
            messages.success(request, f"{connection.get_platform_display()} sync complete. {output}")
        else:
            messages.success(request, f"{connection.get_platform_display()} sync complete.")
    return redirect(f"{reverse('marketing_connect')}?edit={connection.pk}")


@require_POST
def social_platform_sync(request, platform: str):
    _require_enabled()
    try:
        platform = normalize_oauth_platform(platform)
        output = run_social_platform_sync(platform)
    except MarketingServiceError as exc:
        messages.error(request, f"{platform} sync failed: {exc}")
    else:
        if output:
            messages.success(request, f"{platform} sync complete. {output}")
        else:
            messages.success(request, f"{platform} sync complete.")
    return redirect("marketing_connection_settings")


def oauth_start(request, platform: str):
    _require_enabled()
    if not _can_connect_marketing_oauth(request.user):
        return HttpResponseForbidden("Only marketing admins can connect OAuth accounts.")
    try:
        platform = normalize_oauth_platform(platform)
        request_platform = "meta" if platform in META_OAUTH_PLATFORMS else platform
        requested_scope_mode = request.GET.get("scope_mode")
        scope_mode = requested_scope_mode if requested_scope_mode in meta_scope_modes() else ""
        state = uuid.uuid4().hex
        conn = OAuthConnectionRequest.objects.create(
            platform=request_platform,
            user=request.user,
            state=state,
            status="initiated",
        )
        if scope_mode:
            conn.error_message = f"scope_mode={scope_mode}"
            conn.save(update_fields=["error_message", "updated_at"])
        return redirect(build_oauth_authorization_url(platform=platform, state=state, scope_mode=scope_mode))
    except MarketingServiceError as exc:
        messages.error(request, str(exc))
        return redirect("marketing_connection_settings")


def oauth_callback(request, platform: str):
    _require_enabled()
    try:
        platform = normalize_oauth_platform(platform)
        request_platform = "meta" if platform in META_OAUTH_PLATFORMS else platform
    except MarketingServiceError as exc:
        messages.error(request, str(exc))
        return redirect("marketing_connection_settings")

    state = request.GET.get("state", "")
    code = request.GET.get("code", "")
    error = request.GET.get("error_description") or request.GET.get("error")
    conn = OAuthConnectionRequest.objects.filter(platform=request_platform, state=state).first()
    if not conn:
        messages.error(request, "OAuth request not found.")
        return redirect("marketing_connection_settings")
    if conn.user and conn.user != request.user:
        conn.status = "error"
        conn.error_message = "User mismatch during OAuth callback."
        conn.save(update_fields=["status", "error_message", "updated_at"])
        messages.error(request, "OAuth user mismatch. Please try again.")
        return redirect("marketing_connection_settings")
    if error:
        conn.status = "error"
        conn.error_message = error
        conn.save(update_fields=["status", "error_message", "updated_at"])
        messages.error(request, f"{platform} authorization failed: {error}")
        return redirect("marketing_connection_settings")
    if not code:
        conn.status = "error"
        conn.error_message = "Missing authorization code."
        conn.save(update_fields=["status", "error_message", "updated_at"])
        messages.error(request, f"{platform} authorization failed: missing code.")
        return redirect("marketing_connection_settings")

    try:
        if platform in GOOGLE_OAUTH_PLATFORMS:
            token_payload = exchange_code_for_tokens(code)
            discovery = complete_google_oauth(conn=conn, token_payload=token_payload)
            credential = discovery["credential"]
            conn.code = code
            conn.save(update_fields=["code", "updated_at"])
            messages.success(
                request,
                (
                    "Google connected. "
                    f"GA4 properties: {discovery['ga4_count']} | "
                    f"Search Console sites: {discovery['gsc_count']} | "
                    f"YouTube channels: {discovery.get('youtube_count', 0)} | "
                    f"Business Profile locations: {discovery.get('google_business_count', 0)}."
                ),
            )
            return redirect("marketing_connection_settings")
        if platform in META_OAUTH_PLATFORMS:
            conn.code = code
            conn.status = "received"
            conn.error_message = ""
            conn.save(update_fields=["code", "status", "error_message", "updated_at"])
            result = complete_meta_oauth_request(conn)
            messages.success(
                request,
                (
                    "Meta connected. "
                    f"Facebook pages: {result['facebook_count']} | "
                    f"Instagram accounts: {result['instagram_count']} | "
                    f"Meta ad accounts: {result.get('meta_ads_count', 0)}."
                ),
            )
            return redirect("marketing_connection_settings")
        credential = exchange_direct_oauth_code(platform=platform, code=code)
    except MarketingServiceError as exc:
        conn.status = "error"
        conn.error_message = str(exc)
        conn.save(update_fields=["status", "error_message", "updated_at"])
        messages.error(request, f"{platform} connection failed: {exc}")
        return redirect("marketing_connection_settings")

    conn.code = code
    conn.status = "completed"
    conn.error_message = ""
    conn.save(update_fields=["code", "status", "error_message", "updated_at"])
    messages.success(request, f"{credential.get_platform_display()} connected with OAuth.")
    return redirect("marketing_connection_settings")


def google_oauth_start(request):
    _require_enabled()
    if not _can_connect_marketing_oauth(request.user):
        return HttpResponseForbidden("Only marketing admins can connect Google.")
    try:
        platform = normalize_oauth_platform(request.GET.get("platform") or "google")
        if platform not in GOOGLE_OAUTH_PLATFORMS:
            platform = "google"
        state = uuid.uuid4().hex
        OAuthConnectionRequest.objects.create(
            platform=platform,
            user=request.user,
            state=state,
            status="initiated",
        )
        return redirect(build_oauth_authorization_url(platform=platform, state=state))
    except MarketingServiceError as exc:
        messages.error(request, str(exc))
        return redirect("marketing_connection_settings")


def google_oauth_callback(request):
    _require_enabled()
    state = request.GET.get("state", "")
    code = request.GET.get("code", "")
    error = request.GET.get("error_description") or request.GET.get("error")

    if not state:
        messages.error(request, "Missing Google OAuth state.")
        return redirect("marketing_connection_settings")

    conn = OAuthConnectionRequest.objects.filter(platform__in=GOOGLE_OAUTH_PLATFORMS, state=state).first()
    if not conn:
        messages.error(request, "Google OAuth request not found.")
        return redirect("marketing_connection_settings")
    if conn.user and conn.user != request.user:
        conn.status = "error"
        conn.error_message = "User mismatch during Google OAuth callback."
        conn.save(update_fields=["status", "error_message", "updated_at"])
        messages.error(request, "Google OAuth user mismatch. Please try again.")
        return redirect("marketing_connection_settings")

    if error:
        conn.status = "error"
        conn.error_message = error
        conn.save(update_fields=["status", "error_message", "updated_at"])
        messages.error(request, f"Google authorization failed: {error}")
        return redirect("marketing_connection_settings")

    try:
        token_payload = exchange_code_for_tokens(code)
        discovery = complete_google_oauth(conn=conn, token_payload=token_payload)
        credential = discovery["credential"]
    except MarketingServiceError as exc:
        conn.status = "error"
        conn.error_message = str(exc)
        conn.save(update_fields=["status", "error_message", "updated_at"])
        messages.error(request, f"Google connection failed: {exc}")
        return redirect("marketing_connection_settings")

    conn.code = code
    conn.status = "completed"
    conn.error_message = ""
    conn.save(update_fields=["code", "status", "error_message", "updated_at"])
    messages.success(
        request,
        (
            "Google connected. "
            f"Found {discovery['ga4_count']} GA4 propertie(s), "
            f"{discovery['gsc_count']} Search Console site(s), "
            f"{discovery.get('youtube_count', 0)} YouTube channel(s), and "
            f"{discovery.get('google_business_count', 0)} Business Profile location(s)."
        ),
    )
    return redirect(f"{reverse('marketing_connection_settings')}?edit={credential.pk}")


def google_oauth_callback_test(request):
    _require_enabled()
    google_status = _google_connection_status()
    return JsonResponse(
        {
            "ok": True,
            "callback_route": "/api/auth/google/callback",
            "configured": google_oauth_configured(),
            "client_id_configured": bool(getattr(settings, "MARKETING_GOOGLE_CLIENT_ID", "")),
            "client_secret_configured": bool(getattr(settings, "MARKETING_GOOGLE_CLIENT_SECRET", "")),
            "redirect_uri": getattr(settings, "MARKETING_GOOGLE_REDIRECT_URI", ""),
            "requested_scopes": getattr(settings, "MARKETING_GOOGLE_SCOPES", []),
            "connection_status": google_status["label"],
        }
    )


@require_POST
def google_discover_properties(request):
    _require_enabled()
    credential = OAuthCredential.objects.filter(platform="google", is_active=True).order_by("-updated_at").first()
    if not credential:
        messages.error(request, "Connect Google before refreshing properties.")
        return redirect("marketing_connection_settings")
    try:
        get_valid_access_token(credential)
        discovery = sync_google_properties(credential=credential)
        messages.success(
            request,
            (
                "Google properties refreshed. "
                f"GA4: {discovery['ga4_count']} | "
                f"Search Console: {discovery['gsc_count']} | "
                f"YouTube: {discovery.get('youtube_count', 0)} | "
                f"Business Profile: {discovery.get('google_business_count', 0)}."
            ),
        )
    except MarketingServiceError as exc:
        messages.error(request, f"Google property refresh failed: {exc}")
    return redirect("marketing_connection_settings")
