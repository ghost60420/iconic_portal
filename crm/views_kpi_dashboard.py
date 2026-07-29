import time

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from crm.models_access import UserAccess
from crm.services.kpi_dashboard import (
    EXPORT_INTERFACES,
    DashboardFilters,
    KPIDashboardPermissionError,
    dashboard_audience,
    dashboard_filter_options,
    dashboard_widget_payload,
    dashboard_widgets,
)


@login_required
@require_GET
def kpi_dashboard(request):
    started = time.perf_counter()
    filters = DashboardFilters.from_querydict(request.GET)
    context = dashboard_filter_options(request.user, filters)
    request_profile = context.pop("_request_profile", None)
    request_access = context.pop("_request_access", None)
    if request_profile is not None:
        request.user._state.fields_cache["employee_profile"] = request_profile
    if request_access is None:
        request_access = UserAccess.objects.filter(user_id=request.user.pk).first()
    request.user._state.fields_cache["access"] = request_access
    request.user._crm_user_access_cached = request_access
    context.update(
        {
            "dashboard_widgets": dashboard_widgets(request.user),
            "filters": filters,
        }
    )
    response = render(request, "crm/kpi/dashboard/index.html", context)
    response["Cache-Control"] = "private, no-store"
    response["Server-Timing"] = (
        f"kpi-dashboard;dur={(time.perf_counter() - started) * 1000:.1f}"
    )
    return response


@login_required
@require_GET
def kpi_dashboard_widget(request, slug):
    started = time.perf_counter()
    filters = DashboardFilters.from_querydict(request.GET)
    try:
        payload = dashboard_widget_payload(request.user, slug, filters)
    except KPIDashboardPermissionError:
        return HttpResponseForbidden("This KPI widget is not available.")
    title = next(
        widget.title
        for widget in dashboard_widgets(request.user)
        if widget.slug == slug
    )
    response = render(
        request,
        "crm/kpi/dashboard/_widget.html",
        {"widget": payload, "widget_title": title},
    )
    response["Cache-Control"] = "private, no-store"
    response["Vary"] = "Cookie"
    response["X-KPI-Source"] = "approved-snapshots"
    response["Server-Timing"] = (
        f"kpi-widget;dur={(time.perf_counter() - started) * 1000:.1f}"
    )
    return response


@login_required
@require_GET
def kpi_dashboard_export_capabilities(request):
    return JsonResponse(
        {
            "audience": dashboard_audience(request.user),
            "capabilities": EXPORT_INTERFACES,
            "generation_enabled": False,
        }
    )
