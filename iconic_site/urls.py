from django.contrib import admin
from django.contrib.auth.decorators import login_required
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.shortcuts import redirect
from marketing import views_social_connections as marketing_social_views

urlpatterns = [
    path("admin/", admin.site.urls),

    # login logout
    path("accounts/", include("django.contrib.auth.urls")),

    path("marketing/", include("marketing.urls")),
    path(
        "api/auth/google/start",
        login_required(marketing_social_views.google_oauth_start),
        name="marketing_google_oauth_start_api",
    ),
    path(
        "api/auth/google/start/",
        login_required(marketing_social_views.google_oauth_start),
        name="marketing_google_oauth_start_api_slash",
    ),
    path(
        "api/auth/google/callback",
        login_required(marketing_social_views.google_oauth_callback),
        name="marketing_google_oauth_callback_api",
    ),
    path(
        "api/auth/google/callback/",
        login_required(marketing_social_views.google_oauth_callback),
        name="marketing_google_oauth_callback_api_slash",
    ),
    path(
        "api/auth/google/callback/test",
        login_required(marketing_social_views.google_oauth_callback_test),
        name="marketing_google_oauth_callback_test",
    ),
    path(
        "api/auth/meta/start",
        login_required(marketing_social_views.oauth_start),
        {"platform": "meta"},
        name="marketing_meta_oauth_start_api",
    ),
    path(
        "api/auth/meta/start/",
        login_required(marketing_social_views.oauth_start),
        {"platform": "meta"},
        name="marketing_meta_oauth_start_api_slash",
    ),
    path(
        "api/auth/meta/callback",
        login_required(marketing_social_views.oauth_callback),
        {"platform": "meta"},
        name="marketing_meta_oauth_callback_api",
    ),
    path(
        "api/auth/meta/callback/",
        login_required(marketing_social_views.oauth_callback),
        {"platform": "meta"},
        name="marketing_meta_oauth_callback_api_slash",
    ),
    path(
        "api/auth/linkedin/start",
        login_required(marketing_social_views.oauth_start),
        {"platform": "linkedin"},
        name="marketing_linkedin_oauth_start_api",
    ),
    path(
        "api/auth/linkedin/start/",
        login_required(marketing_social_views.oauth_start),
        {"platform": "linkedin"},
        name="marketing_linkedin_oauth_start_api_slash",
    ),
    path(
        "api/auth/linkedin/callback",
        login_required(marketing_social_views.oauth_callback),
        {"platform": "linkedin"},
        name="marketing_linkedin_oauth_callback_api",
    ),
    path(
        "api/auth/linkedin/callback/",
        login_required(marketing_social_views.oauth_callback),
        {"platform": "linkedin"},
        name="marketing_linkedin_oauth_callback_api_slash",
    ),
    path(
        "api/auth/tiktok/start",
        login_required(marketing_social_views.oauth_start),
        {"platform": "tiktok"},
        name="marketing_tiktok_oauth_start_api",
    ),
    path(
        "api/auth/tiktok/start/",
        login_required(marketing_social_views.oauth_start),
        {"platform": "tiktok"},
        name="marketing_tiktok_oauth_start_api_slash",
    ),
    path(
        "api/auth/tiktok/callback",
        login_required(marketing_social_views.oauth_callback),
        {"platform": "tiktok"},
        name="marketing_tiktok_oauth_callback_api",
    ),
    path(
        "api/auth/tiktok/callback/",
        login_required(marketing_social_views.oauth_callback),
        {"platform": "tiktok"},
        name="marketing_tiktok_oauth_callback_api_slash",
    ),
    path("whatsapp/", include("whatsapp.urls")),

    # your crm app
    path("", include("crm.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
