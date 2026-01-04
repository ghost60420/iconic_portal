from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("admin/", admin.site.urls),

    # Auth
    path("accounts/", include("django.contrib.auth.urls")),

    # CRM app
    path("", include("crm.urls")),
]

# Media files (attachments)
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)