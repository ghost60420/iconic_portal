from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from marketing.services.errors import MarketingServiceError
from marketing.services.operations import build_marketing_operations_context, run_marketing_operations_sync


def _require_enabled():
    if not getattr(settings, "MARKETING_ENABLED", False):
        raise Http404("Marketing disabled")


def _can_view_logs(user) -> bool:
    return bool(user and user.is_authenticated and (user.is_superuser or user.groups.filter(name="CEO").exists()))


def _can_run_sync(user) -> bool:
    return bool(
        user
        and user.is_authenticated
        and (user.is_superuser or user.groups.filter(name__in=["Marketing Manager", "CEO"]).exists())
    )


def marketing_operations(request):
    _require_enabled()
    context = build_marketing_operations_context(include_logs=_can_view_logs(request.user))
    context.update(
        {
            "can_view_logs": _can_view_logs(request.user),
            "can_run_sync": _can_run_sync(request.user),
        }
    )
    return render(request, "marketing/operations.html", context)


@require_POST
def marketing_operations_sync(request, platform: str):
    _require_enabled()
    if not _can_run_sync(request.user):
        return HttpResponseForbidden("Only CEO and Marketing Manager users can run marketing syncs.")
    try:
        output = run_marketing_operations_sync(platform=platform, user=request.user)
    except MarketingServiceError as exc:
        messages.error(request, f"{platform} sync failed: {exc}")
    else:
        detail = f" {output}" if output else ""
        messages.success(request, f"{platform} sync complete.{detail}")
    return redirect(f"{reverse('marketing_operations')}#{platform}")
