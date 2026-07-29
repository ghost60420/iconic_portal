import time

from django.contrib.auth.decorators import login_required
from django.http import (
    HttpResponse,
    HttpResponseForbidden,
    HttpResponseNotFound,
)
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET

from crm.models_access import UserAccess
from crm.services.kpi_dashboard import (
    DashboardFilters,
    dashboard_filter_options,
)
from crm.services.kpi_intelligence import (
    KPIIntelligencePermissionError,
    allowed_report_types,
    intelligence_widget_payload,
    intelligence_widgets,
    record_intelligence_events,
    resolve_intelligence_action,
)
from crm.services.kpi_reporting import (
    build_kpi_report,
    report_csv_bytes,
    report_pdf_bytes,
    report_xlsx_bytes,
)


REPORT_FORMATS = {"pdf", "xlsx", "csv", "print"}


def _prime_request_user(request, context):
    request_profile = context.pop("_request_profile", None)
    request_access = context.pop("_request_access", None)
    if request_profile is not None:
        request.user._state.fields_cache["employee_profile"] = request_profile
    if request_access is None:
        request_access = UserAccess.objects.filter(user_id=request.user.pk).first()
    request.user._state.fields_cache["access"] = request_access
    request.user._crm_user_access_cached = request_access


@login_required
@require_GET
def kpi_intelligence(request):
    started = time.perf_counter()
    filters = DashboardFilters.from_querydict(request.GET)
    context = dashboard_filter_options(request.user, filters)
    _prime_request_user(request, context)
    context.update(
        {
            "intelligence_widgets": intelligence_widgets(request.user),
            "reports": allowed_report_types(request.user),
            "filters": filters,
        }
    )
    response = render(request, "crm/kpi/intelligence/index.html", context)
    response["Cache-Control"] = "private, no-store"
    response["Server-Timing"] = (
        f"kpi-intelligence;dur={(time.perf_counter() - started) * 1000:.1f}"
    )
    return response


@login_required
@require_GET
def kpi_intelligence_widget(request, slug):
    started = time.perf_counter()
    filters = DashboardFilters.from_querydict(request.GET)
    try:
        payload = intelligence_widget_payload(
            request.user,
            slug,
            filters,
            page=request.GET.get("page", 1),
        )
        title = next(
            widget.title
            for widget in intelligence_widgets(request.user)
            if widget.slug == slug
        )
    except KPIIntelligencePermissionError:
        return HttpResponseForbidden(
            "This intelligence section is not available."
        )
    events = []
    if not payload.pop("_cache_hit", False):
        events.append(
            {
                "event": "cached_intelligence_refreshed",
                "record_id": slug,
                "record_label": title,
            }
        )
    if slug in {"critical-alerts", "yellow-attention"}:
        events.append(
            {
                "event": "executive_alert_viewed",
                "record_id": slug,
                "record_label": title,
            }
        )
    record_intelligence_events(actor=request.user, events=events)
    response = render(
        request,
        "crm/kpi/intelligence/_widget.html",
        {"widget": payload, "widget_title": title},
    )
    response["Cache-Control"] = "private, no-store"
    response["Vary"] = "Cookie"
    response["X-KPI-Source"] = "approved-snapshots"
    response["X-KPI-Intelligence-Version"] = payload.get(
        "intelligence_version",
        "",
    )
    response["Server-Timing"] = (
        f"kpi-intelligence-widget;dur="
        f"{(time.perf_counter() - started) * 1000:.1f}"
    )
    return response


@login_required
@require_GET
def kpi_intelligence_report(request, report_type, export_format):
    if export_format not in REPORT_FORMATS:
        return HttpResponseNotFound("This export format is not available.")
    filters = DashboardFilters.from_querydict(request.GET)
    try:
        report = build_kpi_report(request.user, report_type, filters)
    except KPIIntelligencePermissionError:
        return HttpResponseForbidden("This KPI report is not available.")

    report_url = request.get_full_path()
    events = [
        {
            "event": "report_generated",
            "record_id": report_type,
            "record_label": report.title,
            "target_url": report_url,
        }
    ]
    if export_format != "print":
        events.append(
            {
                "event": "report_exported",
                "record_id": f"{report_type}:{export_format}",
                "record_label": report.title,
                "target_url": report_url,
            }
        )
    if report_type == "bonus-readiness":
        events.append(
            {
                "event": "sensitive_bonus_report_opened",
                "record_id": report_type,
                "record_label": report.title,
                "target_url": report_url,
            }
        )
    if filters.employee_id:
        events.append(
            {
                "event": "filtered_employee_report_generated",
                "record_id": filters.employee_id,
                "record_label": report.title,
                "target_url": report_url,
            }
        )
    record_intelligence_events(actor=request.user, events=events)

    filename = f"kpi-{report_type}-{filters.year}"
    if export_format == "print":
        response = render(
            request,
            "crm/kpi/intelligence/report_print.html",
            {"report": report},
        )
    elif export_format == "pdf":
        response = HttpResponse(
            report_pdf_bytes(report),
            content_type="application/pdf",
        )
        response["Content-Disposition"] = (
            f'attachment; filename="{filename}.pdf"'
        )
    elif export_format == "xlsx":
        response = HttpResponse(
            report_xlsx_bytes(report),
            content_type=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
        )
        response["Content-Disposition"] = (
            f'attachment; filename="{filename}.xlsx"'
        )
    else:
        response = HttpResponse(
            report_csv_bytes(report),
            content_type="text/csv; charset=utf-8",
        )
        response["Content-Disposition"] = (
            f'attachment; filename="{filename}.csv"'
        )
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
@require_GET
def kpi_intelligence_action(request, token):
    try:
        target = resolve_intelligence_action(request.user, token)
    except KPIIntelligencePermissionError:
        return HttpResponseForbidden("This intelligence action is not available.")
    return redirect(target)
