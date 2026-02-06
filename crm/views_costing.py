import io
from decimal import Decimal

from django.contrib import messages
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.core.files.base import ContentFile

from .forms_costing import (
    CostLineItemForm,
    CostSheetForm,
    OpportunityDocumentForm,
)
from .models import (
    CostLineItem,
    CostSheet,
    CostSheetAudit,
    Opportunity,
    OpportunityDocument,
    COST_SECTION_CHOICES,
    COST_SHEET_STATUS_CHOICES,
)
from .services.costing import build_variance_report, calculate_cost_sheet


TEMPLATE_PRESETS = {
    "hoodie": [
        {"section": "fabric", "item_name": "Main fabric", "uom": "kg", "consumption_per_piece": "1.2", "waste_percent": "3", "rate": "5.8"},
        {"section": "trims", "item_name": "Zipper", "uom": "pc", "consumption_per_piece": "1", "waste_percent": "1", "rate": "0.5"},
        {"section": "labor", "item_name": "Sewing", "uom": "min", "consumption_per_piece": "18", "waste_percent": "0", "rate": "0.12"},
        {"section": "overhead", "item_name": "Factory overhead", "uom": "pc", "consumption_per_piece": "1", "waste_percent": "0", "rate": "0.65"},
        {"section": "packaging", "item_name": "Poly + carton", "uom": "pc", "consumption_per_piece": "1", "waste_percent": "0", "rate": "0.2"},
    ],
    "tshirt": [
        {"section": "fabric", "item_name": "Main fabric", "uom": "kg", "consumption_per_piece": "0.6", "waste_percent": "2", "rate": "4.2"},
        {"section": "trims", "item_name": "Neck rib", "uom": "pc", "consumption_per_piece": "1", "waste_percent": "1", "rate": "0.18"},
        {"section": "labor", "item_name": "Sewing", "uom": "min", "consumption_per_piece": "8", "waste_percent": "0", "rate": "0.1"},
        {"section": "overhead", "item_name": "Factory overhead", "uom": "pc", "consumption_per_piece": "1", "waste_percent": "0", "rate": "0.35"},
        {"section": "packaging", "item_name": "Poly + carton", "uom": "pc", "consumption_per_piece": "1", "waste_percent": "0", "rate": "0.12"},
    ],
    "legging": [
        {"section": "fabric", "item_name": "Main fabric", "uom": "kg", "consumption_per_piece": "0.9", "waste_percent": "3", "rate": "6.1"},
        {"section": "trims", "item_name": "Elastic", "uom": "pc", "consumption_per_piece": "1", "waste_percent": "1", "rate": "0.22"},
        {"section": "labor", "item_name": "Sewing", "uom": "min", "consumption_per_piece": "12", "waste_percent": "0", "rate": "0.11"},
        {"section": "overhead", "item_name": "Factory overhead", "uom": "pc", "consumption_per_piece": "1", "waste_percent": "0", "rate": "0.45"},
        {"section": "packaging", "item_name": "Poly + carton", "uom": "pc", "consumption_per_piece": "1", "waste_percent": "0", "rate": "0.15"},
    ],
    "kids_set": [
        {"section": "fabric", "item_name": "Main fabric", "uom": "kg", "consumption_per_piece": "0.7", "waste_percent": "3", "rate": "4.8"},
        {"section": "trims", "item_name": "Label + tag", "uom": "pc", "consumption_per_piece": "1", "waste_percent": "1", "rate": "0.15"},
        {"section": "labor", "item_name": "Sewing", "uom": "min", "consumption_per_piece": "10", "waste_percent": "0", "rate": "0.1"},
        {"section": "overhead", "item_name": "Factory overhead", "uom": "pc", "consumption_per_piece": "1", "waste_percent": "0", "rate": "0.32"},
        {"section": "packaging", "item_name": "Poly + carton", "uom": "pc", "consumption_per_piece": "1", "waste_percent": "0", "rate": "0.12"},
    ],
}


def _parse_decimal(value):
    try:
        return Decimal(str(value).strip())
    except Exception:
        return Decimal("0")


def _user_can_approve(user):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    access = getattr(user, "access", None)
    return bool(access and (access.can_costing_approve or access.is_ca))


def _audit(cost_sheet, action, user, note="", before=None, after=None):
    try:
        CostSheetAudit.objects.create(
            cost_sheet=cost_sheet,
            action=action,
            changed_by=user if user and user.is_authenticated else None,
            note=note or "",
            before_data=before,
            after_data=after,
        )
    except Exception:
        pass


def cost_sheet_list(request):
    qs = (
        CostSheet.objects.select_related("opportunity", "customer")
        .prefetch_related("line_items")
        .order_by("-updated_at")
    )

    customer_id = (request.GET.get("customer") or "").strip()
    product_type = (request.GET.get("product_type") or "").strip()
    status = (request.GET.get("status") or "").strip()
    show_all = (request.GET.get("show") or "").strip() == "all"

    if customer_id:
        qs = qs.filter(customer_id=customer_id)
    if product_type:
        qs = qs.filter(product_type=product_type)
    if status:
        qs = qs.filter(status=status)
    if not show_all:
        qs = qs.filter(is_active=True)

    customers = (
        Opportunity.objects.select_related("customer")
        .exclude(customer__isnull=True)
        .values("customer_id", "customer__account_brand")
        .distinct()
        .order_by("customer__account_brand")
    )

    rows = []
    for sheet in qs:
        rows.append({"sheet": sheet, "calc": calculate_cost_sheet(sheet)})

    context = {
        "rows": rows,
        "customers": customers,
        "product_types": Opportunity.PRODUCT_TYPE_CHOICES,
        "status_choices": COST_SHEET_STATUS_CHOICES,
        "show_all": show_all,
        "selected_customer": customer_id,
        "selected_product_type": product_type,
        "selected_status": status,
    }
    return render(request, "crm/costing/costsheet_list.html", context)


def cost_sheet_create(request, opportunity_id=None):
    opportunity = None
    if opportunity_id:
        opportunity = get_object_or_404(Opportunity, pk=opportunity_id)

    if request.method == "POST":
        form = CostSheetForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            if opportunity:
                obj.opportunity = opportunity
            obj.save()
            if not CostSheet.objects.filter(opportunity=obj.opportunity, is_active=True).exclude(id=obj.id).exists():
                obj.is_active = True
                obj.save(update_fields=["is_active"])
            _audit(obj, "created_version", request.user, note="New cost sheet created.")
            messages.success(request, "Cost sheet created.")
            return redirect("cost_sheet_detail", pk=obj.pk)
        messages.error(request, "Please fix the errors below.")
    else:
        initial = {}
        if opportunity:
            initial["opportunity"] = opportunity
            initial["customer"] = opportunity.customer
            initial["product_type"] = opportunity.product_type
            initial["target_quantity"] = opportunity.moq_units or 0
        form = CostSheetForm(initial=initial)

    context = {
        "form": form,
        "opportunity": opportunity,
        "mode": "create",
    }
    return render(request, "crm/costing/costsheet_form.html", context)


def cost_sheet_detail(request, pk):
    cost_sheet = get_object_or_404(
        CostSheet.objects.select_related("opportunity", "customer").prefetch_related("line_items"),
        pk=pk,
    )

    locked = cost_sheet.status in ["approved", "locked"]
    section_labels = dict(COST_SECTION_CHOICES)

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()

        if action == "save_header":
            if locked:
                messages.error(request, "This cost sheet is approved or locked. Create a new version to edit.")
                return redirect("cost_sheet_detail", pk=pk)

            data = request.POST.copy()
            data["opportunity"] = cost_sheet.opportunity_id
            if cost_sheet.customer_id:
                data["customer"] = cost_sheet.customer_id
            form = CostSheetForm(data, instance=cost_sheet)
            if form.is_valid():
                form.save()
                for line in cost_sheet.line_items.all():
                    line.save()
                messages.success(request, "Cost sheet updated.")
            else:
                messages.error(request, "Please fix the form errors.")
            return redirect("cost_sheet_detail", pk=pk)

        if action == "add_line":
            if locked:
                messages.error(request, "This cost sheet is approved or locked.")
                return redirect("cost_sheet_detail", pk=pk)

            line_form = CostLineItemForm(request.POST)
            if line_form.is_valid():
                line = line_form.save(commit=False)
                line.cost_sheet = cost_sheet
                line.save()
                messages.success(request, "Line item added.")
            else:
                messages.error(request, "Please fill all required line fields.")
            return redirect("cost_sheet_detail", pk=pk)

        if action == "update_line":
            if locked:
                messages.error(request, "This cost sheet is approved or locked.")
                return redirect("cost_sheet_detail", pk=pk)

            line_id = (request.POST.get("line_id") or "").strip()
            line = CostLineItem.objects.filter(id=line_id, cost_sheet=cost_sheet).first()
            if line:
                line.section = request.POST.get("section", line.section)
                line.item_name = (request.POST.get("item_name") or "").strip()
                line.uom = (request.POST.get("uom") or "").strip()
                line.consumption_per_piece = _parse_decimal(request.POST.get("consumption_per_piece"))
                line.waste_percent = _parse_decimal(request.POST.get("waste_percent"))
                line.rate = _parse_decimal(request.POST.get("rate"))
                line.setup_cost = _parse_decimal(request.POST.get("setup_cost"))
                line.notes = (request.POST.get("notes") or "").strip()
                line.save()
                messages.success(request, "Line item updated.")
            return redirect("cost_sheet_detail", pk=pk)

        if action == "delete_line":
            if locked:
                messages.error(request, "This cost sheet is approved or locked.")
                return redirect("cost_sheet_detail", pk=pk)

            line_id = (request.POST.get("line_id") or "").strip()
            CostLineItem.objects.filter(id=line_id, cost_sheet=cost_sheet).delete()
            messages.success(request, "Line item removed.")
            return redirect("cost_sheet_detail", pk=pk)

        if action == "duplicate_line":
            if locked:
                messages.error(request, "This cost sheet is approved or locked.")
                return redirect("cost_sheet_detail", pk=pk)

            line_id = (request.POST.get("line_id") or "").strip()
            line = CostLineItem.objects.filter(id=line_id, cost_sheet=cost_sheet).first()
            if line:
                CostLineItem.objects.create(
                    cost_sheet=cost_sheet,
                    section=line.section,
                    item_name=line.item_name,
                    uom=line.uom,
                    consumption_per_piece=line.consumption_per_piece,
                    waste_percent=line.waste_percent,
                    rate=line.rate,
                    setup_cost=line.setup_cost,
                    notes=line.notes,
                )
                messages.success(request, "Line item duplicated.")
            return redirect("cost_sheet_detail", pk=pk)

        if action == "apply_template":
            if locked:
                messages.error(request, "This cost sheet is approved or locked.")
                return redirect("cost_sheet_detail", pk=pk)

            key = (request.POST.get("template_key") or "").strip()
            template = TEMPLATE_PRESETS.get(key)
            if template:
                for row in template:
                    CostLineItem.objects.create(
                        cost_sheet=cost_sheet,
                        section=row.get("section", "other"),
                        item_name=row.get("item_name", ""),
                        uom=row.get("uom", ""),
                        consumption_per_piece=_parse_decimal(row.get("consumption_per_piece")),
                        waste_percent=_parse_decimal(row.get("waste_percent")),
                        rate=_parse_decimal(row.get("rate")),
                        setup_cost=_parse_decimal(row.get("setup_cost")),
                    )
                messages.success(request, "Template line items added.")
            else:
                messages.error(request, "Template not found.")
            return redirect("cost_sheet_detail", pk=pk)

        if action == "set_active":
            cost_sheet.is_active = True
            cost_sheet.save(update_fields=["is_active"])
            messages.success(request, "This version is now active for quoting.")
            return redirect("cost_sheet_detail", pk=pk)

        if action == "approve":
            if not _user_can_approve(request.user):
                return HttpResponseForbidden("No access to approve costing.")
            cost_sheet.status = "approved"
            cost_sheet.approved_at = timezone.now()
            cost_sheet.approved_by = request.user if request.user.is_authenticated else None
            cost_sheet.save()
            _audit(cost_sheet, "approved", request.user, note="Cost sheet approved.")
            messages.success(request, "Cost sheet approved.")
            return redirect("cost_sheet_detail", pk=pk)

        if action == "lock":
            if not _user_can_approve(request.user):
                return HttpResponseForbidden("No access to lock costing.")
            cost_sheet.status = "locked"
            cost_sheet.save(update_fields=["status"])
            _audit(cost_sheet, "locked", request.user, note="Cost sheet locked.")
            messages.success(request, "Cost sheet locked.")
            return redirect("cost_sheet_detail", pk=pk)

        if action == "duplicate_version":
            new_sheet = CostSheet.objects.create(
                opportunity=cost_sheet.opportunity,
                customer=cost_sheet.customer,
                product_type=cost_sheet.product_type,
                style_code=cost_sheet.style_code,
                style_name=cost_sheet.style_name,
                currency=cost_sheet.currency,
                production_location=cost_sheet.production_location,
                target_quantity=cost_sheet.target_quantity,
                overhead_method=cost_sheet.overhead_method,
                target_margin_percent=cost_sheet.target_margin_percent,
                quote_price_per_piece=cost_sheet.quote_price_per_piece,
                status="draft",
                is_active=False,
            )
            for line in cost_sheet.line_items.all():
                CostLineItem.objects.create(
                    cost_sheet=new_sheet,
                    section=line.section,
                    item_name=line.item_name,
                    uom=line.uom,
                    consumption_per_piece=line.consumption_per_piece,
                    waste_percent=line.waste_percent,
                    rate=line.rate,
                    setup_cost=line.setup_cost,
                    notes=line.notes,
                )
            _audit(new_sheet, "created_version", request.user, note="Version duplicated.")
            messages.success(request, "New version created.")
            return redirect("cost_sheet_detail", pk=new_sheet.pk)

        if action == "upload_document":
            form = OpportunityDocumentForm(request.POST, request.FILES)
            if form.is_valid():
                doc = form.save(commit=False)
                doc.opportunity = cost_sheet.opportunity
                doc.cost_sheet = cost_sheet
                doc.uploaded_by = request.user if request.user.is_authenticated else None
                doc.save()
                _audit(cost_sheet, "uploaded_file", request.user, note=doc.original_name)
                messages.success(request, "Document uploaded.")
            else:
                messages.error(request, "Please choose a file and type.")
            return redirect("cost_sheet_detail", pk=pk)

    calc = calculate_cost_sheet(cost_sheet)

    scenario_quantities = [50, 100, 200, 500, 1000]
    scenarios = []
    for qty in scenario_quantities:
        scenario_calc = calculate_cost_sheet(cost_sheet, target_qty_override=qty)
        scenarios.append(
            {
                "qty": qty,
                "total_cost_per_piece": scenario_calc["display"]["total_cost_per_piece"],
                "quote_price_per_piece": scenario_calc["display"]["quote_price_per_piece"],
                "total_quote_value": scenario_calc["display"]["total_quote_value"],
            }
        )

    versions = CostSheet.objects.filter(opportunity=cost_sheet.opportunity).order_by("-version_number")
    documents = OpportunityDocument.objects.filter(cost_sheet=cost_sheet).order_by("-uploaded_at")

    compare_a = request.GET.get("compare_a")
    compare_b = request.GET.get("compare_b")
    compare_rows = []

    if compare_a and compare_b:
        sheet_a = CostSheet.objects.filter(id=compare_a, opportunity=cost_sheet.opportunity).first()
        sheet_b = CostSheet.objects.filter(id=compare_b, opportunity=cost_sheet.opportunity).first()
        if sheet_a and sheet_b:
            lines_a = {f"{l.section}:{l.item_name}": l for l in sheet_a.line_items.all()}
            lines_b = {f"{l.section}:{l.item_name}": l for l in sheet_b.line_items.all()}
            all_keys = sorted(set(lines_a.keys()) | set(lines_b.keys()))
            for key in all_keys:
                la = lines_a.get(key)
                lb = lines_b.get(key)
                row = {
                    "section": (la.section if la else lb.section),
                    "item_name": (la.item_name if la else lb.item_name),
                    "a": la,
                    "b": lb,
                    "changed": False,
                }
                if la and lb:
                    row["changed"] = any(
                        [
                            la.consumption_per_piece != lb.consumption_per_piece,
                            la.waste_percent != lb.waste_percent,
                            la.rate != lb.rate,
                            la.setup_cost != lb.setup_cost,
                        ]
                    )
                else:
                    row["changed"] = True
                compare_rows.append(row)

    header_form = CostSheetForm(instance=cost_sheet)
    if "opportunity" in header_form.fields:
        header_form.fields["opportunity"].disabled = True
    if "customer" in header_form.fields:
        header_form.fields["customer"].disabled = True

    context = {
        "cost_sheet": cost_sheet,
        "locked": locked,
        "section_labels": section_labels,
        "sections": COST_SECTION_CHOICES,
        "calc": calc,
        "line_items": cost_sheet.line_items.all(),
        "line_form": CostLineItemForm(),
        "header_form": header_form,
        "documents": documents,
        "document_form": OpportunityDocumentForm(),
        "versions": versions,
        "scenarios": scenarios,
        "compare_rows": compare_rows,
        "compare_a": compare_a,
        "compare_b": compare_b,
        "template_keys": TEMPLATE_PRESETS.keys(),
    }
    return render(request, "crm/costing/costsheet_detail.html", context)


def _save_export_document(cost_sheet, filename, data, doc_type, user):
    try:
        OpportunityDocument.objects.create(
            opportunity=cost_sheet.opportunity,
            cost_sheet=cost_sheet,
            file=ContentFile(data, name=filename),
            original_name=filename,
            doc_type=doc_type,
            uploaded_by=user if user and user.is_authenticated else None,
        )
        _audit(cost_sheet, "exported", user, note=f"{doc_type} {filename}")
    except Exception:
        pass


def cost_sheet_export_pdf(request, pk):
    cost_sheet = get_object_or_404(
        CostSheet.objects.select_related("opportunity", "customer").prefetch_related("line_items"),
        pk=pk,
    )

    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
    except ImportError:
        return HttpResponse(
            "ReportLab is not installed. Install 'reportlab' to enable PDF export.",
            content_type="text/plain",
        )

    calc = calculate_cost_sheet(cost_sheet)
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    y = height - 50

    p.setFont("Helvetica-Bold", 16)
    p.drawString(50, y, "Costing Sheet")
    y -= 22

    p.setFont("Helvetica", 10)
    header_lines = [
        f"Customer: {(cost_sheet.customer.account_brand if cost_sheet.customer else '') or 'Not set'}",
        f"Opportunity: {cost_sheet.opportunity.opportunity_id}",
        f"Style: {cost_sheet.style_name or cost_sheet.style_code or '-'}",
        f"Version: v{cost_sheet.version_number}",
        f"Date: {timezone.now().date()}",
    ]
    for line in header_lines:
        p.drawString(50, y, line)
        y -= 14

    y -= 6
    p.setFont("Helvetica-Bold", 11)
    p.drawString(50, y, "Summary")
    y -= 16
    p.setFont("Helvetica", 10)
    summary_lines = [
        f"Total cost per piece: {calc['display']['total_cost_per_piece']}",
        f"Quote price per piece: {calc['display']['quote_price_per_piece']}",
        f"Margin %: {calc['display']['margin_percent']}",
        f"Target quantity: {cost_sheet.target_quantity}",
    ]
    for line in summary_lines:
        p.drawString(50, y, line)
        y -= 14

    y -= 6
    p.setFont("Helvetica-Bold", 11)
    p.drawString(50, y, "Line items")
    y -= 16

    p.setFont("Helvetica-Bold", 9)
    p.drawString(50, y, "Item")
    p.drawString(220, y, "UOM")
    p.drawString(260, y, "Cons")
    p.drawString(310, y, "Waste%")
    p.drawString(360, y, "Rate")
    p.drawString(410, y, "Setup")
    p.drawString(470, y, "Total/pc")
    y -= 12
    p.line(50, y, width - 50, y)
    y -= 12

    for section_key, section_label in COST_SECTION_CHOICES:
        p.setFont("Helvetica-Bold", 9)
        p.drawString(50, y, section_label)
        y -= 12
        p.setFont("Helvetica", 9)

        for row in calc["line_rows"]:
            if row["section"] != section_key:
                continue
            p.drawString(50, y, str(row["item_name"])[:28])
            p.drawString(220, y, str(row["uom"])[:6])
            p.drawString(260, y, str(row["consumption_per_piece"]))
            p.drawString(310, y, str(row["waste_percent"]))
            p.drawString(360, y, str(row["rate"]))
            p.drawString(410, y, str(row["setup_cost"]))
            p.drawString(470, y, str(row["total_cost_per_piece"]))
            y -= 12
            if y < 80:
                p.showPage()
                y = height - 50
                p.setFont("Helvetica", 9)

        total = calc["display"]["section_totals"].get(section_key)
        if total is not None:
            p.setFont("Helvetica-Bold", 9)
            p.drawString(410, y, "Section total")
            p.drawString(470, y, str(total))
            y -= 14
            p.setFont("Helvetica", 9)

        if y < 80:
            p.showPage()
            y = height - 50
            p.setFont("Helvetica", 9)

    p.showPage()
    p.save()
    pdf_bytes = buffer.getvalue()

    filename = f"costing_{cost_sheet.opportunity.opportunity_id}_v{cost_sheet.version_number}.pdf"
    _save_export_document(cost_sheet, filename, pdf_bytes, "costing_pdf", request.user)

    resp = HttpResponse(content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp.write(pdf_bytes)
    return resp


def cost_sheet_export_excel(request, pk):
    cost_sheet = get_object_or_404(
        CostSheet.objects.select_related("opportunity", "customer").prefetch_related("line_items"),
        pk=pk,
    )

    try:
        from openpyxl import Workbook
    except Exception:
        return HttpResponse("openpyxl is not installed", status=500)

    calc = calculate_cost_sheet(cost_sheet)

    wb = Workbook()
    ws_summary = wb.active
    ws_summary.title = "Summary"

    ws_summary.append(["Customer", (cost_sheet.customer.account_brand if cost_sheet.customer else "") or "Not set"])
    ws_summary.append(["Opportunity", cost_sheet.opportunity.opportunity_id])
    ws_summary.append(["Style", cost_sheet.style_name or cost_sheet.style_code or "-"])
    ws_summary.append(["Version", f"v{cost_sheet.version_number}"])
    ws_summary.append(["Currency", cost_sheet.currency])
    ws_summary.append(["Target quantity", cost_sheet.target_quantity])
    ws_summary.append(["Total cost per piece", float(calc["display"]["total_cost_per_piece"])])
    ws_summary.append(["Quote price per piece", float(calc["display"]["quote_price_per_piece"])])
    ws_summary.append(["Margin %", float(calc["display"]["margin_percent"])])

    ws_summary.append([])
    ws_summary.append(["Section", "Total per piece"])
    for section_key, section_label in COST_SECTION_CHOICES:
        total_val = calc["display"]["section_totals"].get(section_key, 0)
        ws_summary.append([section_label, float(total_val)])

    ws_lines = wb.create_sheet("Line Items")
    ws_lines.append([
        "Section",
        "Item",
        "UOM",
        "Consumption per piece",
        "Waste %",
        "Rate",
        "Setup cost",
        "Total cost per piece",
        "Notes",
    ])

    for row in calc["line_rows"]:
        ws_lines.append([
            dict(COST_SECTION_CHOICES).get(row["section"], row["section"]),
            row["item_name"],
            row["uom"],
            float(row["consumption_per_piece"]),
            float(row["waste_percent"]),
            float(row["rate"]),
            float(row["setup_cost"]),
            float(row["total_cost_per_piece"]),
            row.get("notes", ""),
        ])

    output = io.BytesIO()
    wb.save(output)
    data = output.getvalue()

    filename = f"costing_{cost_sheet.opportunity.opportunity_id}_v{cost_sheet.version_number}.xlsx"
    _save_export_document(cost_sheet, filename, data, "costing_excel", request.user)

    resp = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp.write(data)
    return resp
