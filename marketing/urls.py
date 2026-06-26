from django.urls import path
from django.contrib.auth.decorators import login_required

from crm.permissions import require_access
from . import views
from . import views_social_connections


def perm(view_func):
    def _wrapped(request, *args, **kwargs):
        user = request.user
        if user.is_authenticated and (user.is_superuser or user.groups.filter(name="Marketing Manager").exists()):
            return view_func(request, *args, **kwargs)
        return require_access("can_marketing")(view_func)(request, *args, **kwargs)

    return login_required(_wrapped)


urlpatterns = [
    path("", perm(views.marketing_home), name="marketing_home"),
    path("dashboard/", perm(views.dashboard), name="marketing_dashboard"),
    path("connect/", perm(views_social_connections.social_connections), name="marketing_connect"),
    path(
        "social/connections/",
        perm(views_social_connections.social_connections),
        name="marketing_social_connections",
    ),
    path(
        "connection-diagnostics/",
        perm(views_social_connections.connection_diagnostics),
        name="marketing_connection_diagnostics",
    ),
    path(
        "social/connections/<int:pk>/sync/",
        perm(views_social_connections.social_connection_sync),
        name="marketing_social_connection_sync",
    ),
    path(
        "social/connections/<int:pk>/disconnect/",
        perm(views_social_connections.social_connection_disconnect),
        name="marketing_social_connection_disconnect",
    ),
    path(
        "social/connections/<str:platform>/sync/",
        perm(views_social_connections.social_platform_sync),
        name="marketing_social_platform_sync",
    ),
    path("oauth/<str:platform>/start/", perm(views_social_connections.oauth_start), name="marketing_oauth_start"),
    path("oauth/<str:platform>/callback/", perm(views_social_connections.oauth_callback), name="marketing_oauth_callback"),
    path("oauth/meta/start/", perm(views.meta_oauth_start), name="marketing_meta_oauth_start"),
    path("oauth/meta/callback/", perm(views.meta_oauth_callback), name="marketing_meta_oauth_callback"),
    path("oauth/google/start/", perm(views_social_connections.google_oauth_start), name="marketing_google_oauth_start"),
    path("oauth/google/callback/", perm(views_social_connections.google_oauth_callback), name="marketing_google_oauth_callback"),
    path("google/properties/refresh/", perm(views_social_connections.google_discover_properties), name="marketing_google_discover_properties"),
    path("platform/<str:platform>/", perm(views.platform_detail), name="marketing_platform"),
    path("content/", perm(views.content_library), name="marketing_content"),
    path("content/<int:pk>/", perm(views.content_detail), name="marketing_content_detail"),
    path("ads/", perm(views.ads_overview), name="marketing_ads"),
    path("best-practices/", perm(views.best_practices), name="marketing_best_practices"),
    path("insights/", perm(views.insights_list), name="marketing_insights"),
    path("ai-insights/", perm(views.insights_list), name="marketing_ai_insights"),
    path("insights/<int:pk>/", perm(views.insight_update), name="marketing_insight_update"),
    path("workflow/", perm(views.weekly_workflow), name="marketing_workflow"),
    path("website/", perm(views.website_analytics), name="marketing_website"),
    path("website-analytics/", perm(views.website_analytics), name="marketing_website_analytics"),
    path("google-search/", perm(views.google_search_performance), name="marketing_google_search"),
    path("seo/", perm(views.seo_overview), name="marketing_seo"),
    path("social/", perm(views.social_overview), name="marketing_social"),
    path("campaigns/", perm(views.campaigns_list), name="marketing_campaigns"),
    path("campaign-performance/", perm(views.campaigns_list), name="marketing_campaign_performance"),
    path("connection-settings/", perm(views_social_connections.social_connections), name="marketing_connection_settings"),
    path("campaigns/<int:pk>/", perm(views.campaign_detail), name="marketing_campaign_detail"),
    path("competitors/", perm(views.competitors_list), name="marketing_competitors"),
    path("competitors/add/", perm(views.competitor_add), name="marketing_competitor_add"),
    path("competitors/<int:pk>/", perm(views.competitor_detail), name="marketing_competitor_detail"),
    path("competitors/<int:pk>/edit/", perm(views.competitor_edit), name="marketing_competitor_edit"),
    path("competitors/<int:pk>/accounts/add/", perm(views.competitor_account_add), name="marketing_competitor_account_add"),
    path("competitor-accounts/<int:pk>/posts/add/", perm(views.competitor_post_add), name="marketing_competitor_post_add"),
    path("outreach/", perm(views.outreach_dashboard), name="marketing_outreach"),
    path("calls/", perm(views.calls_queue), name="marketing_calls"),
    path("unsubscribe/<uuid:token>/", views.unsubscribe, name="marketing_unsubscribe"),
]
