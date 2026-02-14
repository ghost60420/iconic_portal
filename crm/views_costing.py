import io
import logging
from decimal import Decimal

from django.contrib import messages
from django.core.files.base import ContentFile
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.db.models import Q
from django.utils import timezone

from .forms_costing import CostSheetSimpleForm, OpportunityDocumentForm
from .models import (
    COST_SHEET_SIMPLE_STATUS_CHOICES,
    CostSheetSimple,
    Opportunity,
    OpportunityDocument,
)
from .services.costing_currency import format_bdt, format_cad
from .services.costing_simple import calculate_cost_sheet_simple


logger = logging.getLogger(__name__)


def _safe_pdf_text(value):
    text = str(value or "")
    return text.encode("ascii", "replace").decode("ascii")


def _can_edit_exchange_rate(user):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    access = getattr(user, "access", None)
    return bool(access and access.can_costing_approve)


def cost_sheet_list(request):
    sheets = CostSheetSimple.objects.select_related("opportunity", "customer")
    search_query = (request.GET.get("q") or "").strip()
    if search_query:
        sheets = sheets.filter(
            Q(opportunity__opportunity_id__icontains=search_query)
            | Q(opportunity__lead__account_brand__icontains=search_query)
            | Q(customer__account_brand__icontains=search_query)
            | Q(customer__contact_name__icontains=search_query)
            | Q(style_name__icontains=search_query)
            | Q(style_code__icontains=search_query)
        )
    sheets = sheets.order_by("-updated_at")

    rows = []
    for sheet in sheets:
        rows.append({"sheet": sheet, "calc": calculate_cost_sheet_simple(sheet)})

    context = {
        "rows": rows,
        "status_choices": COST_SHEET_SIMPLE_STATUS_CHOICES,
        "search_query": search_query,
    }
    return render(request, "crm/costing/costsheet_list.html", context)


def cost_sheet_create(request, opportunity_id=None):
    opportunity = None
    can_edit_exchange_rate = _can_edit_exchange_rate(request.user)
    if opportunity_id:
        opportunity = get_object_or_404(Opportunity, pk=opportunity_id)

        existing = (
            CostSheetSimple.objects.filter(opportunity=opportunity)
            .order_by("-updated_at", "-id")
            .first()
        )
        if existing:
            messages.info(request, "Cost sheet already exists. Opened the latest one.")
            return redirect("cost_sheet_detail", pk=existing.pk)

    if request.method == "POST":
        data = request.POST.copy()
        if opportunity:
            data["opportunity"] = opportunity.pk
            if opportunity.customer_id:
                data["customer"] = opportunity.customer_id
        if not can_edit_exchange_rate:
            data["exchange_rate_bdt_per_cad"] = ""

        form = CostSheetSimpleForm(data)
        if form.is_valid():
            try:
                sheet = form.save(commit=False)
                if opportunity:
                    sheet.opportunity = opportunity
                sheet.currency = "BDT"
                sheet.save()
                messages.success(request, "Cost sheet created.")
                return redirect("cost_sheet_detail", pk=sheet.pk)
            except Exception:
                logger.exception("Failed to create simple cost sheet")
                messages.error(request, "Could not create the cost sheet. Please try again.")
        else:
            messages.error(request, "Please fix the errors below.")
    else:
        initial = {}
        if opportunity:
            initial = {
                "opportunity": opportunity,
                "customer": opportunity.customer,
                "product_type": opportunity.product_type,
                "quantity": opportunity.moq_units or 0,
                "factory_location": "bd",
            }
        form = CostSheetSimpleForm(initial=initial)

    if opportunity:
        if "opportunity" in form.fields:
            form.fields["opportunity"].disabled = True
        if "customer" in form.fields:
            form.fields["customer"].disabled = True
    if "exchange_rate_bdt_per_cad" in form.fields and not can_edit_exchange_rate:
        form.fields["exchange_rate_bdt_per_cad"].disabled = True

    context = {
        "form": form,
        "opportunity": opportunity,
        "mode": "create",
        "can_edit_exchange_rate": can_edit_exchange_rate,
        "exchange_rate_locked": False,
    }
    return render(request, "crm/costing/costsheet_form.html", context)


def cost_sheet_detail(request, pk):
    cost_sheet = get_object_or_404(
        CostSheetSimple.objects.select_related("opportunity", "customer"),
        pk=pk,
    )
    can_edit_exchange_rate = _can_edit_exchange_rate(request.user)
    exchange_rate_locked = cost_sheet.status != "draft"

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()

        if action == "save_sheet":
            data = request.POST.copy()
            data["opportunity"] = cost_sheet.opportunity_id
            if cost_sheet.customer_id:
                data["customer"] = cost_sheet.customer_id
            if not can_edit_exchange_rate or exchange_rate_locked:
                data["exchange_rate_bdt_per_cad"] = cost_sheet.exchange_rate_bdt_per_cad or ""

            form = CostSheetSimpleForm(data, instance=cost_sheet)
            if form.is_valid():
                try:
                    sheet = form.save(commit=False)
                    sheet.currency = "BDT"
                    sheet.save()
                    messages.success(request, "Cost sheet updated.")
                except Exception:
                    logger.exception("Failed to update simple cost sheet", extra={"id": cost_sheet.pk})
                    messages.error(request, "Could not save the cost sheet.")
            else:
                messages.error(request, "Please fix the errors below.")
            return redirect("cost_sheet_detail", pk=pk)

        if action == "approve":
            cost_sheet.status = "approved"
            cost_sheet.save(update_fields=["status"])
            messages.success(request, "Cost sheet approved.")
            return redirect("cost_sheet_detail", pk=pk)

        if action == "upload_document":
            form = OpportunityDocumentForm(request.POST, request.FILES)
            if form.is_valid():
                doc = form.save(commit=False)
                doc.opportunity = cost_sheet.opportunity
                doc.cost_sheet_simple = cost_sheet
                doc.uploaded_by = request.user if request.user.is_authenticated else None
                doc.save()
                messages.success(request, "Document uploaded.")
            else:
                messages.error(request, "Please choose a file and type.")
            return redirect("cost_sheet_detail", pk=pk)

    calc = calculate_cost_sheet_simple(cost_sheet)

    documents = OpportunityDocument.objects.filter(
        opportunity=cost_sheet.opportunity,
        doc_type__in=["costing_pdf", "costing_excel", "costing_other"],
    ).order_by("-uploaded_at")

    form = CostSheetSimpleForm(instance=cost_sheet)
    if "opportunity" in form.fields:
        form.fields["opportunity"].disabled = True
    if "customer" in form.fields:
        form.fields["customer"].disabled = True
    if "exchange_rate_bdt_per_cad" in form.fields and (not can_edit_exchange_rate or exchange_rate_locked):
        form.fields["exchange_rate_bdt_per_cad"].disabled = True

    context = {
        "cost_sheet": cost_sheet,
        "calc": calc,
        "form": form,
        "documents": documents,
        "document_form": OpportunityDocumentForm(),
        "can_edit_exchange_rate": can_edit_exchange_rate,
        "exchange_rate_locked": exchange_rate_locked,
    }
    return render(request, "crm/costing/costsheet_detail.html", context)


def _save_export_document(cost_sheet, filename, data, doc_type, user):
    try:
        OpportunityDocument.objects.create(
            opportunity=cost_sheet.opportunity,
            cost_sheet_simple=cost_sheet,
            file=ContentFile(data, name=filename),
            original_name=filename,
            doc_type=doc_type,
            uploaded_by=user if user and user.is_authenticated else None,
        )
    except Exception:
        logger.exception("Failed to save costing export document", extra={"cost_sheet_simple": cost_sheet.pk})


def cost_sheet_export_pdf(request, pk):
    cost_sheet = get_object_or_404(
        CostSheetSimple.objects.select_related("opportunity", "customer"),
        pk=pk,
    )

    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
    except ImportError:
        messages.error(request, "PDF export is unavailable. Please install ReportLab.")
        return redirect("cost_sheet_detail", pk=pk)

    try:
        calc = calculate_cost_sheet_simple(cost_sheet)
        exchange_rate = calc["exchange_rate"]
        cad_available = calc["cad_available"]
        cad_values = calc["cad"]
        buffer = io.BytesIO()
        p = canvas.Canvas(buffer, pagesize=letter)
        width, height = letter
        y = height - 50

        p.setFont("Helvetica-Bold", 16)
        p.drawString(50, y, "Costing Sheet (Simplified)")
        y -= 22

        p.setFont("Helvetica", 10)
        header_lines = [
            f"Customer: {_safe_pdf_text((cost_sheet.customer.account_brand if cost_sheet.customer else '') or 'Not set')}",
            f"Opportunity: {_safe_pdf_text(cost_sheet.opportunity.opportunity_id)}",
            f"Style: {_safe_pdf_text(cost_sheet.style_name or cost_sheet.style_code or '-')} ({_safe_pdf_text(cost_sheet.style_code or '-')})",
            f"Product type: {_safe_pdf_text(cost_sheet.get_product_type_display())}",
            f"Quantity: {cost_sheet.quantity}",
            f"Factory location: {_safe_pdf_text(cost_sheet.get_factory_location_display())}",
            f"Status: {_safe_pdf_text(cost_sheet.get_status_display())}",
            f"All costing is in \u09F3 Taka",
            "CAD values are reference only",
            (
                f"Exchange rate used: {format_bdt(exchange_rate)} per 1 CAD"
                if cad_available
                else "Exchange rate used: Not set"
            ),
            f"Date: {timezone.now().date()}",
        ]
        for line in header_lines:
            p.drawString(50, y, line)
            y -= 14

        y -= 6
        p.setFont("Helvetica-Bold", 11)
        p.drawString(50, y, "Summary line (BDT per piece)")
        y -= 16
        p.setFont("Helvetica", 10)
        summary_lines = [
            f"Fabric cost: {format_bdt(cost_sheet.fabric_cost_per_piece)}",
            f"Fabric wastage %: {cost_sheet.fabric_wastage_percent}",
            f"Fabric effective: {format_bdt(calc['display']['fabric_effective_cost_per_piece'])}",
            f"Rib: {format_bdt(cost_sheet.rib_cost_per_piece)}",
            f"Woven fabrics: {format_bdt(cost_sheet.woven_fabric_cost_per_piece)}",
            f"Zipper: {format_bdt(cost_sheet.zipper_cost_per_piece)}",
            f"Zipper puller: {format_bdt(cost_sheet.zipper_puller_cost_per_piece)}",
            f"Button: {format_bdt(cost_sheet.button_cost_per_piece)}",
            f"Thread: {format_bdt(cost_sheet.thread_cost_per_piece)}",
            f"Lining: {format_bdt(cost_sheet.lining_cost_per_piece)}",
            f"Velcro: {format_bdt(cost_sheet.velcro_cost_per_piece)}",
            f"Neck tape: {format_bdt(cost_sheet.neck_tape_cost_per_piece)}",
            f"Elastic: {format_bdt(cost_sheet.elastic_cost_per_piece)}",
            f"Collar & cuff: {format_bdt(cost_sheet.collar_cuff_cost_per_piece)}",
            f"Ring: {format_bdt(cost_sheet.ring_cost_per_piece)}",
            f"Buckle/clip: {format_bdt(cost_sheet.buckle_clip_cost_per_piece)}",
            f"Main label: {format_bdt(cost_sheet.main_label_cost_per_piece)}",
            f"Care label: {format_bdt(cost_sheet.care_label_cost_per_piece)}",
            f"Hang tag: {format_bdt(cost_sheet.hang_tag_cost_per_piece)}",
            f"Accessories: {format_bdt(cost_sheet.trim_cost_per_piece)}",
            f"Conveyance: {format_bdt(cost_sheet.conveyance_cost_per_piece)}",
            f"Sewing charge: {format_bdt(cost_sheet.labor_cost_per_piece)}",
            f"Overhead: {format_bdt(cost_sheet.overhead_cost_per_piece)}",
            f"Process/wash: {format_bdt(cost_sheet.process_cost_per_piece)}",
            f"Packaging: {format_bdt(cost_sheet.packaging_cost_per_piece)}",
            f"Freight/export: {format_bdt(cost_sheet.freight_cost_per_piece)}",
            f"Testing/compliance: {format_bdt(cost_sheet.testing_cost_per_piece)}",
            f"Others: {format_bdt(cost_sheet.other_cost_per_piece)}",
            (
                f"Total cost per piece: {format_bdt(calc['display']['total_cost_per_piece'])} | "
                f"CAD {format_cad(cad_values['total_cost_per_piece'])}"
                if cad_available
                else f"Total cost per piece: {format_bdt(calc['display']['total_cost_per_piece'])}"
            ),
            (
                f"Total order cost: {format_bdt(calc['display']['total_order_cost'])} | "
                f"CAD {format_cad(cad_values['total_order_cost'])}"
                if cad_available
                else f"Total order cost: {format_bdt(calc['display']['total_order_cost'])}"
            ),
        ]
        for line in summary_lines:
            p.drawString(50, y, line)
            y -= 14
            if y < 80:
                p.showPage()
                y = height - 50
                p.setFont("Helvetica", 10)

        y -= 6
        p.setFont("Helvetica-Bold", 11)
        p.drawString(50, y, "Quote & margin")
        y -= 16
        p.setFont("Helvetica", 10)
        quote_lines = [
            (
                f"Quote price per piece: {format_bdt(calc['display']['quote_price_per_piece'])} | "
                f"CAD {format_cad(cad_values['quote_price_per_piece'])}"
                if cad_available
                else f"Quote price per piece: {format_bdt(calc['display']['quote_price_per_piece'])}"
            ),
            (
                f"Profit per piece: {format_bdt(calc['display']['profit_per_piece'])} | "
                f"CAD {format_cad(cad_values['profit_per_piece'])}"
                if cad_available
                else f"Profit per piece: {format_bdt(calc['display']['profit_per_piece'])}"
            ),
            f"Margin %: {calc['display']['margin_percent']}",
            (
                f"Total profit: {format_bdt(calc['display']['total_profit'])} | "
                f"CAD {format_cad(cad_values['total_profit'])}"
                if cad_available
                else f"Total profit: {format_bdt(calc['display']['total_profit'])}"
            ),
        ]
        for line in quote_lines:
            p.drawString(50, y, line)
            y -= 14

        y -= 6
        p.setFont("Helvetica-Bold", 11)
        p.drawString(50, y, "Notes")
        y -= 16
        p.setFont("Helvetica", 10)
        notes = _safe_pdf_text(cost_sheet.notes or "-")
        p.drawString(50, y, notes[:120])

        p.showPage()
        p.save()
        pdf_bytes = buffer.getvalue()
    except Exception:
        logger.exception("Failed to generate costing PDF", extra={"cost_sheet_id": cost_sheet.pk})
        messages.error(request, "Could not generate the PDF. Please try again.")
        return redirect("cost_sheet_detail", pk=pk)

    filename = f"costing_{cost_sheet.opportunity.opportunity_id}_simple.pdf"
    _save_export_document(cost_sheet, filename, pdf_bytes, "costing_pdf", request.user)

    resp = HttpResponse(content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp.write(pdf_bytes)
    return resp


def cost_sheet_export_excel(request, pk):
    cost_sheet = get_object_or_404(
        CostSheetSimple.objects.select_related("opportunity", "customer"),
        pk=pk,
    )

    try:
        from openpyxl import Workbook
    except Exception:
        messages.error(request, "Excel export is unavailable. Please install openpyxl.")
        return redirect("cost_sheet_detail", pk=pk)

    try:
        calc = calculate_cost_sheet_simple(cost_sheet)
        exchange_rate = calc["exchange_rate"]
        cad_available = calc["cad_available"]
        cad_values = calc["cad"]
        wb = Workbook()
        ws_summary = wb.active
        ws_summary.title = "Summary"

        ws_summary.append(["Customer", (cost_sheet.customer.account_brand if cost_sheet.customer else "") or "Not set"])
        ws_summary.append(["Opportunity", cost_sheet.opportunity.opportunity_id])
        ws_summary.append(["Style name", cost_sheet.style_name or "-"])
        ws_summary.append(["Style code", cost_sheet.style_code or "-"])
        ws_summary.append(["Product type", cost_sheet.get_product_type_display()])
        ws_summary.append(["Quantity", cost_sheet.quantity])
        ws_summary.append(["Factory location", cost_sheet.get_factory_location_display()])
        ws_summary.append(["Status", cost_sheet.get_status_display()])
        ws_summary.append(["Currency", "BDT"])
        ws_summary.append(["All costing is in \u09F3 Taka", "CAD values are reference only"])
        ws_summary.append([
            "Exchange rate (\u09F3 per 1 CAD)",
            format_bdt(exchange_rate) if cad_available else "Not set",
        ])

        ws_summary.append([])
        ws_summary.append(["Fabric cost per piece", format_bdt(cost_sheet.fabric_cost_per_piece)])
        ws_summary.append(["Fabric wastage %", float(cost_sheet.fabric_wastage_percent)])
        ws_summary.append(["Fabric effective cost", format_bdt(calc["display"]["fabric_effective_cost_per_piece"])])
        ws_summary.append(["Rib cost per piece", format_bdt(cost_sheet.rib_cost_per_piece)])
        ws_summary.append(["Woven fabrics cost per piece", format_bdt(cost_sheet.woven_fabric_cost_per_piece)])
        ws_summary.append(["Zipper cost per piece", format_bdt(cost_sheet.zipper_cost_per_piece)])
        ws_summary.append(["Zipper puller cost per piece", format_bdt(cost_sheet.zipper_puller_cost_per_piece)])
        ws_summary.append(["Button cost per piece", format_bdt(cost_sheet.button_cost_per_piece)])
        ws_summary.append(["Thread cost per piece", format_bdt(cost_sheet.thread_cost_per_piece)])
        ws_summary.append(["Lining cost per piece", format_bdt(cost_sheet.lining_cost_per_piece)])
        ws_summary.append(["Velcro cost per piece", format_bdt(cost_sheet.velcro_cost_per_piece)])
        ws_summary.append(["Neck tape cost per piece", format_bdt(cost_sheet.neck_tape_cost_per_piece)])
        ws_summary.append(["Elastic cost per piece", format_bdt(cost_sheet.elastic_cost_per_piece)])
        ws_summary.append(["Collar & cuff cost per piece", format_bdt(cost_sheet.collar_cuff_cost_per_piece)])
        ws_summary.append(["Ring cost per piece", format_bdt(cost_sheet.ring_cost_per_piece)])
        ws_summary.append(["Buckle/clip cost per piece", format_bdt(cost_sheet.buckle_clip_cost_per_piece)])
        ws_summary.append(["Main label cost per piece", format_bdt(cost_sheet.main_label_cost_per_piece)])
        ws_summary.append(["Care label cost per piece", format_bdt(cost_sheet.care_label_cost_per_piece)])
        ws_summary.append(["Hang tag cost per piece", format_bdt(cost_sheet.hang_tag_cost_per_piece)])
        ws_summary.append(["Accessories cost per piece", format_bdt(cost_sheet.trim_cost_per_piece)])
        ws_summary.append(["Conveyance cost per piece", format_bdt(cost_sheet.conveyance_cost_per_piece)])
        ws_summary.append(["Sewing charge per piece", format_bdt(cost_sheet.labor_cost_per_piece)])
        ws_summary.append(["Overhead cost per piece", format_bdt(cost_sheet.overhead_cost_per_piece)])
        ws_summary.append(["Process cost per piece", format_bdt(cost_sheet.process_cost_per_piece)])
        ws_summary.append(["Packaging cost per piece", format_bdt(cost_sheet.packaging_cost_per_piece)])
        ws_summary.append(["Freight cost per piece", format_bdt(cost_sheet.freight_cost_per_piece)])
        ws_summary.append(["Testing cost per piece", format_bdt(cost_sheet.testing_cost_per_piece)])
        ws_summary.append(["Others cost per piece", format_bdt(cost_sheet.other_cost_per_piece)])

        ws_summary.append([])
        ws_summary.append(["Totals", "BDT", "CAD"])
        ws_summary.append([
            "Total cost per piece",
            format_bdt(calc["display"]["total_cost_per_piece"]),
            format_cad(cad_values["total_cost_per_piece"]) if cad_available else "",
        ])
        ws_summary.append([
            "Total order cost",
            format_bdt(calc["display"]["total_order_cost"]),
            format_cad(cad_values["total_order_cost"]) if cad_available else "",
        ])
        ws_summary.append([
            "Quote price per piece",
            format_bdt(calc["display"]["quote_price_per_piece"]),
            format_cad(cad_values["quote_price_per_piece"]) if cad_available else "",
        ])
        ws_summary.append([
            "Profit per piece",
            format_bdt(calc["display"]["profit_per_piece"]),
            format_cad(cad_values["profit_per_piece"]) if cad_available else "",
        ])
        ws_summary.append([
            "Total profit",
            format_bdt(calc["display"]["total_profit"]),
            format_cad(cad_values["total_profit"]) if cad_available else "",
        ])
        ws_summary.append(["Margin percent", float(calc["display"]["margin_percent"]), ""])

        ws_summary.append([])
        ws_summary.append(["Notes", cost_sheet.notes or "-"])

        ws_breakdown = wb.create_sheet("Breakdown")
        ws_breakdown.append(["Component", "Cost per piece (BDT)"])
        ws_breakdown.append(["Fabric (effective)", format_bdt(calc["display"]["fabric_effective_cost_per_piece"])])
        ws_breakdown.append(["Rib", format_bdt(cost_sheet.rib_cost_per_piece)])
        ws_breakdown.append(["Woven fabrics", format_bdt(cost_sheet.woven_fabric_cost_per_piece)])
        ws_breakdown.append(["Zipper", format_bdt(cost_sheet.zipper_cost_per_piece)])
        ws_breakdown.append(["Zipper puller", format_bdt(cost_sheet.zipper_puller_cost_per_piece)])
        ws_breakdown.append(["Button", format_bdt(cost_sheet.button_cost_per_piece)])
        ws_breakdown.append(["Thread", format_bdt(cost_sheet.thread_cost_per_piece)])
        ws_breakdown.append(["Lining", format_bdt(cost_sheet.lining_cost_per_piece)])
        ws_breakdown.append(["Velcro", format_bdt(cost_sheet.velcro_cost_per_piece)])
        ws_breakdown.append(["Neck tape", format_bdt(cost_sheet.neck_tape_cost_per_piece)])
        ws_breakdown.append(["Elastic", format_bdt(cost_sheet.elastic_cost_per_piece)])
        ws_breakdown.append(["Collar & cuff", format_bdt(cost_sheet.collar_cuff_cost_per_piece)])
        ws_breakdown.append(["Ring", format_bdt(cost_sheet.ring_cost_per_piece)])
        ws_breakdown.append(["Buckle/clip", format_bdt(cost_sheet.buckle_clip_cost_per_piece)])
        ws_breakdown.append(["Main label", format_bdt(cost_sheet.main_label_cost_per_piece)])
        ws_breakdown.append(["Care label", format_bdt(cost_sheet.care_label_cost_per_piece)])
        ws_breakdown.append(["Hang tag", format_bdt(cost_sheet.hang_tag_cost_per_piece)])
        ws_breakdown.append(["Accessories", format_bdt(cost_sheet.trim_cost_per_piece)])
        ws_breakdown.append(["Conveyance", format_bdt(cost_sheet.conveyance_cost_per_piece)])
        ws_breakdown.append(["Sewing charge", format_bdt(cost_sheet.labor_cost_per_piece)])
        ws_breakdown.append(["Overhead", format_bdt(cost_sheet.overhead_cost_per_piece)])
        ws_breakdown.append(["Process/Wash", format_bdt(cost_sheet.process_cost_per_piece)])
        ws_breakdown.append(["Packaging", format_bdt(cost_sheet.packaging_cost_per_piece)])
        ws_breakdown.append(["Freight/Export", format_bdt(cost_sheet.freight_cost_per_piece)])
        ws_breakdown.append(["Testing/Compliance", format_bdt(cost_sheet.testing_cost_per_piece)])
        ws_breakdown.append(["Others", format_bdt(cost_sheet.other_cost_per_piece)])

        output = io.BytesIO()
        wb.save(output)
        data = output.getvalue()
    except Exception:
        logger.exception("Failed to generate costing Excel", extra={"cost_sheet_id": cost_sheet.pk})
        messages.error(request, "Could not generate the Excel file. Please try again.")
        return redirect("cost_sheet_detail", pk=pk)

    filename = f"costing_{cost_sheet.opportunity.opportunity_id}_simple.xlsx"
    _save_export_document(cost_sheet, filename, data, "costing_excel", request.user)

    resp = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp.write(data)
    return resp
