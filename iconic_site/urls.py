from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from crm.views_auth import DashboardLoginView


urlpatterns = [
    # Admin
    path("admin/", admin.site.urls),

    # Custom login that always redirects to dashboard
    path("accounts/login/", DashboardLoginView.as_view(), name="login"),

    # Django built in auth urls (logout, password reset, etc)
    path("accounts/", include("django.contrib.auth.urls")),

    # CRM app
    path("", include("crm.urls")),
]

# Media files
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)