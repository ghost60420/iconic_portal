import io
import json
import logging
from collections import defaultdict
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.core.files.base import ContentFile
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms_costing import CostingHeaderForm, CostingSMVForm, OpportunityDocumentForm
from .models import (
    CostingHeader,
    CostingLineItem,
    CostingSMV,
    CostingAuditLog,
    CostingSnapshot,
    NEW_COSTING_CATEGORY_CHOICES,
    NEW_COSTING_UOM_CHOICES,
    Opportunity,
    OpportunityDocument,
)
from .services.costing_currency import format_bdt
from .services.costing_engine import compute_costing, validate_costing
from .services.costing_workflow import (
    CostingWorkflowError,
    convert_costing_to_quotation,
    create_invoice_from_costing,
    get_costing_quote_amounts,
)
from .services.order_lifecycle import create_lifecycle_from_costing


logger = logging.getLogger(__name__)


DEFAULT_QUOTATION_TERMS = """For bulk orders, 50% advance confirms the order and 50% is due before shipment.

For samples, 100% payment is required before development begins.

Production starts after payment is cleared.

Any change after approval may affect price and timeline.

Shipping time may vary due to courier, customs, or international delay.

Import duties and local taxes are the buyer's responsibility unless agreed otherwise.

Any issue must be reported within 5 days of receiving goods.

All agreements are governed under the laws of British Columbia, Canada."""


def _can_approve(user):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    access = getattr(user, "access", None)
    return bool(access and access.can_costing_approve)


def _can_convert_to_invoice(user):
    return bool(user and user.is_authenticated and user.is_superuser)


def _quotation_company():
    return {
        "name": getattr(settings, "INVOICE_COMPANY_NAME", "Iconic Apparel House"),
        "email": getattr(settings, "INVOICE_COMPANY_EMAIL", "info@iconicapparelhouse.com"),
        "phone": getattr(settings, "INVOICE_COMPANY_PHONE", "604-500-6009"),
        "website": getattr(settings, "INVOICE_COMPANY_WEBSITE", "iconicapparelhouse.com"),
        "address": getattr(settings, "INVOICE_ADDRESS_CA", ""),
    }


def _quotation_context(costing, user=None):
    amounts = get_costing_quote_amounts(costing)
    return {
        "costing": costing,
        "amounts": amounts,
        "company": _quotation_company(),
        "terms": DEFAULT_QUOTATION_TERMS,
        "can_convert_to_invoice": _can_convert_to_invoice(user),
    }


def _workflow_context(costing, user=None):
    invoice = costing.invoices.select_related("order").order_by("-created_at", "-id").first()
    production_order = None
    if invoice and invoice.order_id:
        production_order = invoice.order
    if not production_order:
        production_order = costing.production_orders.order_by("-created_at", "-id").first()
    lifecycle = costing.order_lifecycles_as_quotation.order_by("-updated_at", "-id").first()
    if not lifecycle:
        lifecycle = costing.order_lifecycles_as_costing.order_by("-updated_at", "-id").first()
    return {
        "is_quotation": bool(costing.quotation_number and costing.quoted_at),
        "invoice": invoice,
        "production_order": production_order,
        "lifecycle": lifecycle,
        "can_convert_to_invoice": _can_convert_to_invoice(user),
    }


def _pdf_lines(pdf, text, max_width, font_name="Helvetica", font_size=9):
    words = (text or "").split()
    if not words:
        return [""]

    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if pdf.stringWidth(candidate, font_name, font_size) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _safe_costing_smv(costing):
    try:
        return costing.smv
    except CostingSMV.DoesNotExist:
        return None


def _build_line_dict(line=None, category=None):
    if line:
        return {
            "id": line.id,
            "category": line.category,
            "item_name": line.item_name,
            "item_reference": line.item_reference,
            "supplier": line.supplier,
            "uom": line.uom,
            "unit_price": line.unit_price,
            "freight": line.freight,
            "consumption_value": line.consumption_value,
            "wastage_percent": line.wastage_percent,
            "denominator_value": line.denominator_value if line.denominator_value is not None else "",
            "placement": line.placement,
            "color": line.color,
            "gsm": line.gsm,
            "cut_width": line.cut_width,
            "remarks": line.remarks,
            "sort_order": line.sort_order,
        }
    return {
        "id": None,
        "category": category or "other",
        "item_name": "",
        "item_reference": "",
        "supplier": "",
        "uom": "piece",
        "unit_price": "",
        "freight": "0",
        "consumption_value": "1",
        "wastage_percent": "0",
        "denominator_value": "1",
        "placement": "",
        "color": "",
        "gsm": "",
        "cut_width": "",
        "remarks": "",
        "sort_order": 0,
    }


def _parse_line_payload(raw):
    if raw is None:
        return []
    raw = raw.strip()
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    return payload


def _save_line_items(costing, payload):
    seen_ids = []
    for idx, row in enumerate(payload, start=1):
        item_name = (row.get("item_name") or "").strip()
        if not item_name:
            continue
        line_id = row.get("id")
        line = None
        if line_id:
            line = CostingLineItem.objects.filter(pk=line_id, costing=costing).first()
        if not line:
            line = CostingLineItem(costing=costing)
        line.category = (row.get("category") or "other").strip() or "other"
        line.item_name = item_name
        line.item_reference = (row.get("item_reference") or "").strip()
        line.supplier = (row.get("supplier") or "").strip()
        line.uom = (row.get("uom") or "piece").strip() or "piece"
        line.unit_price = row.get("unit_price") or 0
        line.freight = row.get("freight") or 0
        line.consumption_value = row.get("consumption_value") or 1
        line.wastage_percent = row.get("wastage_percent") or 0
        line.denominator_value = row.get("denominator_value") or 1
        line.placement = (row.get("placement") or "").strip()
        line.color = (row.get("color") or "").strip()
        line.gsm = (row.get("gsm") or "").strip()
        line.cut_width = (row.get("cut_width") or "").strip()
        line.remarks = (row.get("remarks") or "").strip()
        line.sort_order = int(row.get("sort_order") or idx)
        line.save()
        seen_ids.append(line.id)

    CostingLineItem.objects.filter(costing=costing).exclude(id__in=seen_ids).delete()


def _group_lines(costing):
    grouped = {key: [] for key, _ in NEW_COSTING_CATEGORY_CHOICES}
    for line in costing.line_items.all().order_by("category", "sort_order", "id"):
        grouped[line.category].append(_build_line_dict(line))
    for key in grouped:
        if not grouped[key]:
            grouped[key].append(_build_line_dict(category=key))
    return grouped


def _update_opportunity_summary(costing, calc):
    opp = costing.opportunity
    opp.costing_total_cost_per_piece = calc["total_cost_per_piece"]
    opp.costing_fob_per_piece = calc["fob_per_piece"]
    opp.costing_margin_percent = calc["margin_percent"]
    opp.costing_status = costing.status
    opp.save(update_fields=[
        "costing_total_cost_per_piece",
        "costing_fob_per_piece",
        "costing_margin_percent",
        "costing_status",
    ])


def _costing_header_initial(opportunity=None):
    initial = {
        "factory_location": "bd",
        "currency": "BDT",
        "costing_date": timezone.now().date(),
    }
    if not opportunity:
        return initial

    initial.update(
        {
            "opportunity": opportunity,
            "customer": opportunity.customer,
            "product_type": opportunity.product_type,
            "order_quantity": opportunity.moq_units or 0,
            "moq": opportunity.moq_units or 0,
            "brand": getattr(opportunity.lead, "account_brand", "") or "",
        }
    )
    return initial


def cost_sheet_list(request):
    qs = CostingHeader.objects.select_related("opportunity", "customer").order_by("-updated_at")

    customer_id = (request.GET.get("customer") or "").strip()
    product_type = (request.GET.get("product_type") or "").strip()
    status = (request.GET.get("status") or "").strip()
    search = (request.GET.get("q") or "").strip()
    if customer_id:
        qs = qs.filter(customer_id=customer_id)
    if product_type:
        qs = qs.filter(product_type=product_type)
    if status:
        qs = qs.filter(status=status)
    if search:
        qs = qs.filter(
            Q(opportunity__opportunity_id__icontains=search)
            | Q(customer__account_brand__icontains=search)
            | Q(style_name__icontains=search)
            | Q(style_code__icontains=search)
        )

    rows = []
    for sheet in qs:
        calc = compute_costing(sheet.id)
        if calc:
            margin_percent = calc.get("margin_percent") or Decimal("0")
            if margin_percent >= Decimal("20"):
                margin_tone = "good"
            elif margin_percent >= Decimal("5"):
                margin_tone = "watch"
            else:
                margin_tone = "risk"
            rows.append({"sheet": sheet, "calc": calc, "margin_tone": margin_tone})

    total_cost_order = sum((row["calc"].get("total_cost_order") or Decimal("0")) for row in rows)
    total_sales_order = sum((row["calc"].get("total_sales_order") or Decimal("0")) for row in rows)
    total_profit_order = sum((row["calc"].get("total_profit_order") or Decimal("0")) for row in rows)
    margin_values = [row["calc"].get("margin_percent") or Decimal("0") for row in rows]
    average_margin = (sum(margin_values) / Decimal(len(margin_values))) if margin_values else Decimal("0")
    customers_by_id = {
        row["sheet"].customer_id: row["sheet"].customer
        for row in rows
        if row["sheet"].customer_id and row["sheet"].customer
    }

    context = {
        "rows": rows,
        "customers": sorted(
            customers_by_id.values(),
            key=lambda customer: (customer.account_brand or customer.contact_name or "").lower(),
        ),
        "status_choices": [("draft", "Draft"), ("approved", "Approved")],
        "product_types": Opportunity.PRODUCT_TYPE_CHOICES,
        "summary": {
            "count": len(rows),
            "approved_count": sum(1 for row in rows if row["sheet"].status == "approved"),
            "draft_count": sum(1 for row in rows if row["sheet"].status == "draft"),
            "total_cost_order": total_cost_order,
            "total_sales_order": total_sales_order,
            "total_profit_order": total_profit_order,
            "average_margin": average_margin,
        },
        "selected": {
            "customer": customer_id,
            "product_type": product_type,
            "status": status,
            "q": search,
        },
    }
    return render(request, "crm/costing/costsheet_list.html", context)


def cost_sheet_create(request, opportunity_id=None):
    opportunity = None
    if opportunity_id:
        opportunity = get_object_or_404(Opportunity, pk=opportunity_id)

    if request.method == "POST":
        data = request.POST.copy()
        if opportunity:
            data["opportunity"] = opportunity.pk
            if opportunity.customer_id:
                data["customer"] = opportunity.customer_id
        form = CostingHeaderForm(data)
        if form.is_valid():
            costing = form.save(commit=False)
            if opportunity:
                costing.opportunity = opportunity
            costing.save()
            create_lifecycle_from_costing(costing, user=request.user)
            CostingAuditLog.objects.create(
                costing=costing,
                action="created",
                changed_by=request.user if request.user.is_authenticated else None,
            )
            messages.success(request, "Costing header created. Add line items next.")
            return redirect("cost_sheet_detail", pk=costing.pk)
        messages.error(request, "Please fix the errors below.")
    else:
        initial = _costing_header_initial(opportunity)
        form = CostingHeaderForm(initial=initial)

    if opportunity:
        if "opportunity" in form.fields:
            form.fields["opportunity"].disabled = True
        if "customer" in form.fields:
            form.fields["customer"].disabled = True

    context = {
        "form": form,
        "opportunity": opportunity,
        "mode": "create",
    }
    return render(request, "crm/costing/costsheet_form.html", context)


def cost_sheet_detail(request, pk):
    costing = get_object_or_404(
        CostingHeader.objects.select_related("opportunity", "customer").prefetch_related("line_items"),
        pk=pk,
    )
    can_approve = _can_approve(request.user)
    is_locked = costing.status == "approved"

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()

        if action == "save_costing":
            if is_locked:
                messages.error(request, "Costing is approved and locked.")
                return redirect("cost_sheet_detail", pk=pk)

            data = request.POST.copy()
            data["opportunity"] = costing.opportunity_id
            if costing.customer_id:
                data["customer"] = costing.customer_id

            form = CostingHeaderForm(data, instance=costing)
            smv_form = CostingSMVForm(data, instance=_safe_costing_smv(costing))
            if form.is_valid() and smv_form.is_valid():
                before = {
                    "order_quantity": costing.order_quantity,
                    "currency": costing.currency,
                    "exchange_rate": str(costing.exchange_rate or ""),
                    "finance_percent_fabric": str(costing.finance_percent_fabric),
                    "finance_percent_trims": str(costing.finance_percent_trims),
                    "commission_percent": str(costing.commission_percent),
                    "target_margin_percent": str(costing.target_margin_percent or ""),
                    "manual_fob_per_piece": str(costing.manual_fob_per_piece or ""),
                }
                header = form.save()
                smv = smv_form.save(commit=False)
                smv.costing = header
                smv.save()

                payload = _parse_line_payload(request.POST.get("line_payload"))
                _save_line_items(header, payload)

                calc = compute_costing(header.id)
                if calc:
                    _update_opportunity_summary(header, calc)
                create_lifecycle_from_costing(header, user=request.user)

                CostingAuditLog.objects.create(
                    costing=header,
                    action="updated",
                    changed_by=request.user if request.user.is_authenticated else None,
                    before_data=before,
                    after_data={
                        "order_quantity": header.order_quantity,
                        "currency": header.currency,
                        "exchange_rate": str(header.exchange_rate or ""),
                        "finance_percent_fabric": str(header.finance_percent_fabric),
                        "finance_percent_trims": str(header.finance_percent_trims),
                        "commission_percent": str(header.commission_percent),
                        "target_margin_percent": str(header.target_margin_percent or ""),
                        "manual_fob_per_piece": str(header.manual_fob_per_piece or ""),
                    },
                )

                messages.success(request, "Costing updated.")
            else:
                messages.error(request, "Please fix the errors below.")
            return redirect("cost_sheet_detail", pk=pk)

        if action == "approve":
            if not can_approve:
                messages.error(request, "You do not have permission to approve.")
                return redirect("cost_sheet_detail", pk=pk)

            calc = compute_costing(costing.id)
            errors, warnings = validate_costing(costing, calc)
            if errors:
                for err in errors:
                    messages.error(request, err)
                return redirect("cost_sheet_detail", pk=pk)
            for warn in warnings:
                messages.warning(request, warn)

            costing.status = "approved"
            costing.approved_by = request.user if request.user.is_authenticated else None
            costing.approved_at = timezone.now()
            costing.save(update_fields=["status", "approved_by", "approved_at"])
            create_lifecycle_from_costing(costing, user=request.user)

            if calc:
                _update_opportunity_summary(costing, calc)
                CostingSnapshot.objects.create(
                    costing=costing,
                    data={
                        "total_cost_per_piece": str(calc["total_cost_per_piece"]),
                        "fob_per_piece": str(calc["fob_per_piece"]),
                        "margin_percent": str(calc["margin_percent"]),
                        "total_cost_order": str(calc["total_cost_order"]),
                        "total_sales_order": str(calc["total_sales_order"]),
                        "total_profit_order": str(calc["total_profit_order"]),
                        "breakdown": {k: str(v) for k, v in calc["breakdown"].items()},
                    },
                )

            CostingAuditLog.objects.create(
                costing=costing,
                action="approved",
                changed_by=request.user if request.user.is_authenticated else None,
            )
            messages.success(request, "Costing approved and locked.")
            return redirect("cost_sheet_detail", pk=pk)

        if action == "unlock":
            if not can_approve:
                messages.error(request, "You do not have permission to unlock.")
                return redirect("cost_sheet_detail", pk=pk)
            reason = (request.POST.get("unlock_reason") or "").strip()
            if not reason:
                messages.error(request, "Unlock reason is required.")
                return redirect("cost_sheet_detail", pk=pk)
            costing.status = "draft"
            costing.approved_by = None
            costing.approved_at = None
            costing.save(update_fields=["status", "approved_by", "approved_at"])
            CostingAuditLog.objects.create(
                costing=costing,
                action="unlocked",
                changed_by=request.user if request.user.is_authenticated else None,
                note=reason,
            )
            messages.success(request, "Costing unlocked.")
            return redirect("cost_sheet_detail", pk=pk)

        if action == "upload_document":
            form = OpportunityDocumentForm(request.POST, request.FILES)
            if form.is_valid():
                doc = form.save(commit=False)
                doc.opportunity = costing.opportunity
                doc.costing_header = costing
                doc.uploaded_by = request.user if request.user.is_authenticated else None
                doc.save()
                CostingAuditLog.objects.create(
                    costing=costing,
                    action="uploaded_file",
                    changed_by=request.user if request.user.is_authenticated else None,
                    note=doc.original_name or doc.file.name,
                )
                messages.success(request, "Document uploaded.")
            else:
                messages.error(request, "Please choose a file and type.")
            return redirect("cost_sheet_detail", pk=pk)

    calc = compute_costing(costing.id)
    margin_tone = "neutral"
    if calc:
        margin_percent = calc.get("margin_percent") or Decimal("0")
        if margin_percent >= Decimal("20"):
            margin_tone = "good"
        elif margin_percent >= Decimal("5"):
            margin_tone = "watch"
        else:
            margin_tone = "risk"
    grouped_lines = _group_lines(costing)
    section_total_labels = {
        "fabric": "Fabric Total",
        "sewing_trim": "Sewing Trims Total",
        "packaging_trim": "Packaging Total",
        "labels_branding": "Labels Total",
        "wash_process": "Wash / Process Total",
        "cm_labor": "CM / Labor Total",
        "logistics_compliance": "Logistics Total",
        "other": "Other Total",
    }
    category_sections = [
        {
            "key": key,
            "label": label,
            "total_label": section_total_labels.get(key, f"{label} Total"),
            "rows": grouped_lines.get(key, []),
        }
        for key, label in NEW_COSTING_CATEGORY_CHOICES
    ]

    documents = OpportunityDocument.objects.filter(
        opportunity=costing.opportunity,
        doc_type__in=["costing_pdf", "costing_excel", "costing_other"],
    ).order_by("-uploaded_at")
    audits = costing.audits.select_related("changed_by").all()[:8]
    snapshots = costing.snapshots.all()[:6]

    form = CostingHeaderForm(instance=costing)
    smv_form = CostingSMVForm(instance=_safe_costing_smv(costing))
    if "opportunity" in form.fields:
        form.fields["opportunity"].disabled = True
    if "customer" in form.fields:
        form.fields["customer"].disabled = True

    context = {
        "costing": costing,
        "calc": calc,
        "margin_tone": margin_tone,
        "form": form,
        "smv_form": smv_form,
        "documents": documents,
        "audits": audits,
        "snapshots": snapshots,
        "document_form": OpportunityDocumentForm(),
        "grouped_lines": grouped_lines,
        "category_sections": category_sections,
        "category_choices": NEW_COSTING_CATEGORY_CHOICES,
        "uom_choices": NEW_COSTING_UOM_CHOICES,
        "can_approve": can_approve,
        "is_locked": is_locked,
        "workflow": _workflow_context(costing, request.user),
    }
    return render(request, "crm/costing/costsheet_detail.html", context)


@require_POST
def cost_sheet_convert_to_quotation(request, pk):
    costing = get_object_or_404(CostingHeader.objects.select_related("opportunity", "customer"), pk=pk)
    if not _can_approve(request.user):
        messages.error(request, "You do not have permission to convert this costing to a quotation.")
        return redirect("cost_sheet_detail", pk=pk)

    try:
        convert_costing_to_quotation(costing, user=request.user)
    except CostingWorkflowError as exc:
        messages.error(request, str(exc))
        return redirect("cost_sheet_detail", pk=pk)

    messages.success(request, f"Quotation {costing.quotation_number} is ready.")
    return redirect("cost_sheet_client_quotation", pk=pk)


def cost_sheet_client_quotation(request, pk):
    costing = get_object_or_404(CostingHeader.objects.select_related("opportunity", "customer"), pk=pk)
    if costing.status != "approved":
        messages.error(request, "Approve the costing before viewing the client quotation.")
        return redirect("cost_sheet_detail", pk=pk)
    if not costing.quotation_number:
        messages.error(request, "Convert this approved costing to a quotation first.")
        return redirect("cost_sheet_detail", pk=pk)

    try:
        context = _quotation_context(costing, request.user)
    except CostingWorkflowError as exc:
        messages.error(request, str(exc))
        return redirect("cost_sheet_detail", pk=pk)

    return render(request, "crm/costing/quotation_client.html", context)


def cost_sheet_quotation_pdf(request, pk):
    costing = get_object_or_404(CostingHeader.objects.select_related("opportunity", "customer"), pk=pk)
    if costing.status != "approved" or not costing.quotation_number:
        messages.error(request, "Convert this approved costing to a quotation before downloading the quotation PDF.")
        return redirect("cost_sheet_detail", pk=pk)

    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
    except ImportError:
        messages.error(request, "PDF export is unavailable. Please install ReportLab.")
        return redirect("cost_sheet_client_quotation", pk=pk)

    try:
        context = _quotation_context(costing, request.user)
        amounts = context["amounts"]
        company = context["company"]
        buffer = io.BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=letter, pageCompression=0)
        width, height = letter
        left = 46
        right = width - 46
        y = height - 46

        pdf.setFillColor(colors.HexColor("#111827"))
        pdf.rect(0, height - 104, width, 104, fill=1, stroke=0)
        pdf.setFillColor(colors.white)
        pdf.setFont("Helvetica-Bold", 18)
        pdf.drawString(left, height - 58, company["name"])
        pdf.setFont("Helvetica", 9)
        pdf.drawString(left, height - 76, "Professional apparel manufacturing quotation")
        pdf.setFont("Helvetica-Bold", 18)
        pdf.drawRightString(right, height - 58, "QUOTATION")
        pdf.setFont("Helvetica", 9)
        pdf.drawRightString(right, height - 76, f"Quote # {costing.quotation_number}")

        y = height - 132
        pdf.setFillColor(colors.HexColor("#111827"))
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(left, y, "From")
        pdf.drawString(width / 2 + 12, y, "Quotation Details")
        y -= 15

        pdf.setFont("Helvetica", 9)
        company_lines = [
            company.get("name", ""),
            company.get("address", ""),
            " | ".join(part for part in [company.get("phone", ""), company.get("email", "")] if part),
            company.get("website", ""),
        ]
        detail_lines = [
            f"Quote date: {costing.quoted_at:%Y-%m-%d}" if costing.quoted_at else f"Quote date: {timezone.localdate():%Y-%m-%d}",
            f"Opportunity: {costing.opportunity.opportunity_id}",
            f"Currency: {costing.currency}",
            f"Reference: {costing.quotation_number}",
        ]
        row_y = y
        for line in [line for line in company_lines if line]:
            pdf.drawString(left, row_y, line[:84])
            row_y -= 12
        row_y = y
        for line in detail_lines:
            pdf.drawString(width / 2 + 12, row_y, line[:70])
            row_y -= 12

        y -= 78
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(left, y, "Customer")
        y -= 15
        pdf.setFont("Helvetica", 9)
        customer = costing.customer
        customer_lines = [
            (getattr(customer, "account_brand", "") or getattr(customer, "contact_name", "") or "Customer") if customer else "Customer",
            getattr(customer, "contact_name", "") if customer else "",
            getattr(customer, "email", "") if customer else "",
            getattr(customer, "phone", "") if customer else "",
        ]
        for line in [line for line in customer_lines if line]:
            pdf.drawString(left, y, line[:92])
            y -= 12

        y -= 16
        pdf.setFillColor(colors.HexColor("#f3f4f6"))
        pdf.rect(left, y - 98, right - left, 118, fill=1, stroke=0)
        pdf.setFillColor(colors.HexColor("#111827"))
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(left + 10, y, "Item")
        pdf.drawRightString(right - 190, y, "Qty")
        pdf.drawRightString(right - 95, y, "Unit Price")
        pdf.drawRightString(right - 10, y, "Amount")
        y -= 20
        pdf.setFont("Helvetica", 9)
        description = costing.style_name or costing.style_code or costing.get_product_type_display()
        pdf.drawString(left + 10, y, description[:58])
        pdf.drawRightString(right - 190, y, f"{amounts['quantity']}")
        pdf.drawRightString(right - 95, y, f"{costing.currency} {amounts['unit_price']:,.2f}")
        pdf.drawRightString(right - 10, y, f"{costing.currency} {amounts['order_total']:,.2f}")
        y -= 18
        specs = " | ".join(
            part
            for part in [
                f"Fabric: {costing.fabric_type}" if costing.fabric_type else "",
                f"GSM: {costing.fabric_gsm}" if costing.fabric_gsm else "",
                f"Composition: {costing.fabric_composition}" if costing.fabric_composition else "",
                f"Packaging: {costing.packaging_type}" if costing.packaging_type else "",
            ]
            if part
        )
        pdf.drawString(left + 10, y, (specs or "Apparel production quotation")[:110])
        y -= 38
        pdf.setFont("Helvetica-Bold", 12)
        pdf.drawRightString(right - 10, y, f"Total: {costing.currency} {amounts['order_total']:,.2f}")

        y -= 44
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(left, y, "Terms and Conditions")
        y -= 14
        pdf.setFont("Helvetica", 8.5)
        for paragraph in DEFAULT_QUOTATION_TERMS.splitlines():
            if not paragraph.strip():
                y -= 5
                continue
            for line in _pdf_lines(pdf, paragraph, right - left, "Helvetica", 8.5):
                if y < 48:
                    pdf.showPage()
                    y = height - 46
                    pdf.setFont("Helvetica", 8.5)
                pdf.drawString(left, y, line)
                y -= 11

        pdf.showPage()
        pdf.save()
        data = buffer.getvalue()
    except CostingWorkflowError as exc:
        messages.error(request, str(exc))
        return redirect("cost_sheet_detail", pk=pk)
    except Exception:
        logger.exception("Failed to generate client quotation PDF", extra={"costing_header": costing.pk})
        messages.error(request, "Could not generate the quotation PDF. Please try again.")
        return redirect("cost_sheet_client_quotation", pk=pk)

    filename = f"quotation_{costing.quotation_number or costing.pk}.pdf"
    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write(data)
    return response


@require_POST
def cost_sheet_convert_to_invoice(request, pk):
    costing = get_object_or_404(CostingHeader.objects.select_related("opportunity", "customer"), pk=pk)
    if not _can_convert_to_invoice(request.user):
        messages.error(request, "Only invoice managers can convert quotations to invoices.")
        return redirect("cost_sheet_detail", pk=pk)

    try:
        invoice, created = create_invoice_from_costing(costing, user=request.user)
    except CostingWorkflowError as exc:
        messages.error(request, str(exc))
        return redirect("cost_sheet_detail", pk=pk)

    if created:
        messages.success(request, f"Invoice {invoice.invoice_number} created from quotation.")
    else:
        messages.info(request, f"Invoice {invoice.invoice_number} already exists for this quotation.")
    return redirect("invoice_view", pk=invoice.pk)


def cost_sheet_duplicate(request, pk):
    costing = get_object_or_404(
        CostingHeader.objects.select_related("opportunity", "customer").prefetch_related("line_items"),
        pk=pk,
    )
    new_costing = CostingHeader.objects.create(
        opportunity=costing.opportunity,
        customer=costing.customer,
        buyer=costing.buyer,
        brand=costing.brand,
        style_name=costing.style_name,
        style_code=costing.style_code,
        product_type=costing.product_type,
        gender=costing.gender,
        size_range=costing.size_range,
        season=costing.season,
        factory_location=costing.factory_location,
        order_quantity=costing.order_quantity,
        moq=costing.moq,
        costing_date=costing.costing_date,
        currency=costing.currency,
        exchange_rate=costing.exchange_rate,
        finance_percent_fabric=costing.finance_percent_fabric,
        finance_percent_trims=costing.finance_percent_trims,
        commission_percent=costing.commission_percent,
        target_margin_percent=costing.target_margin_percent,
        manual_fob_per_piece=costing.manual_fob_per_piece,
        merchandiser=costing.merchandiser,
        fabric_type=costing.fabric_type,
        fabric_gsm=costing.fabric_gsm,
        fabric_composition=costing.fabric_composition,
        wash_type=costing.wash_type,
        print_type=costing.print_type,
        embroidery=costing.embroidery,
        label_type=costing.label_type,
        packaging_type=costing.packaging_type,
        special_trims=costing.special_trims,
        fit_remarks=costing.fit_remarks,
        notes=costing.notes,
        status="draft",
    )

    for line in costing.line_items.all():
        line.pk = None
        line.costing = new_costing
        line.save()

    smv = _safe_costing_smv(costing)
    if smv:
        CostingSMV.objects.create(
            costing=new_costing,
            machine_smv=smv.machine_smv,
            finishing_smv=smv.finishing_smv,
            cpm=smv.cpm,
            efficiency_costing=smv.efficiency_costing,
            efficiency_planned=smv.efficiency_planned,
        )

    CostingAuditLog.objects.create(
        costing=new_costing,
        action="created",
        changed_by=request.user if request.user.is_authenticated else None,
        note=f"Duplicated from COST-{costing.pk}",
    )
    messages.success(request, "Costing duplicated. You are now editing the new version.")
    return redirect("cost_sheet_detail", pk=new_costing.pk)


def _save_export_document(costing, filename, data, doc_type, user):
    try:
        OpportunityDocument.objects.create(
            opportunity=costing.opportunity,
            costing_header=costing,
            file=ContentFile(data, name=filename),
            original_name=filename,
            doc_type=doc_type,
            uploaded_by=user if user and user.is_authenticated else None,
        )
        CostingAuditLog.objects.create(
            costing=costing,
            action="exported",
            changed_by=user if user and user.is_authenticated else None,
            note=filename,
        )
    except Exception:
        logger.exception("Failed to save costing export document", extra={"costing_header": costing.pk})


def cost_sheet_export_pdf(request, pk):
    costing = get_object_or_404(
        CostingHeader.objects.select_related("opportunity", "customer").prefetch_related("line_items"),
        pk=pk,
    )

    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
    except ImportError:
        messages.error(request, "PDF export is unavailable. Please install ReportLab.")
        return redirect("cost_sheet_detail", pk=pk)

    try:
        calc = compute_costing(costing.id)
        buffer = io.BytesIO()
        p = canvas.Canvas(buffer, pagesize=letter)
        width, height = letter
        y = height - 50

        p.setFillColor(colors.HexColor("#111827"))
        p.rect(0, height - 88, width, 88, fill=1, stroke=0)
        p.setFillColor(colors.white)
        p.setFont("Helvetica-Bold", 18)
        p.drawString(50, height - 44, "ICONIC CRM")
        p.setFont("Helvetica", 11)
        p.drawString(50, height - 62, "Professional FOB Quotation")
        p.setFont("Helvetica-Bold", 12)
        p.drawRightString(width - 50, height - 44, f"COST-{costing.pk}")
        p.setFont("Helvetica", 9)
        p.drawRightString(width - 50, height - 62, timezone.now().strftime("%Y-%m-%d"))
        p.setFillColor(colors.black)
        y = height - 116

        p.setFont("Helvetica", 10)
        header_lines = [
            f"Customer: {(costing.customer.account_brand if costing.customer else '') or 'Not set'}",
            f"Opportunity: {costing.opportunity.opportunity_id}",
            f"Style: {costing.style_name or costing.style_code or '-'}",
            f"Product type: {costing.get_product_type_display()}",
            f"Quantity: {costing.order_quantity}",
            f"Factory location: {costing.get_factory_location_display()}",
            f"Approval status: {costing.get_status_display()}",
            f"Currency: {costing.currency}",
        ]
        for line in header_lines:
            p.drawString(50, y, line)
            y -= 14

        y -= 10
        p.setFillColor(colors.HexColor("#f3f4f6"))
        p.rect(45, y - 76, width - 90, 92, fill=1, stroke=0)
        p.setFillColor(colors.black)
        p.setFont("Helvetica-Bold", 12)
        p.drawString(55, y, "FOB Summary")
        y -= 16
        p.setFont("Helvetica", 10)
        summary_lines = [
            f"Total cost per piece: {format_bdt(calc['display']['total_cost_per_piece'])}",
            f"FOB per piece: {format_bdt(calc['display']['fob_per_piece'])}",
            f"Profit per piece: {format_bdt(calc['display']['profit_per_piece'])}",
            f"Margin %: {calc['display']['margin_percent']}",
            f"Total cost order: {format_bdt(calc['display']['total_cost_order'])}",
            f"Final offer total: {format_bdt(calc['display']['total_final_offer_order'])}",
        ]
        for line in summary_lines:
            p.drawString(55, y, line)
            y -= 14

        y -= 6
        p.setFont("Helvetica-Bold", 11)
        p.drawString(50, y, "Line items")
        y -= 14
        p.setFont("Helvetica", 9)

        for category, _ in NEW_COSTING_CATEGORY_CHOICES:
            items = [row for row in calc["line_rows"] if row["category"] == category]
            if not items:
                continue
            p.setFont("Helvetica-Bold", 10)
            p.drawString(50, y, category.replace("_", " ").title())
            y -= 12
            p.setFont("Helvetica", 9)
            for item in items:
                line = f"{item['item_name']} | {item['uom']} | {format_bdt(item['cost_per_piece'])}"
                p.drawString(60, y, line[:110])
                y -= 12
                if y < 80:
                    p.showPage()
                    y = height - 50
                    p.setFont("Helvetica", 9)
            y -= 6

        y -= 6
        p.setFont("Helvetica-Bold", 11)
        p.drawString(50, y, "Notes")
        y -= 14
        p.setFont("Helvetica", 9)
        p.drawString(50, y, (costing.notes or "-")[:120])

        p.showPage()
        p.save()
        pdf_bytes = buffer.getvalue()
    except Exception:
        logger.exception("Failed to generate costing PDF", extra={"costing_header": costing.pk})
        messages.error(request, "Could not generate the PDF. Please try again.")
        return redirect("cost_sheet_detail", pk=pk)

    filename = f"costing_{costing.opportunity.opportunity_id}.pdf"
    _save_export_document(costing, filename, pdf_bytes, "costing_pdf", request.user)

    resp = HttpResponse(content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp.write(pdf_bytes)
    return resp


def cost_sheet_export_excel(request, pk):
    costing = get_object_or_404(
        CostingHeader.objects.select_related("opportunity", "customer").prefetch_related("line_items"),
        pk=pk,
    )

    try:
        from openpyxl import Workbook
    except Exception:
        messages.error(request, "Excel export is unavailable. Please install openpyxl.")
        return redirect("cost_sheet_detail", pk=pk)

    try:
        calc = compute_costing(costing.id)
        wb = Workbook()
        ws_summary = wb.active
        ws_summary.title = "Summary"

        ws_summary.append(["Customer", (costing.customer.account_brand if costing.customer else "") or "Not set"])
        ws_summary.append(["Opportunity", costing.opportunity.opportunity_id])
        ws_summary.append(["Style name", costing.style_name or "-"])
        ws_summary.append(["Style code", costing.style_code or "-"])
        ws_summary.append(["Product type", costing.get_product_type_display()])
        ws_summary.append(["Quantity", costing.order_quantity])
        ws_summary.append(["Factory location", costing.get_factory_location_display()])
        ws_summary.append(["Status", costing.get_status_display()])
        ws_summary.append(["Currency", costing.currency])
        ws_summary.append(["Exchange rate", format_bdt(costing.exchange_rate) if costing.exchange_rate else ""])

        ws_summary.append([])
        ws_summary.append(["Total cost per piece", format_bdt(calc["display"]["total_cost_per_piece"])])
        ws_summary.append(["FOB per piece", format_bdt(calc["display"]["fob_per_piece"])])
        ws_summary.append(["Profit per piece", format_bdt(calc["display"]["profit_per_piece"])])
        ws_summary.append(["Margin %", float(calc["display"]["margin_percent"])])
        ws_summary.append(["Total cost order", format_bdt(calc["display"]["total_cost_order"])])
        ws_summary.append(["Total sales order", format_bdt(calc["display"]["total_sales_order"])])
        ws_summary.append(["Total profit order", format_bdt(calc["display"]["total_profit_order"])])

        ws_lines = wb.create_sheet("Line items")
        ws_lines.append([
            "Category",
            "Item",
            "UOM",
            "Unit price",
            "Freight",
            "Consumption",
            "Wastage %",
            "Denominator",
            "Cost per piece",
        ])
        for row in calc["line_rows"]:
            ws_lines.append([
                row["category"],
                row["item_name"],
                row["uom"],
                float(row["unit_price"]),
                float(row["freight"]),
                float(row["consumption_value"]),
                float(row["wastage_percent"]),
                float(row["denominator_value"] or 0),
                float(row["cost_per_piece"]),
            ])

        output = io.BytesIO()
        wb.save(output)
        data = output.getvalue()
    except Exception:
        logger.exception("Failed to generate costing Excel", extra={"costing_header": costing.pk})
        messages.error(request, "Could not generate the Excel file. Please try again.")
        return redirect("cost_sheet_detail", pk=pk)

    filename = f"costing_{costing.opportunity.opportunity_id}.xlsx"
    _save_export_document(costing, filename, data, "costing_excel", request.user)

    resp = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp.write(data)
    return resp


def cost_sheet_dashboard(request):
    qs = CostingHeader.objects.select_related("customer", "opportunity").order_by("-updated_at")

    approved_only = (request.GET.get("approved") or "").strip() == "1"
    customer_id = (request.GET.get("customer") or "").strip()
    product_type = (request.GET.get("product_type") or "").strip()
    factory_location = (request.GET.get("factory_location") or "").strip()
    start_date = (request.GET.get("start") or "").strip()
    end_date = (request.GET.get("end") or "").strip()

    if approved_only:
        qs = qs.filter(status="approved")
    if customer_id:
        qs = qs.filter(customer_id=customer_id)
    if product_type:
        qs = qs.filter(product_type=product_type)
    if factory_location:
        qs = qs.filter(factory_location=factory_location)
    if start_date:
        qs = qs.filter(updated_at__date__gte=start_date)
    if end_date:
        qs = qs.filter(updated_at__date__lte=end_date)

    rows = []
    for cost in qs:
        calc = compute_costing(cost.id)
        if calc:
            rows.append(calc)

    top_profit = sorted(rows, key=lambda r: r["total_profit_order"], reverse=True)[:10]
    lowest_margin = sorted(rows, key=lambda r: r["margin_percent"])[:10]

    breakdown_totals = defaultdict(Decimal)
    for row in rows:
        breakdown_totals["fabric"] += row["fabric_base"]
        breakdown_totals["trims"] += row["trims_base"]
        breakdown_totals["labor"] += row["labor_cost_per_piece"]
        breakdown_totals["other"] += row["other_base"]

    trend = defaultdict(list)
    for row in rows:
        key = row["costing"].updated_at.strftime("%Y-%m")
        trend[key].append(row["total_cost_per_piece"])

    trend_labels = sorted(trend.keys())
    trend_values = [float(sum(trend[k]) / len(trend[k])) for k in trend_labels]

    customer_summary = defaultdict(list)
    for row in rows:
        customer = row["costing"].customer
        if not customer:
            continue
        customer_summary[customer].append(row)

    customer_rows = []
    for customer, items in customer_summary.items():
        total_qty = sum(r["order_quantity"] for r in items)
        avg_margin = sum(r["margin_percent"] for r in items) / Decimal(len(items)) if items else Decimal("0")
        customer_rows.append(
            {
                "customer": customer,
                "total_qty": total_qty,
                "avg_margin": avg_margin,
            }
        )

    context = {
        "top_profit": top_profit,
        "lowest_margin": lowest_margin,
        "breakdown_totals": breakdown_totals,
        "trend_labels": trend_labels,
        "trend_values": trend_values,
        "customer_rows": customer_rows,
        "filters": {
            "approved": approved_only,
            "customer": customer_id,
            "product_type": product_type,
            "factory_location": factory_location,
            "start": start_date,
            "end": end_date,
        },
        "product_types": Opportunity.PRODUCT_TYPE_CHOICES,
        "factory_locations": [
            ("bd", "Bangladesh"),
            ("ca", "Canada"),
            ("other", "Other"),
        ],
        "customers": list({row["costing"].customer for row in rows if row["costing"].customer}),
    }
    return render(request, "crm/costing/costing_dashboard.html", context)


def cost_sheet_reports(request):
    qs = CostingHeader.objects.select_related("customer", "opportunity").order_by("-updated_at")
    export = (request.GET.get("export") or "").strip()

    rows = []
    for cost in qs:
        calc = compute_costing(cost.id)
        if calc:
            rows.append(calc)

    if export:
        output = io.StringIO()
        if export == "list":
            output.write("Opportunity,Customer,Style,Qty,Cost per piece,FOB per piece,Margin %\n")
            for row in rows:
                cost = row["costing"]
                output.write(
                    f"{cost.opportunity.opportunity_id},{(cost.customer.account_brand if cost.customer else '')},{cost.style_name},{row['order_quantity']},{row['total_cost_per_piece']},{row['fob_per_piece']},{row['margin_percent']}\n"
                )
        elif export == "margin":
            output.write("Opportunity,Style,Margin %,Total profit\n")
            for row in rows:
                cost = row["costing"]
                output.write(
                    f"{cost.opportunity.opportunity_id},{cost.style_name},{row['margin_percent']},{row['total_profit_order']}\n"
                )
        elif export == "finance":
            output.write("Month,Fabric finance,Trim finance\n")
            month_totals = defaultdict(lambda: {"fabric": Decimal("0"), "trims": Decimal("0")})
            for row in rows:
                key = row["costing"].updated_at.strftime("%Y-%m")
                month_totals[key]["fabric"] += row["fabric_finance"] * Decimal(row["order_quantity"])
                month_totals[key]["trims"] += row["trims_finance"] * Decimal(row["order_quantity"])
            for key in sorted(month_totals.keys()):
                output.write(f"{key},{month_totals[key]['fabric']},{month_totals[key]['trims']}\n")
        else:
            output.write("Style,Old cost per piece,New cost per piece,Delta\n")
            by_style = defaultdict(list)
            for row in rows:
                by_style[row["costing"].style_code or row["costing"].style_name].append(row)
            for style, items in by_style.items():
                if len(items) < 2:
                    continue
                items_sorted = sorted(items, key=lambda r: r["costing"].updated_at)
                old = items_sorted[0]["total_cost_per_piece"]
                new = items_sorted[-1]["total_cost_per_piece"]
                output.write(f"{style},{old},{new},{new - old}\n")

        resp = HttpResponse(content_type="text/csv")
        resp["Content-Disposition"] = f'attachment; filename="costing_{export}_report.csv"'
        resp.write(output.getvalue())
        return resp

    context = {
        "rows": rows,
    }
    return render(request, "crm/costing/costing_reports.html", context)


def cost_sheet_guide(request):
    return render(request, "crm/costing/costing_guide.html")
