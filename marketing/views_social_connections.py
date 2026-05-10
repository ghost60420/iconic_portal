from django.conf import settings
from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from marketing.forms_social_connections import MarketingSocialConnectionForm
from marketing.models import OAuthCredential
from marketing.services.errors import MarketingServiceError
from marketing.services.social_connections import (
    SOCIAL_CONNECTION_CONFIG,
    SOCIAL_CONNECTION_PLATFORM_KEYS,
    build_connection_cards,
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


@require_http_methods(["GET", "POST"])
def social_connections(request):
    _require_enabled()

    editable = _editable_connection_from_request(request)
    selected_platform = request.GET.get("platform", "").strip()
    if selected_platform not in SOCIAL_CONNECTION_PLATFORM_KEYS:
        selected_platform = ""

    if request.method == "POST":
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
    meta_app_id = getattr(settings, "MARKETING_META_APP_ID", "")
    meta_app_secret = getattr(settings, "MARKETING_META_APP_SECRET", "")
    meta_redirect_uri = getattr(settings, "MARKETING_META_REDIRECT_URI", "")

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
        },
    )


@require_POST
def social_connection_sync(request, pk: int):
    _require_enabled()
    connection = get_object_or_404(
        OAuthCredential.objects.select_related("platform_account"),
        pk=pk,
        platform__in=SOCIAL_CONNECTION_PLATFORM_KEYS,
    )
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
