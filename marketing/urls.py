from django.urls import path
from django.contrib.auth.decorators import login_required

from crm.permissions import require_access
from . import views


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
    path("connect/", perm(views.connect_accounts), name="marketing_connect"),
    path("oauth/meta/start/", perm(views.meta_oauth_start), name="marketing_meta_oauth_start"),
    path("oauth/meta/callback/", perm(views.meta_oauth_callback), name="marketing_meta_oauth_callback"),
    path("platform/<str:platform>/", perm(views.platform_detail), name="marketing_platform"),
    path("content/", perm(views.content_library), name="marketing_content"),
    path("content/<int:pk>/", perm(views.content_detail), name="marketing_content_detail"),
    path("ads/", perm(views.ads_overview), name="marketing_ads"),
    path("best-practices/", perm(views.best_practices), name="marketing_best_practices"),
    path("insights/", perm(views.insights_list), name="marketing_insights"),
    path("insights/<int:pk>/", perm(views.insight_update), name="marketing_insight_update"),
    path("workflow/", perm(views.weekly_workflow), name="marketing_workflow"),
    path("seo/", perm(views.seo_overview), name="marketing_seo"),
    path("social/", perm(views.social_overview), name="marketing_social"),
    path("campaigns/", perm(views.campaigns_list), name="marketing_campaigns"),
    path("campaigns/<int:pk>/", perm(views.campaign_detail), name="marketing_campaign_detail"),
    path("outreach/", perm(views.outreach_dashboard), name="marketing_outreach"),
    path("calls/", perm(views.calls_queue), name="marketing_calls"),
    path("unsubscribe/<uuid:token>/", views.unsubscribe, name="marketing_unsubscribe"),
]
