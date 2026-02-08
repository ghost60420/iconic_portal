from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.shortcuts import redirect

urlpatterns = [
    path("admin/", admin.site.urls),

    # login logout
    path("accounts/", include("django.contrib.auth.urls")),

    path("marketing/", include("marketing.urls")),
    path("whatsapp/", include("whatsapp.urls")),

    # your crm app
    path("", include("crm.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
