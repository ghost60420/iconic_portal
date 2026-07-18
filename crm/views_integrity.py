from django.http import HttpResponse, HttpResponseBadRequest

from crm.services.opportunity_stage_audit import (
    build_opportunity_stage_audit,
    render_crm_integrity_csv,
)


def crm_integrity_export_csv(request):
    filter_mode = (request.GET.get("filter") or "broken").strip().lower()
    audit = build_opportunity_stage_audit()
    try:
        csv_body = render_crm_integrity_csv(audit, filter_mode=filter_mode)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))
    response = HttpResponse(csv_body, content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="crm_integrity_export.csv"'
    return response
