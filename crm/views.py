# crm/views.py

import json
import logging
import re
import os
from collections import Counter, defaultdict
from datetime import timedelta, date
from decimal import Decimal
from types import SimpleNamespace
from django.apps import apps
from django.db.models import Count, Exists, F, Sum, Q, Max, OuterRef, Prefetch, Subquery, prefetch_related_objects
from django.db import models
from django.conf import settings
try:
    from openai import OpenAI
except Exception:
    OpenAI = None
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.mail import send_mail
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction, IntegrityError, connection
from django.db.utils import DataError, OperationalError, ProgrammingError
from django.db.models import Case, Count, IntegerField, Q, When
from django.db.models.functions import Coalesce, TruncDate, TruncMonth, TruncYear
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import NoReverseMatch, reverse
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from .models import Product, Fabric, Accessory, Trim, ThreadOption, InventoryItem, InventoryMovement, InventoryReorder, ProductionOrderMaterial

from .services.costing import build_variance_report, calculate_cost_sheet
from .services.costing_currency import (
    CurrencyConversionError,
    convert_currency,
    currency_summary_rows,
    format_compact_finance_money,
    format_finance_money,
)
from .services.costing_engine import compute_costing
from .services.order_lifecycle import (
    build_lifecycle_profit_breakdown,
    can_view_lifecycle_profit,
    create_lifecycle_from_production,
    create_lifecycle_from_shipping,
    lifecycle_currency,
    lifecycle_dashboard_metrics,
)
from .services.production_operational_status import (
    OPERATIONAL_ACTIVE_STATUSES,
    OPERATIONAL_FINISHED_STATUSES,
    OPERATIONAL_STATUS_APPROVED,
    OPERATIONAL_STATUS_CANCELLED,
    OPERATIONAL_STATUS_CUTTING,
    OPERATIONAL_STATUS_FABRIC_SOURCING,
    OPERATIONAL_STATUS_LABELS,
    OPERATIONAL_STATUS_PACKING,
    OPERATIONAL_STATUS_PLANNING,
    OPERATIONAL_STATUS_PRINTING,
    OPERATIONAL_STATUS_QC,
    OPERATIONAL_STATUS_READY_TO_SHIP,
    OPERATIONAL_STATUS_SAMPLE_SENT,
    OPERATIONAL_STATUS_SAMPLE_DEVELOPMENT,
    OPERATIONAL_STATUS_SEWING,
    OPERATIONAL_STATUS_SHIPPED,
    OPERATIONAL_STATUS_VALUES,
    get_production_operational_status,
    sync_operational_status,
)
from .services.product_reference_images import (
    attach_primary_reference_images_to_leads,
    attach_primary_reference_images_to_opportunities,
    attach_primary_reference_images_to_production_orders,
    link_reference_images_to_opportunity,
    link_reference_images_to_production,
    product_snapshot_for_lead,
    product_snapshot_for_opportunity,
    product_snapshot_for_production,
    reference_image_payload_from_cleaned_data,
    reference_image_payload_from_request,
    reference_images_for_lead,
    reference_images_for_opportunity,
    reference_images_for_production,
    save_reference_images_for_lead,
)
from .services.workflow_visibility import build_workflow_visibility_context
from .services.historical_dates import (
    INVOICE_REPORTING_DATE_ALIAS,
    OPPORTUNITY_REPORTING_DATE_ALIAS,
    can_edit_historical_dates,
    with_invoice_reporting_date,
    with_opportunity_reporting_date,
)
from .services.automation_engine import automation_dashboard_context
from .services.operations_dashboard import operations_dashboard_context
from .services.pipeline import (
    CLOSED_PIPELINE_STAGES,
    open_pipeline_queryset,
    summarize_pipeline,
    with_pipeline_value,
)
from .services.operations_permissions import (
    active_sales_lead_q,
    available_sales_lead_q,
    can_access_operations_module,
    can_approve_costing,
    can_claim_sales_lead,
    can_manage_all_sales_records,
    can_view_local_sewing_financials,
    can_release_sales_lead,
    is_available_sales_lead,
    scope_owned_sales_leads,
    scope_production_orders,
    scope_sales_lead_queue,
    scope_sales_leads,
    scope_sales_opportunities,
)
from .services.production_orders import (
    ProductionOrderCreationError,
    create_production_order_from_approved_quotation,
    create_production_order_from_paid_full_package_quick_costing,
    paid_full_package_quick_costing_source_for_opportunity,
)
from .services.local_sewing import (
    calculate_local_sewing,
    is_bangladesh_local_sewing,
    summarize_local_sewing_orders,
    summarize_production_business_models,
)
from .services.audit_log import model_snapshot, schedule_audit
from .services.platform_tools import can_manage_archives, dashboard_personalization
from .services.chatter_mentions import notify_chatter_mentions
from .services.chatter_permissions import (
    can_access_chatter_record,
    resolve_chatter_target,
    visible_chatter_comments,
)
from .services.employee_profiles import employee_display_name
from .services.employee_identity import (
    employee_lead_ownership_q,
    employee_profile_ids_matching,
    get_employee_identity_index,
    known_employee_owner_q,
    resolve_employee_identity,
    resolve_lead_owner,
)
from .services.calendar_notifications import (
    calendar_event_signature,
    queue_calendar_invite_email,
)

def _parse_decimal(value):
    try:
        return Decimal(str(value).strip())
    except Exception:
        return Decimal("0")

def _safe_decimal_or_none(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except Exception:
        return None

def _calc_order_value_bdt(order_amount, fx_rate, currency="USD"):
    if order_amount is None:
        return None
    currency = (currency or "USD").upper()
    if currency == "BDT":
        return order_amount
    if fx_rate is None:
        return None
    try:
        if currency == "CAD":
            return convert_currency(order_amount, "CAD", "BDT", bdt_per_cad=fx_rate)
        return convert_currency(order_amount, "USD", "BDT", bdt_per_usd=fx_rate)
    except CurrencyConversionError:
        return None


def _opportunity_currency_summary(opportunity):
    currency = (getattr(opportunity, "order_currency", "") or "CAD").upper()
    if currency not in {"CAD", "USD", "BDT"}:
        currency = "CAD"
    amount = getattr(opportunity, "order_value_usd", None)
    total_bdt = getattr(opportunity, "order_value", None)
    rate = getattr(opportunity, "fx_rate_bdt_per_usd", None)
    moq = getattr(opportunity, "moq_units", None) or 0
    total_cad = None
    if currency == "CAD" and amount is not None:
        total_cad = amount
    elif currency == "BDT" and amount is not None and rate:
        try:
            total_cad = convert_currency(amount, "BDT", "CAD", bdt_per_cad=rate)
        except CurrencyConversionError:
            total_cad = None
    bdt_per_piece = None
    cad_per_piece = None
    if total_bdt is not None and moq:
        bdt_per_piece = (Decimal(total_bdt) / Decimal(moq)).quantize(Decimal("0.01"))
    if total_cad is not None and moq:
        cad_per_piece = (Decimal(total_cad) / Decimal(moq)).quantize(Decimal("0.01"))
    return {
        "currency": currency,
        "entered_amount": amount,
        "exchange_rate": rate,
        "total_bdt": total_bdt,
        "total_cad": total_cad,
        "bdt_per_piece": bdt_per_piece,
        "cad_per_piece": cad_per_piece,
        "total_bdt_display": format_finance_money(total_bdt, "BDT") if total_bdt is not None else "",
        "total_cad_display": format_finance_money(total_cad, "CAD") if total_cad is not None else "",
        "bdt_per_piece_display": format_finance_money(bdt_per_piece, "BDT") if bdt_per_piece is not None else "",
        "cad_per_piece_display": format_finance_money(cad_per_piece, "CAD") if cad_per_piece is not None else "",
        "conversion_available": bool(total_cad is not None or currency != "BDT"),
    }

from .forms import (
    BDStaffMonthForm,
    EventForm,
    LeadForm,
    ShipmentForm,
    InventoryItemForm,
    ProductForm,
    FabricForm,
    AccessoryForm,
    TrimForm,
    ThreadForm,
    LibraryAttachmentForm,
)
from .forms_costing import ActualCostEntryForm
from .models import (
    AIAgent,
    ActualCostEntry,
    BDStaff,
    BDStaffMonth,
    CostSheetAudit,
    Customer,
    CustomerEvent,
    CustomerNote,
    EmployeeProfile,
    Lead,
    LeadActivity,
    LeadContactPoint,
    LeadAIInsight,
    LeadImportJob,
    LeadResearchJob,
    LeadTask,
    LEAD_QUAL_STATUS_CHOICES,
    LeadComment,
    CostingHeader,
    CostSheet,
    CostSheetSimple,
    QuickCosting,
    Invoice,
    InvoicePayment,
    Opportunity,
    OpportunityDocument,
    OpportunityFile,
    OpportunityTask,
    OrderLifecycle,
    Product,
    ProductReferenceImage,
    ProductTypeMaster,
    ProductCategoryMaster,
    FabricNameMaster,
    GSMRangeMaster,
    FabricGroupMaster,
    FabricTypeMaster,
    KnitStructureMaster,
    WeaveMaster,
    SurfaceMaster,
    HandfeelMaster,
    LibraryAttachment,
    ProductionOrder,
    ProductionProgressPhoto,
    ProductionStage,
    Shipment,
    AccountingEntry,
    CRMAuditLog,
    SystemActivityLog,
)
try:
    from .models import ProductionOrderLine
except Exception:
    ProductionOrderLine = None
from .production_forms import ProductionOrderForm, ProductionStageForm
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

logger = logging.getLogger(__name__)

def _get_openai_client():
    api_key = (getattr(settings, "OPENAI_API_KEY", "") or "").strip()
    if not api_key or OpenAI is None:
        return None
    return OpenAI(api_key=api_key)

client = _get_openai_client()

# One fixed stage order used everywhere
STAGE_FLOW_ORDER = [
    "development",
    "sampling",
    "cutting",
    "sewing",
    "ironing",
    "qc",
    "finishing",
    "packing",
    "shipping",
]

def _ordered_stages_qs(order_id):
    whens = [When(stage_key=key, then=idx) for idx, key in enumerate(STAGE_FLOW_ORDER)]
    return (
        ProductionStage.objects.filter(order_id=order_id)
        .annotate(
            _sort=Case(
                *whens,
                default=999,
                output_field=IntegerField(),
            )
        )
        .order_by("_sort", "id")
    )


def production_add(request):
    if request.method == "POST":
        form = ProductionOrderForm(request.POST, request.FILES)
        if form.is_valid():
            order = form.save()
            _apply_production_library_links(order, request)
            messages.success(request, "Production order created.")
            return redirect("production_detail", pk=order.pk)
    else:
        form = ProductionOrderForm()

    return render(
        request,
        "crm/production_add.html",
        {
            "form": form,
            "is_edit": False,
            "order": None,
        },
    )


def production_edit(request, pk):
    order = get_object_or_404(ProductionOrder, pk=pk)

    if request.method == "POST":
        form = ProductionOrderForm(request.POST, request.FILES, instance=order)
        if form.is_valid():
            form.save()
            messages.success(request, "Production order updated.")
            return redirect("production_detail", pk=pk)
    else:
        form = ProductionOrderForm(instance=order)

    return render(
        request,
        "crm/production_add.html",
        {
            "form": form,
            "is_edit": True,
            "order": order,
        },
    )


def production_detail(request, pk):
    order = get_object_or_404(
        ProductionOrder.objects.prefetch_related(
            "fabrics",
            "accessories",
            "trims",
            "threads",
        ),
        pk=pk,
    )

    # Correct stage order
    stages = _ordered_stages_qs(order.pk)

    # You already have this helper somewhere else in views.py
    size_grid, size_total = build_size_grid(order)

    attachments = order.attachments.all().order_by("-created_at")
    shipments = order.shipments.all().order_by("-ship_date", "-created_at")

    shipping_cost_bdt_total = Decimal("0")
    shipping_cost_cad_total = Decimal("0")
    for s in shipments:
        shipping_cost_bdt_total += s.cost_bdt or Decimal("0")
        shipping_cost_cad_total += s.cost_cad or Decimal("0")

    context = {
        "order": order,
        "stages": stages,
        "percent_done": order.percent_done,
        "order_delayed": order.is_delayed,
        "size_grid": size_grid,
        "size_total": size_total,
        "attachments": attachments,
        "shipments": shipments,
        "shipping_cost_bdt_total": shipping_cost_bdt_total,
        "shipping_cost_cad_total": shipping_cost_cad_total,
    }
    return render(request, "crm/production_detail.html", context)


@require_POST
def production_stage_click(request, stage_id):
    """
    Click stage to auto save dates
    planned -> in_progress sets actual_start
    in_progress -> done sets actual_end
    done -> no change
    """
    stage = get_object_or_404(ProductionStage, pk=stage_id)
    today = timezone.localdate()

    if stage.status == "planned":
        stage.status = "in_progress"
        if not stage.actual_start:
            stage.actual_start = today
        stage.save(update_fields=["status", "actual_start"])
        sync_operational_status(stage.order)
        messages.success(request, "Stage started and date saved.")

    elif stage.status == "in_progress":
        stage.status = "done"
        if not stage.actual_start:
            stage.actual_start = today
        if not stage.actual_end:
            stage.actual_end = today
        stage.save(update_fields=["status", "actual_start", "actual_end"])
        sync_operational_status(stage.order)
        messages.success(request, "Stage completed and date saved.")

    else:
        messages.info(request, "Stage is already done.")

    return redirect("production_detail", pk=stage.order_id)


def production_stage_edit(request, stage_id):
    stage = get_object_or_404(ProductionStage, pk=stage_id)
    today = timezone.localdate()

    if request.method == "POST":
        form = ProductionStageForm(request.POST, instance=stage)
        if form.is_valid():
            obj = form.save(commit=False)

            if obj.status in ["in_progress", "done"] and not obj.actual_start:
                obj.actual_start = today

            if obj.status == "done" and not obj.actual_end:
                obj.actual_end = today

            obj.save()
            messages.success(request, "Stage updated.")
            return redirect("production_detail", pk=obj.order_id)
    else:
        form = ProductionStageForm(instance=stage)

    return render(
        request,
        "crm/production_stage_edit.html",
        {
            "stage": stage,
            "form": form,
        },
    )
# ------------------------------------------
# Permissions helpers (non accounting)
# ------------------------------------------

def user_in_groups(user, group_names):
    if not user.is_authenticated:
        return False
    return user.groups.filter(name__in=group_names).exists() or user.is_superuser


def require_groups(group_names):
    return user_passes_test(lambda u: user_in_groups(u, group_names), login_url="login")


def is_canada_user(user):
    return user.is_authenticated and (user.is_staff or user.is_superuser)


canada_required = user_passes_test(is_canada_user, login_url="login")


def _can_archive_workflow_record(user):
    return can_manage_archives(user)


def _can_archive_customer(user):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    access = getattr(user, "access", None)
    return bool(access and getattr(access, "can_view_ceo_tools", False))


def _active_crm_user_options():
    User = get_user_model()
    return (
        User.objects.filter(is_active=True, employee_profile__is_archived=False)
        .order_by("first_name", "last_name", "username")
    )


def _user_display_name(user):
    if not user:
        return ""
    full_name = user.get_full_name()
    return full_name or user.get_username()


def _archive_workflow_record(record, user):
    record.is_archived = True
    record.archived_at = timezone.now()
    record.archived_by = user if user and user.is_authenticated else None
    record.save(update_fields=["is_archived", "archived_at", "archived_by"])


def _workflow_object_label(record):
    for attr in ("purchase_order_number", "lead_id", "opportunity_id", "order_code", "invoice_number", "customer_code"):
        value = getattr(record, attr, None)
        if value:
            return str(value)
    return str(getattr(record, "pk", "") or record)


def _log_workflow_safety_action(request, *, action, record, message, meta=None):
    try:
        SystemActivityLog.objects.create(
            actor=request.user if request and request.user.is_authenticated else None,
            area="workflow",
            action=action,
            level="info",
            path=request.get_full_path()[:255] if request else "",
            method=request.method if request else "",
            model_label=record.__class__.__name__,
            object_id=str(getattr(record, "pk", "") or ""),
            message=message[:255],
            meta_json=json.dumps(meta or {}, default=str),
        )
    except Exception:
        logger.exception("Failed to write workflow safety log for %s", record)


def _log_lead_workflow_note(lead, user, description):
    if not lead:
        return
    try:
        LeadActivity.objects.create(
            lead=lead,
            activity_type="note_added",
            description=description,
            user=user if user and user.is_authenticated else None,
        )
    except Exception:
        logger.exception("Failed to write lead workflow activity for lead %s", getattr(lead, "pk", None))


def _lead_linked_record_labels(lead):
    labels = []
    if lead.opportunities.exists():
        labels.append("opportunities")
    if ProductionOrder.objects.filter(Q(lead=lead) | Q(opportunity__lead=lead)).exists():
        labels.append("production orders")
    if Invoice.objects.filter(
        Q(order__lead=lead)
        | Q(order__opportunity__lead=lead)
        | Q(costing_header__opportunity__lead=lead)
        | Q(quick_costing__opportunity__lead=lead)
    ).exists():
        labels.append("invoices")
    if LeadActivity.objects.filter(lead=lead).exists():
        labels.append("activity history")
    if LeadTask.objects.filter(lead=lead).exists():
        labels.append("tasks")
    if LeadComment.objects.filter(lead=lead).exists():
        labels.append("comments")
    if Event.objects.filter(lead=lead).exists():
        labels.append("calendar events")
    if ProductReferenceImage.objects.filter(lead=lead).exists():
        labels.append("reference images")
    return labels


def _hydrate_calendar_event_links(event):
    production = getattr(event, "production", None)
    opportunity = getattr(event, "opportunity", None)
    lead = getattr(event, "lead", None)

    if production:
        if not event.opportunity_id and getattr(production, "opportunity_id", None):
            event.opportunity = production.opportunity
            opportunity = event.opportunity
        if not event.lead_id and getattr(production, "lead_id", None):
            event.lead = production.lead
            lead = event.lead
        if not event.customer_id and getattr(production, "customer_id", None):
            event.customer = production.customer

    if opportunity:
        if not event.lead_id and getattr(opportunity, "lead_id", None):
            event.lead = opportunity.lead
            lead = event.lead
        if not event.customer_id and getattr(opportunity, "customer_id", None):
            event.customer = opportunity.customer

    if lead and not event.customer_id and getattr(lead, "customer_id", None):
        event.customer = lead.customer


def _calendar_related_opportunities(event):
    seen = set()
    related = []

    def add(opp):
        if opp and opp.pk not in seen:
            seen.add(opp.pk)
            related.append(opp)

    add(getattr(event, "opportunity", None))
    production = getattr(event, "production", None)
    if production:
        add(getattr(production, "opportunity", None))
    lead = getattr(event, "lead", None)
    if lead:
        for opp in lead.opportunities.select_related("customer").order_by("-updated_at", "-id")[:5]:
            add(opp)
    return related


def _calendar_related_productions(event):
    seen = set()
    related = []

    def add(order):
        if order and order.pk not in seen:
            seen.add(order.pk)
            related.append(order)

    add(getattr(event, "production", None))
    opportunity = getattr(event, "opportunity", None)
    if opportunity:
        for order in ProductionOrder.objects.filter(opportunity=opportunity).select_related("customer", "opportunity").order_by("-created_at", "-id")[:5]:
            add(order)
    lead = getattr(event, "lead", None)
    if lead:
        for order in ProductionOrder.objects.filter(Q(lead=lead) | Q(opportunity__lead=lead)).select_related("customer", "opportunity").order_by("-created_at", "-id")[:5]:
            add(order)
    return related


def _calendar_link_payload():
    payload = []

    def add(
        record_type,
        record,
        label,
        *,
        lead_id=None,
        opportunity_id=None,
        customer_id=None,
        production_id=None,
        search_extra="",
    ):
        payload.append(
            {
                "type": record_type,
                "id": record.pk,
                "label": label,
                "search": f"{record_type} {label} {search_extra}".lower(),
                "lead_id": lead_id or "",
                "opportunity_id": opportunity_id or "",
                "customer_id": customer_id or "",
                "production_id": production_id or "",
            }
        )

    for lead in Lead.objects.select_related("customer").order_by("-id")[:400]:
        add(
            "lead",
            lead,
            f"{lead.lead_id} - {lead.account_brand or lead.contact_name or 'Lead'}",
            lead_id=lead.pk,
            customer_id=lead.customer_id,
        )
    for opp in Opportunity.objects.select_related("lead", "customer").order_by("-id")[:400]:
        add(
            "opportunity",
            opp,
            f"{opp.opportunity_id} - {getattr(opp.lead, 'account_brand', '') or getattr(opp.customer, 'account_brand', '') or 'Opportunity'}",
            lead_id=opp.lead_id,
            opportunity_id=opp.pk,
            customer_id=opp.customer_id,
        )
    for customer in Customer.objects.order_by("-id")[:400]:
        add(
            "customer",
            customer,
            f"{customer.account_brand or customer.contact_name or 'Customer'} [{customer.customer_code}]",
            customer_id=customer.pk,
        )
    for order in ProductionOrder.objects.select_related("lead", "opportunity", "customer").order_by("-created_at", "-id")[:400]:
        add(
            "production",
            order,
            f"{order.purchase_order_number} - {order.title}",
            lead_id=order.lead_id,
            opportunity_id=order.opportunity_id,
            customer_id=order.customer_id,
            production_id=order.pk,
            search_extra=order.internal_order_id,
        )
    return payload


def _opportunity_linked_record_labels(opportunity):
    labels = []
    if ProductionOrder.objects.filter(opportunity=opportunity).exists():
        labels.append("production orders")
    if Invoice.objects.filter(
        Q(order__opportunity=opportunity)
        | Q(costing_header__opportunity=opportunity)
        | Q(quick_costing__opportunity=opportunity)
    ).exists():
        labels.append("invoices")
    if CostingHeader.objects.filter(opportunity=opportunity).exists() or CostSheet.objects.filter(opportunity=opportunity).exists() or QuickCosting.objects.filter(opportunity=opportunity).exists():
        labels.append("costings")
    if Shipment.objects.filter(opportunity=opportunity).exists():
        labels.append("shipments")
    if OpportunityTask.objects.filter(opportunity=opportunity).exists():
        labels.append("tasks")
    if OpportunityFile.objects.filter(opportunity=opportunity).exists() or OpportunityDocument.objects.filter(opportunity=opportunity).exists():
        labels.append("files")
    if LeadComment.objects.filter(opportunity=opportunity).exists():
        labels.append("comments")
    if Event.objects.filter(opportunity=opportunity).exists():
        labels.append("calendar events")
    return labels


def _customer_linked_record_labels(customer):
    flags = (
        Customer.objects
        .filter(pk=customer.pk)
        .annotate(
            has_leads=Exists(Lead.objects.filter(customer=OuterRef("pk"))),
            has_opportunities=Exists(
                Opportunity.objects.filter(Q(customer=OuterRef("pk")) | Q(lead__customer=OuterRef("pk")))
            ),
            has_production_orders=Exists(ProductionOrder.objects.filter(customer=OuterRef("pk"))),
            has_invoices=Exists(Invoice.objects.filter(Q(customer=OuterRef("pk")) | Q(order__customer=OuterRef("pk")))),
            has_payments=Exists(InvoicePayment.objects.filter(invoice__customer=OuterRef("pk"))),
            has_shipments=Exists(Shipment.objects.filter(customer=OuterRef("pk"))),
            has_accounting_records=Exists(AccountingEntry.objects.filter(customer=OuterRef("pk"))),
            has_order_lifecycles=Exists(OrderLifecycle.objects.filter(customer=OuterRef("pk"))),
        )
        .values(
            "has_leads",
            "has_opportunities",
            "has_production_orders",
            "has_invoices",
            "has_payments",
            "has_shipments",
            "has_accounting_records",
            "has_order_lifecycles",
        )
        .first()
    ) or {}

    labels = []
    if flags.get("has_leads"):
        labels.append("leads")
    if flags.get("has_opportunities"):
        labels.append("opportunities")
    if flags.get("has_production_orders"):
        labels.append("production orders")
    if flags.get("has_invoices"):
        labels.append("invoices")
    if flags.get("has_payments"):
        labels.append("payments")
    if flags.get("has_shipments"):
        labels.append("shipments")
    if flags.get("has_accounting_records"):
        labels.append("accounting records")
    if flags.get("has_order_lifecycles"):
        labels.append("order lifecycles")
    return labels


def _production_linked_record_labels(order):
    labels = []
    if Shipment.objects.filter(order=order).exists():
        labels.append("shipments")
    if ProductionOrderMaterial.objects.filter(order=order).exists():
        labels.append("inventory allocations")
    if Invoice.objects.filter(order=order).exists():
        labels.append("invoices")
    if AccountingEntry.objects.filter(production_order=order).exists():
        labels.append("accounting records")
    return labels

# ------------------------------------------
# Shipment email helper (non accounting)
# ------------------------------------------

def send_shipment_update_email(shipment, event_label):
    status_key = getattr(shipment, "status", "") or "shipped"
    try:
        from .tasks import send_shipment_notification_async as shipment_notification_task

        shipment_notification_task.apply_async(
            args=[shipment.pk, status_key],
            kwargs={"force": True},
            retry=False,
        )
    except Exception:
        logger.exception("Shipment notification compatibility queue failed", extra={"shipment_id": shipment.pk})

# ------------------------------------------
# Production stage order (non accounting)
# ------------------------------------------

STAGE_ORDER = {
    "development": 1,
    "sampling": 2,
    "cutting": 3,
    "sewing": 4,
    "ironing": 5,
    "qc": 6,
    "finishing": 7,
    "packing": 8,
    "shipping": 9,
}


# ===================================================
# LEADS AI OVERVIEW AND DETAIL
# ===================================================


def leads_ai_overview(request):
    """
    AI overview for many leads.
    Used on leads list page.
    """
    mode = request.POST.get("mode", "overview")
    user_text = request.POST.get("user_text", "").strip()

    leads = Lead.objects.order_by("-created_date", "-id")[:50]

    lines = []
    for ld in leads:
        lines.append(
            f"- {ld.lead_id} | {ld.account_brand} | {ld.lead_status} | "
            f"priority {ld.priority} | market {ld.market} | product {ld.product_interest} "
            f"| qty {ld.order_quantity}"
        )

    base_info = "\n".join(lines) if lines else "No leads in the system."

    if mode == "today_focus":
        task = "Pick which five leads we must act on today and explain why."
    elif mode == "risk_view":
        task = "Find which leads look cold or at risk and suggest rescue steps."
    elif mode == "hot_view":
        task = "Find which leads are close to closing and suggest clear next steps."
    else:
        task = "Give a short overview of this lead pipeline and what to do next."

    if user_text:
        task += f"\nUser extra question: {user_text}"

    prompt = f"""
You are the Iconic CRM AI brain. You see a list of leads for a clothing factory.

Each line looks like:
lead id | brand | status | priority | market | product interest | quantity

Leads:

{base_info}

Task for you:
{task}

Answer in short clear bullet points that a sales person can use right now.
"""

    try:
        resp = client.responses.create(
            model="gpt-4.1-mini",
            input=prompt,
            max_output_tokens=350,
        )
        answer = resp.output[0].content[0].text
        return JsonResponse({"ok": True, "text": answer})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)})



def lead_ai_detail(request, pk):
    """
    AI brain for one lead.
    Used on lead detail page.
    """
    lead = get_object_or_404(Lead, pk=pk)
    mode = request.POST.get("mode", "summary")
    user_text = request.POST.get("user_text", "").strip()

    info = f"""
Lead ID: {lead.lead_id}
Brand: {lead.account_brand}
Contact: {lead.contact_name}
Email: {lead.email}
Phone: {lead.phone}
Market: {lead.market}
Country: {lead.country}
City: {lead.city}
Source: {lead.source}
Lead type: {lead.lead_type}
Status: {lead.lead_status}
Priority: {lead.priority}
Product interest: {lead.product_interest}
Order quantity: {lead.order_quantity}
Budget: {lead.budget}
Preferred contact time: {lead.preferred_contact_time}
Notes: {lead.notes}
"""

    if mode == "summary":
        task = "Summarize this lead in short points including risk and chance."
    elif mode == "next_step":
        task = "Give one clear next step with a reason."
    elif mode == "risk":
        task = "Rate cold risk from 1 to 10 with two line explanation."
    elif mode == "potential":
        task = "Rate this lead value potential from 1 to 10 with short reason."
    elif mode == "mood":
        task = "Guess the lead intent and suggest reply tone."
    elif mode == "product":
        task = "Suggest two or three fitting product ideas with fabric notes."
    elif mode == "email":
        task = (
            "Write a short warm follow up email for this lead. "
            "Do not invent wrong data. Use generic wording for missing info."
        )
    elif mode == "timeline":
        task = "Explain where this lead is in the journey and what comes next."
    elif mode == "chat":
        if not user_text:
            return JsonResponse({"ok": False, "error": "No question given."})
        task = f"Answer this question about the lead in a short way: {user_text}"
    else:
        task = "Give a short helpful summary and next step."

    prompt = f"""
You are the Iconic CRM AI brain.
You see one lead for a clothing production company.

Lead data:
{info}

Task:
{task}

Write in simple clear English.
Keep the answer short.
"""

    try:
        resp = client.responses.create(
            model="gpt-4.1-mini",
            input=prompt,
            max_output_tokens=350,
        )
        answer = resp.output[0].content[0].text

        LeadActivity.objects.create(
            lead=lead,
            activity_type="ai_summary",
            description=f"AI mode {mode}: {answer[:400]}",
        )

        return JsonResponse({"ok": True, "text": answer})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)})


# ===================================================
# LEAD AND OPPORTUNITY LISTS AND BASIC CRUD
# ===================================================
# crm/views.py (your leads list view)
import re
from decimal import Decimal
from urllib.parse import urlparse

from django.core.paginator import Paginator
from django.db.models import Q, Count
from django.shortcuts import render
from django.utils.dateparse import parse_date
from django.contrib.auth import get_user_model

from .models import Lead, LEAD_STATUS_CHOICES, OUTBOUND_STATUS_CHOICES

def _parse_money_value(raw_value):
    if raw_value is None:
        return None
    s = str(raw_value).strip()
    if not s:
        return None
    s = s.replace(",", "")
    match = re.search(r"-?\d+(\.\d+)?", s)
    if not match:
        return None
    try:
        return Decimal(match.group())
    except Exception:
        return None

LEAD_LIST_STATUS_CHOICES = [
    ("active", "Active"),
    ("new", "New"),
    ("contacted", "Contacted"),
    ("follow_up", "Follow Up"),
    ("converted", "Converted"),
    ("closed", "Closed / Not Viable"),
    ("archived", "Archived"),
    ("all", "All"),
]

_LEAD_FOLLOW_UP_OUTBOUND_STATUSES = {
    "Follow Up 1 Sent",
    "Follow Up 2 Sent",
    "Follow Up 3 Sent",
}
_LEAD_CONTACTED_OUTBOUND_STATUSES = {
    "First Contact Sent",
    "Follow Up 1 Sent",
    "Follow Up 2 Sent",
    "Follow Up 3 Sent",
    "Replied",
    "Interested",
    "Meeting Booked",
    "Quote Requested",
    "Sample Discussion",
    "No Response",
}
_LEAD_LOST_OUTBOUND_STATUSES = {"Bad Fit", "Archived"}
_LEAD_CONVERTED_OUTBOUND_STATUSES = {"Converted to Opportunity"}

def _normalize_lead_list_status_filter(value):
    raw = (value or "").strip()
    key = raw.lower().replace("-", "_").replace(" ", "_")
    return {
        "new": "new",
        "contacted": "contacted",
        "working": "contacted",
        "qualified": "contacted",
        "follow_up": "follow_up",
        "followup": "follow_up",
        "nurturing": "follow_up",
        "on_hold": "follow_up",
        "converted": "converted",
        "lost": "lost",
        "closed": "closed",
        "not_viable": "closed",
        "unqualified": "lost",
        "archived": "archived",
        "archive": "archived",
        "active": "active",
        "all": "all",
    }.get(key, "")

def _lead_opportunity_count(lead):
    count = getattr(lead, "opportunity_count", None)
    if count is not None:
        return count
    try:
        return lead.opportunities.count()
    except Exception:
        return 0

def _lead_has_contact_activity(lead):
    status = getattr(lead, "lead_status", "") or ""
    outbound_status = getattr(lead, "outbound_status", "") or ""
    return any(
        [
            getattr(lead, "last_outreach_date", None),
            getattr(lead, "last_reply_date", None),
            outbound_status in _LEAD_CONTACTED_OUTBOUND_STATUSES,
            status in {"Working", "Qualified"},
        ]
    )

def _lead_follow_up_needed(lead, today):
    status = getattr(lead, "lead_status", "") or ""
    outbound_status = getattr(lead, "outbound_status", "") or ""
    next_follow_up = getattr(lead, "next_follow_up_date", None)
    next_followup = getattr(lead, "next_followup", None)
    return any(
        [
            next_follow_up and next_follow_up <= today,
            next_followup and next_followup <= today,
            outbound_status in _LEAD_FOLLOW_UP_OUTBOUND_STATUSES,
            status in {"Nurturing", "On Hold"},
        ]
    )

def _lead_list_status_payload(lead, today=None):
    today = today or timezone.localdate()
    status = getattr(lead, "lead_status", "") or ""
    outbound_status = getattr(lead, "outbound_status", "") or ""

    if (
        status == "Converted"
        or outbound_status in _LEAD_CONVERTED_OUTBOUND_STATUSES
        or _lead_opportunity_count(lead) > 0
    ):
        return "converted", "Converted"

    if status in {"Lost", "Unqualified"} or outbound_status in _LEAD_LOST_OUTBOUND_STATUSES:
        return "lost", "Lost"

    if _lead_follow_up_needed(lead, today):
        return "follow_up", "Follow Up"

    if _lead_has_contact_activity(lead):
        return "contacted", "Contacted"

    if getattr(lead, "assigned_to_id", None):
        return "follow_up", "Follow Up"

    return "new", "New"

def _lead_assigned_to_label(lead):
    return resolve_lead_owner(lead)["canonical_name"]

def _decorate_leads_for_list(leads, today=None):
    today = today or timezone.localdate()
    for lead in leads:
        key, label = _lead_list_status_payload(lead, today=today)
        lead.display_lead_status_key = key
        lead.display_lead_status = label
        lead.assigned_to_display = _lead_assigned_to_label(lead)
    return leads

def _lead_list_converted_q():
    return (
        Q(lead_status="Converted")
        | Q(outbound_status__in=_LEAD_CONVERTED_OUTBOUND_STATUSES)
        | Q(opportunity_count__gt=0)
    )

def _lead_list_lost_q():
    return (
        Q(lead_status__in=["Lost", "Unqualified"])
        | Q(outbound_status__in=_LEAD_LOST_OUTBOUND_STATUSES)
    )

def _lead_list_follow_up_q(today):
    assigned_untouched_q = (
        Q(assigned_to__isnull=False)
        & Q(last_outreach_date__isnull=True)
        & Q(last_reply_date__isnull=True)
        & Q(next_follow_up_date__isnull=True)
        & Q(next_followup__isnull=True)
        & (Q(outbound_status="") | Q(outbound_status="Not Contacted"))
        & Q(lead_status__in=["", "New"])
    )
    return (
        Q(next_follow_up_date__lte=today)
        | Q(next_followup__lte=today)
        | Q(outbound_status__in=_LEAD_FOLLOW_UP_OUTBOUND_STATUSES)
        | Q(lead_status__in=["Nurturing", "On Hold"])
        | assigned_untouched_q
    )

def _lead_list_contacted_q():
    return (
        Q(last_outreach_date__isnull=False)
        | Q(last_reply_date__isnull=False)
        | Q(outbound_status__in=_LEAD_CONTACTED_OUTBOUND_STATUSES)
        | Q(lead_status__in=["Working", "Qualified"])
    )

def _filter_leads_by_list_status(qs, status_key, today):
    converted_q = _lead_list_converted_q()
    lost_q = _lead_list_lost_q()
    follow_up_q = _lead_list_follow_up_q(today)
    contacted_q = _lead_list_contacted_q()

    if status_key == "all":
        return qs
    if status_key == "archived":
        return qs.filter(Q(is_archived=True) | Q(lead_type="outbound", outbound_status="Archived")).distinct()
    if status_key == "active":
        return qs.filter(is_archived=False).exclude(converted_q | lost_q).distinct()
    if status_key == "converted":
        return qs.filter(converted_q).distinct()
    if status_key in {"closed", "lost"}:
        return qs.filter(lost_q).exclude(converted_q).distinct()
    if status_key == "follow_up":
        return qs.filter(follow_up_q).exclude(converted_q | lost_q).distinct()
    if status_key == "contacted":
        return qs.filter(contacted_q).exclude(converted_q | lost_q | follow_up_q).distinct()
    if status_key == "new":
        return qs.filter(
            assigned_to__isnull=True,
            last_outreach_date__isnull=True,
            last_reply_date__isnull=True,
            next_follow_up_date__isnull=True,
            next_followup__isnull=True,
            opportunity_count=0,
        ).filter(
            Q(lead_status="") | Q(lead_status="New"),
            Q(outbound_status="") | Q(outbound_status="Not Contacted"),
        ).exclude(lost_q | converted_q).distinct()
    return qs

def _normalize_phone(value):
    if not value:
        return ""
    digits = re.sub(r"\D", "", str(value))
    return digits

def _normalize_handle(value):
    if not value:
        return ""
    val = str(value).strip().lower()
    if val.startswith("@"):
        val = val[1:]
    return val

def _normalize_domain(value):
    if not value:
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"http://{raw}"
    try:
        parsed = urlparse(raw)
        host = parsed.netloc or ""
    except Exception:
        host = ""
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    return host

def _possible_duplicate_leads(lead, exclude_id=None):
    q = Q()
    email = (lead.email or "").strip()
    phone = _normalize_phone(lead.phone)
    brand = (lead.account_brand or "").strip()
    website = _normalize_domain(getattr(lead, "website", "") or getattr(lead, "company_website", ""))
    instagram = _normalize_handle(getattr(lead, "instagram_handle", ""))
    linkedin = (getattr(lead, "linkedin_url", "") or "").strip().lower()

    if email:
        q |= Q(email__iexact=email)
    if phone:
        q |= Q(phone__icontains=phone)
    if brand:
        q |= Q(account_brand__iexact=brand)
    if website:
        q |= Q(website__icontains=website) | Q(company_website__icontains=website)
    if instagram:
        q |= Q(instagram_handle__iexact=instagram) | Q(instagram_handle__iexact=f"@{instagram}")
    if linkedin:
        q |= Q(linkedin_url__icontains=linkedin)

    if not q:
        return Lead.objects.none()

    qs = Lead.objects.filter(q)
    if exclude_id:
        qs = qs.exclude(id=exclude_id)
    return qs

def _merge_leads(primary, duplicate, user=None):
    if primary.pk == duplicate.pk:
        return

    merge_fields = [
        "account_brand",
        "contact_name",
        "email",
        "phone",
        "attachment",
        "market",
        "website",
        "company_website",
        "country",
        "region",
        "city",
        "product_category",
        "product_interest",
        "order_quantity",
        "budget",
        "preferred_contact_time",
        "source",
        "source_channel",
        "outbound_method",
        "outbound_status",
        "lead_status",
        "priority",
        "priority_level",
        "brand_stage",
        "target_order_volume_min",
        "target_order_volume_max",
        "brand_fit_score",
        "instagram_handle",
        "linkedin_url",
        "last_outreach_date",
        "next_follow_up_date",
        "last_reply_date",
        "ideal_customer_profile_match",
        "disqualification_reason",
        "owner",
        "assigned_to",
        "notes",
    ]

    updated = False
    for field in merge_fields:
        primary_val = getattr(primary, field, None)
        duplicate_val = getattr(duplicate, field, None)
        if (primary_val is None or primary_val == "" or primary_val == 0) and duplicate_val:
            setattr(primary, field, duplicate_val)
            updated = True

    if updated:
        primary.save()

    # move related records
    from crm.models import LeadComment, LeadTask, LeadActivity, LeadAIMessage, Event, Opportunity
    from aihub.models import AIConversation

    LeadComment.objects.filter(lead=duplicate).update(lead=primary)
    LeadTask.objects.filter(lead=duplicate).update(lead=primary)
    LeadActivity.objects.filter(lead=duplicate).update(lead=primary)
    LeadAIMessage.objects.filter(lead=duplicate).update(lead=primary)
    Event.objects.filter(lead=duplicate).update(lead=primary)
    Opportunity.objects.filter(lead=duplicate).update(lead=primary)
    AIConversation.objects.filter(lead=duplicate).update(lead=primary)

    LeadActivity.objects.create(
        lead=primary,
        activity_type="note_added",
        description=f"Merged lead {duplicate.lead_id} into this lead.",
    )
    LeadActivity.objects.create(
        lead=duplicate,
        activity_type="note_added",
        description=f"Merged into lead {primary.lead_id}.",
    )

    if duplicate.lead_type == "outbound":
        duplicate.outbound_status = "Archived"
    if not duplicate.disqualification_reason:
        duplicate.disqualification_reason = f"Merged into lead {primary.lead_id}."
    duplicate.save(update_fields=["outbound_status", "disqualification_reason"])

def leads_list(request):
    can_manage_leads = can_manage_all_sales_records(request.user)
    can_archive_records = _can_archive_workflow_record(request.user)
    lead_id = (request.GET.get("lead_id") or "").strip()
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("lead_status") or request.GET.get("status") or "").strip()
    selected_lead_status = _normalize_lead_list_status_filter(status)
    market = (request.GET.get("market") or "").strip()
    owner = (request.GET.get("owner") or "").strip()
    assigned_to = (request.GET.get("assigned_to") or "").strip()
    assigned_to_key = assigned_to.lower()
    assigned_to_unassigned = assigned_to_key == "unassigned"
    assigned_to_id = None
    if assigned_to and not assigned_to_unassigned:
        try:
            assigned_to_id = int(assigned_to)
        except ValueError:
            assigned_to_id = None
    created_from_raw = (request.GET.get("created_from") or "").strip()
    created_to_raw = (request.GET.get("created_to") or "").strip()
    value_min_raw = (request.GET.get("value_min") or "").strip()
    value_max_raw = (request.GET.get("value_max") or "").strip()
    qual_status = (request.GET.get("qual_status") or "").strip()
    icp_match = (request.GET.get("icp_match") or "").strip().lower()
    fit_min_raw = (request.GET.get("fit_min") or "").strip()
    has_website = (request.GET.get("has_website") or "").strip().lower()
    has_email = (request.GET.get("has_email") or "").strip().lower()
    has_phone = (request.GET.get("has_phone") or "").strip().lower()
    has_social = (request.GET.get("has_social") or "").strip().lower()
    outreach_ready = (request.GET.get("outreach_ready") or "").strip().lower()
    requested_archive = (request.GET.get("archive") or "").strip().lower()
    default_view = "all" if can_manage_leads and requested_archive in {"archived", "all"} else "available"
    requested_view = (request.GET.get("view") or default_view).strip().lower()
    allowed_views = {"available", "my"}
    if can_manage_leads:
        allowed_views.add("all")
    view = requested_view if requested_view in allowed_views else "available"
    if (
        can_manage_leads
        and view == "available"
        and selected_lead_status in {"converted", "closed", "archived", "all"}
    ):
        view = "all"
    archive_filter = (request.GET.get("archive") or ("archived" if view == "archived" else "active")).strip().lower()
    if view in {"available", "my"}:
        archive_filter = "active"
    if selected_lead_status == "archived" and can_manage_leads:
        archive_filter = "archived"
    elif selected_lead_status == "all" and can_manage_leads and "archive" not in request.GET:
        archive_filter = "all"

    sort = (request.GET.get("sort") or "new").strip().lower()

    try:
        per_page = int(request.GET.get("per_page") or 50)
    except ValueError:
        per_page = 50

    if per_page not in (20, 50, 100):
        per_page = 50

    qs = scope_sales_lead_queue(Lead.objects.select_related("assigned_to"), request.user)

    if view == "available":
        qs = qs.filter(available_sales_lead_q())
    elif view == "my":
        qs = qs.filter(active_sales_lead_q(), assigned_to=request.user)
    elif view == "inbound":
        qs = qs.filter(lead_type="inbound")
    elif view == "outbound":
        qs = qs.filter(lead_type="outbound")
    elif view == "followup":
        today = timezone.localdate()
        qs = qs.filter(
            lead_type="outbound",
        ).filter(
            Q(next_follow_up_date__lte=today) | Q(next_followup__lte=today)
        ).filter(Q(last_reply_date__isnull=True))
        qs = qs.exclude(outbound_status__in=["Archived", "Bad Fit"])
    elif view == "replied":
        qs = qs.filter(lead_type="outbound", outbound_status="Replied")
    elif view == "meeting":
        qs = qs.filter(lead_type="outbound", outbound_status="Meeting Booked")
    elif view == "high_fit":
        qs = qs.filter(Q(brand_fit_score__gte=70) | Q(ideal_customer_profile_match=True))
    elif view == "target_volume":
        qs = qs.filter(
            Q(target_order_volume_min__gte=1000, target_order_volume_min__lte=5000)
            | Q(target_order_volume_max__gte=1000, target_order_volume_max__lte=5000)
            | Q(target_order_volume_min__lte=1000, target_order_volume_max__gte=5000)
        )
    elif view == "no_response":
        qs = qs.filter(lead_type="outbound", outbound_status="No Response")
    elif view == "archived":
        qs = qs.filter(Q(is_archived=True) | Q(lead_type="outbound", outbound_status="Archived"))

    if archive_filter == "archived" and view != "archived":
        qs = qs.filter(is_archived=True)
    elif archive_filter == "active":
        qs = qs.filter(is_archived=False)

    if lead_id:
        qs = qs.filter(lead_id__icontains=lead_id)

    if q:
        qs = qs.filter(
            Q(account_brand__icontains=q)
            | Q(contact_name__icontains=q)
            | Q(email__icontains=q)
            | Q(phone__icontains=q)
            | Q(notes__icontains=q)
            | Q(company_website__icontains=q)
            | Q(website__icontains=q)
            | Q(instagram_handle__icontains=q)
            | Q(linkedin_url__icontains=q)
            | Q(primary_product_type__icontains=q)
            | Q(product_interest__icontains=q)
            | Q(product_category__icontains=q)
            | Q(order_quantity__icontains=q)
            | Q(lead_id__icontains=q)
            | Q(source_channel__icontains=q)
            | Q(outbound_status__icontains=q)
        )

    if market:
        qs = qs.filter(market__iexact=market)

    if owner:
        matching_profile_ids = employee_profile_ids_matching(owner)
        owner_filter = Q(owner__icontains=owner)
        for profile_id in matching_profile_ids:
            profile_payload = get_employee_identity_index()["by_profile_id"].get(profile_id)
            if profile_payload:
                owner_filter |= employee_lead_ownership_q(profile_payload["user_id"])
        qs = qs.filter(owner_filter)

    created_from = parse_date(created_from_raw) if created_from_raw else None
    created_to = parse_date(created_to_raw) if created_to_raw else None
    if created_from:
        qs = qs.filter(created_date__gte=created_from)
    if created_to:
        qs = qs.filter(created_date__lte=created_to)

    if qual_status:
        qs = qs.filter(qualification_status=qual_status)

    if icp_match == "yes":
        qs = qs.filter(ideal_customer_profile_match=True)
    elif icp_match == "no":
        qs = qs.filter(ideal_customer_profile_match=False)

    if fit_min_raw:
        try:
            fit_min = int(fit_min_raw)
            qs = qs.filter(brand_fit_score__gte=fit_min)
        except ValueError:
            pass

    if outreach_ready == "1":
        qs = qs.filter(qualification_status__in=["Outreach Ready", "Strong Fit"])

    contact_join = False
    if has_website == "yes":
        qs = qs.filter(Q(website__gt="") | Q(company_website__gt=""))
    elif has_website == "no":
        qs = qs.exclude(Q(website__gt="") | Q(company_website__gt=""))

    if has_email == "yes":
        qs = qs.filter(Q(email__gt="") | Q(contact_points__contact_type="email"))
        contact_join = True
    elif has_email == "no":
        qs = qs.exclude(Q(email__gt="") | Q(contact_points__contact_type="email"))
        contact_join = True

    if has_phone == "yes":
        qs = qs.filter(Q(phone__gt="") | Q(contact_points__contact_type="phone"))
        contact_join = True
    elif has_phone == "no":
        qs = qs.exclude(Q(phone__gt="") | Q(contact_points__contact_type="phone"))
        contact_join = True

    if has_social == "yes":
        qs = qs.filter(
            Q(instagram_handle__gt="")
            | Q(linkedin_url__gt="")
            | Q(contact_points__contact_type="instagram")
            | Q(contact_points__contact_type="linkedin")
        )
        contact_join = True
    elif has_social == "no":
        qs = qs.exclude(
            Q(instagram_handle__gt="")
            | Q(linkedin_url__gt="")
            | Q(contact_points__contact_type="instagram")
            | Q(contact_points__contact_type="linkedin")
        )
        contact_join = True

    if contact_join:
        qs = qs.distinct()

    today = timezone.localdate()
    qs = qs.annotate(opportunity_count=Count("opportunities", distinct=True))
    if can_archive_records:
        qs = qs.annotate(
            list_has_production_orders=Exists(
                ProductionOrder.objects.filter(
                    Q(lead_id=OuterRef("pk")) | Q(opportunity__lead_id=OuterRef("pk"))
                )
            ),
            list_has_invoices=Exists(
                Invoice.objects.filter(
                    Q(order__lead_id=OuterRef("pk"))
                    | Q(order__opportunity__lead_id=OuterRef("pk"))
                    | Q(costing_header__opportunity__lead_id=OuterRef("pk"))
                    | Q(quick_costing__opportunity__lead_id=OuterRef("pk"))
                )
            ),
            list_has_activities=Exists(LeadActivity.objects.filter(lead_id=OuterRef("pk"))),
            list_has_tasks=Exists(LeadTask.objects.filter(lead_id=OuterRef("pk"))),
            list_has_comments=Exists(LeadComment.objects.filter(lead_id=OuterRef("pk"))),
            list_has_events=Exists(Event.objects.filter(lead_id=OuterRef("pk"))),
            list_has_reference_images=Exists(ProductReferenceImage.objects.filter(lead_id=OuterRef("pk"))),
        )
    if selected_lead_status:
        qs = _filter_leads_by_list_status(qs, selected_lead_status, today)
    elif archive_filter == "active":
        qs = _filter_leads_by_list_status(qs, "active", today)

    if sort == "old":
        qs = qs.order_by("created_date", "id")
    else:
        qs = qs.order_by("-created_date", "-id")

    value_min = _parse_money_value(value_min_raw) if value_min_raw else None
    value_max = _parse_money_value(value_max_raw) if value_max_raw else None
    if value_min is not None or value_max is not None:
        filtered = []
        for lead in qs:
            budget_value = _parse_money_value(getattr(lead, "budget", None))
            if budget_value is None:
                continue
            if value_min is not None and budget_value < value_min:
                continue
            if value_max is not None and budget_value > value_max:
                continue
            filtered.append(lead)
        qs = filtered

    if can_manage_leads:
        users = list(
            get_user_model().objects.select_related("employee_profile").filter(
                is_active=True,
                employee_profile__is_archived=False,
            ).order_by("first_name", "last_name", "username")
        )
    else:
        users = [request.user]
    identity_index = get_employee_identity_index()
    user_by_id = {user.pk: user for user in users}

    def build_assignee_url(value):
        params = request.GET.copy()
        params.pop("page", None)
        if value:
            params["assigned_to"] = str(value)
        else:
            params.pop("assigned_to", None)
        query = params.urlencode()
        return f"{request.path}?{query}" if query else request.path

    def display_user(user):
        return resolve_employee_identity(user_id=user.pk, index=identity_index)["canonical_name"]

    for user in users:
        user.canonical_name = display_user(user)

    def count_assignees(source):
        if isinstance(source, list):
            total = len(source)
            counts = defaultdict(int)
            unassigned = 0
            for lead in source:
                identity = resolve_lead_owner(lead, index=identity_index)
                if identity["user_id"]:
                    counts[identity["user_id"]] += 1
                else:
                    unassigned += 1
            return total, dict(counts), unassigned

        total = source.count()
        counts = {}
        unassigned = 0
        for row in source.order_by().values("assigned_to_id", "owner").annotate(count=Count("id", distinct=True)):
            identity = resolve_employee_identity(
                user_id=row["assigned_to_id"],
                owner_text=row["owner"],
                index=identity_index,
            )
            if identity["user_id"]:
                counts[identity["user_id"]] = counts.get(identity["user_id"], 0) + row["count"]
            else:
                unassigned += row["count"]
        return total, counts, unassigned

    assignee_total, assignee_counts, unassigned_count = count_assignees(qs)
    assignee_summary = [
        {
            "label": "All users",
            "count": assignee_total,
            "caption": "total leads",
            "url": build_assignee_url(""),
            "active": not assigned_to,
        }
    ]

    priority_users = []
    for label in ("Admin604", "Refat"):
        label_key = label.lower()
        for user in users:
            user_text = " ".join(
                filter(None, [user.username, user.first_name, user.last_name, user.email])
            ).lower()
            if user.pk not in {u.pk for u in priority_users} and label_key in user_text:
                priority_users.append(user)
                break

    seen_user_ids = set()

    def add_assignee_item(user, force=False):
        count = assignee_counts.get(user.pk, 0)
        if not force and count <= 0:
            return
        seen_user_ids.add(user.pk)
        assignee_summary.append(
            {
                "label": display_user(user),
                "count": count,
                "caption": "lead" if count == 1 else "leads",
                "url": build_assignee_url(user.pk),
                "active": assigned_to == str(user.pk) or assigned_to_key == (user.username or "").lower(),
            }
        )

    for user in priority_users:
        add_assignee_item(user, force=True)

    for user in users:
        if user.pk in seen_user_ids:
            continue
        add_assignee_item(user)

    if unassigned_count:
        assignee_summary.append(
            {
                "label": "Unassigned",
                "count": unassigned_count,
                "caption": "lead" if unassigned_count == 1 else "leads",
                "url": build_assignee_url("unassigned"),
                "active": assigned_to_unassigned,
            }
        )

    if assigned_to_unassigned:
        if isinstance(qs, list):
            qs = [lead for lead in qs if resolve_lead_owner(lead, index=identity_index)["user_id"] is None]
        else:
            qs = qs.filter(assigned_to__isnull=True).exclude(known_employee_owner_q(index=identity_index))
    elif assigned_to_id:
        selected_user = user_by_id.get(assigned_to_id)
        if isinstance(qs, list):
            qs = [
                lead for lead in qs
                if resolve_lead_owner(lead, index=identity_index)["user_id"] == assigned_to_id
            ]
        else:
            qs = qs.filter(employee_lead_ownership_q(selected_user or assigned_to_id, index=identity_index))
    elif assigned_to:
        selected_user = next((user for user in users if (user.username or "").casefold() == assigned_to_key), None)
        if isinstance(qs, list):
            selected_user_id = getattr(selected_user, "pk", None)
            qs = [
                lead for lead in qs
                if resolve_lead_owner(lead, index=identity_index)["user_id"] == selected_user_id
            ]
        else:
            qs = qs.filter(employee_lead_ownership_q(selected_user, index=identity_index)) if selected_user else qs.none()

    paginator = Paginator(qs, per_page)
    page_number = request.GET.get("page") or 1
    page_obj = paginator.get_page(page_number)
    page_obj.object_list = attach_primary_reference_images_to_leads(page_obj.object_list)
    page_obj.object_list = _decorate_leads_for_list(page_obj.object_list, today=today)
    for lead in page_obj.object_list:
        lead.can_claim = can_claim_sales_lead(request.user) and is_available_sales_lead(lead)
        lead.can_release = can_release_sales_lead(request.user, lead)
        lead.can_edit = can_manage_leads or lead.assigned_to_id == request.user.pk
        lead.can_hard_delete = bool(
            can_archive_records
            and not lead.opportunity_count
            and not lead.list_has_production_orders
            and not lead.list_has_invoices
            and not lead.list_has_activities
            and not lead.list_has_tasks
            and not lead.list_has_comments
            and not lead.list_has_events
            and not lead.list_has_reference_images
        )

    context = {
        "page_obj": page_obj,
        "per_page": per_page,
        "lead_status_filter_choices": LEAD_LIST_STATUS_CHOICES,
        "selected_lead_status": selected_lead_status,
        "status_choices": LEAD_STATUS_CHOICES,
        "market_choices": Lead.MARKET_CHOICES,
        "outbound_status_choices": OUTBOUND_STATUS_CHOICES,
        "qual_status_choices": LEAD_QUAL_STATUS_CHOICES,
        "active_view": view,
        "archive_filter": archive_filter,
        "can_archive_records": can_archive_records,
        "can_manage_leads": can_manage_leads,
        "can_claim_leads": can_claim_sales_lead(request.user),
        "users": users,
        "assigned_to_filter": assigned_to,
        "assignee_summary": assignee_summary,
    }
    return render(request, "crm/leads_list.html", context)


def _lead_assignment_return(request, lead):
    return redirect(request.POST.get("next") or request.META.get("HTTP_REFERER") or reverse("lead_detail", args=[lead.pk]))


@require_POST
def lead_claim(request, pk):
    lead = get_object_or_404(Lead.objects.select_related("assigned_to"), pk=pk)
    if not can_claim_sales_lead(request.user):
        return HttpResponseForbidden("You do not have permission to claim leads.")

    before = model_snapshot(lead)
    updated = Lead.objects.filter(pk=lead.pk).filter(available_sales_lead_q()).update(
        assigned_to=request.user,
    )
    if not updated:
        messages.info(request, "This lead is no longer available to claim.")
        return _lead_assignment_return(request, lead)

    lead.refresh_from_db()
    schedule_audit(lead, before=before)
    messages.success(request, f"Lead {lead.lead_id} is now assigned to you.")
    return _lead_assignment_return(request, lead)


@require_POST
def lead_release(request, pk):
    lead = get_object_or_404(Lead.objects.select_related("assigned_to"), pk=pk)
    if not can_release_sales_lead(request.user, lead):
        return HttpResponseForbidden("You do not have permission to release this lead.")

    before = model_snapshot(lead)
    releasable = Lead.objects.filter(pk=lead.pk, assigned_to__isnull=False)
    if not can_manage_all_sales_records(request.user):
        releasable = releasable.filter(assigned_to=request.user)
    updated = releasable.update(assigned_to=None)
    if not updated:
        messages.info(request, "This lead is already unassigned.")
        return _lead_assignment_return(request, lead)

    lead.refresh_from_db()
    schedule_audit(lead, before=before)
    messages.success(request, f"Lead {lead.lead_id} returned to the available queue.")
    return _lead_assignment_return(request, lead)


@require_POST
def lead_bulk_update(request):
    lead_ids = request.POST.getlist("lead_ids")
    action = (request.POST.get("bulk_action") or "").strip()
    return_url = request.POST.get("return_url") or request.META.get("HTTP_REFERER") or "/leads/"

    if not lead_ids:
        messages.error(request, "Select at least one lead.")
        return redirect(return_url)

    qs = scope_owned_sales_leads(Lead.objects.filter(id__in=lead_ids), request.user)

    if action == "assign":
        if not can_manage_all_sales_records(request.user):
            return HttpResponseForbidden("You do not have permission to reassign leads.")
        assigned_to_id = request.POST.get("assigned_to") or ""
        if assigned_to_id:
            qs.update(assigned_to_id=assigned_to_id)
            messages.success(request, "Assigned leads updated.")
        else:
            messages.error(request, "Choose a user to assign.")

    elif action == "outbound_status":
        status = request.POST.get("outbound_status") or ""
        if status:
            qs.filter(lead_type="outbound").update(outbound_status=status)
            messages.success(request, "Outbound status updated.")
        else:
            messages.error(request, "Choose an outbound status.")

    elif action == "followup":
        date_raw = (request.POST.get("next_follow_up_date") or "").strip()
        follow_up = parse_date(date_raw) if date_raw else None
        if follow_up:
            qs.update(next_follow_up_date=follow_up, next_followup=follow_up)
            messages.success(request, "Follow up date updated.")
        else:
            messages.error(request, "Choose a follow up date.")

    elif action == "archive":
        if not _can_archive_workflow_record(request.user):
            messages.error(request, "You do not have permission to archive leads.")
        else:
            now = timezone.now()
            qs.update(
                is_archived=True,
                archived_at=now,
                archived_by=request.user if request.user.is_authenticated else None,
            )
            qs.filter(lead_type="outbound").update(outbound_status="Archived")
            messages.success(request, "Selected leads archived.")

    else:
        messages.error(request, "Select a bulk action.")

    return redirect(return_url)


@require_POST
def lead_archive(request, pk):
    lead = get_object_or_404(Lead, pk=pk)
    if not _can_archive_workflow_record(request.user):
        messages.error(request, "You do not have permission to archive leads.")
        return redirect("lead_detail", pk=lead.pk)

    had_opportunities = lead.opportunities.exists()
    _archive_workflow_record(lead, request.user)
    label = _workflow_object_label(lead)
    _log_lead_workflow_note(lead, request.user, f"Lead archived by {_user_display_name(request.user)}.")
    _log_workflow_safety_action(
        request,
        action="archive",
        record=lead,
        message=f"Lead {label} archived.",
        meta={"linked_records": _lead_linked_record_labels(lead)},
    )
    if lead.lead_type == "outbound" and lead.outbound_status != "Archived":
        lead.outbound_status = "Archived"
        lead.save(update_fields=["outbound_status"])

    if had_opportunities:
        messages.warning(
            request,
            "Lead archived. This lead has linked opportunity records, so linked records were preserved.",
        )
    else:
        messages.success(request, "Lead archived. History is preserved.")
    return redirect(request.POST.get("next") or request.POST.get("return_url") or "leads_list")


@require_POST
def lead_delete(request, pk):
    lead = get_object_or_404(Lead, pk=pk)
    if not _can_archive_workflow_record(request.user):
        messages.error(request, "You do not have permission to delete leads.")
        return redirect("lead_detail", pk=lead.pk)

    return lead_archive(request, pk)


def leads_dashboard(request):
    qs = _active_lead_queryset()
    outbound = qs.filter(lead_type="outbound")
    inbound = qs.filter(lead_type="inbound")
    today = timezone.localdate()

    kpis = {
        "total_outbound": outbound.count(),
        "active_outbound": outbound.exclude(outbound_status__in=["Archived", "Bad Fit"]).count(),
        "high_fit": outbound.filter(Q(brand_fit_score__gte=70) | Q(ideal_customer_profile_match=True)).count(),
        "first_contact": outbound.filter(outbound_status="First Contact Sent").count(),
        "followups_due": outbound.filter(
            Q(next_follow_up_date__lte=today) | Q(next_followup__lte=today),
            last_reply_date__isnull=True,
        ).exclude(outbound_status__in=["Archived", "Bad Fit"]).count(),
        "replied": outbound.filter(outbound_status="Replied").count(),
        "meeting": outbound.filter(outbound_status="Meeting Booked").count(),
        "quote": outbound.filter(outbound_status="Quote Requested").count(),
        "sample": outbound.filter(outbound_status="Sample Discussion").count(),
        "converted": outbound.filter(outbound_status="Converted to Opportunity").count(),
        "no_response": outbound.filter(outbound_status="No Response").count(),
        "bad_fit": outbound.filter(outbound_status="Bad Fit").count(),
        "total_inbound": inbound.count(),
    }

    by_source = (
        outbound.values("source_channel")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    by_method = (
        outbound.values("outbound_method")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    assigned_counts = defaultdict(int)
    identity_index = get_employee_identity_index()
    for row in outbound.values("assigned_to_id", "owner").annotate(count=Count("id")):
        identity = resolve_employee_identity(
            user_id=row["assigned_to_id"],
            owner_text=row["owner"],
            index=identity_index,
        )
        assigned_counts[identity["canonical_name"]] += int(row["count"] or 0)
    by_assigned = [
        {
            "assigned_to__username": "",
            "assigned_to__first_name": name,
            "assigned_to__last_name": "",
            "count": count,
        }
        for name, count in sorted(assigned_counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    by_country = (
        outbound.values("country").annotate(count=Count("id")).order_by("-count")
    )
    by_product = (
        outbound.values("product_interest").annotate(count=Count("id")).order_by("-count")
    )

    fit_ranges = [
        {"label": "0 to 39", "count": outbound.filter(brand_fit_score__lte=39).count()},
        {"label": "40 to 69", "count": outbound.filter(brand_fit_score__gte=40, brand_fit_score__lte=69).count()},
        {"label": "70 to 100", "count": outbound.filter(brand_fit_score__gte=70).count()},
    ]

    funnel = [
        {"label": "Leads added", "count": outbound.count()},
        {"label": "Contacted", "count": outbound.exclude(outbound_status__in=["", "Not Contacted"]).count()},
        {"label": "Replied", "count": outbound.filter(outbound_status="Replied").count()},
        {"label": "Meeting booked", "count": outbound.filter(outbound_status="Meeting Booked").count()},
        {"label": "Quote requested", "count": outbound.filter(outbound_status="Quote Requested").count()},
        {"label": "Converted", "count": outbound.filter(outbound_status="Converted to Opportunity").count()},
    ]
    funnel_max = max([f["count"] for f in funnel]) if funnel else 0
    for f in funnel:
        if funnel_max:
            f["percent"] = round((f["count"] / funnel_max) * 100, 2)
        else:
            f["percent"] = 0

    context = {
        "kpis": kpis,
        "by_source": by_source,
        "by_method": by_method,
        "by_assigned": by_assigned,
        "by_country": by_country,
        "by_product": by_product,
        "fit_ranges": fit_ranges,
        "funnel": funnel,
        "funnel_max": funnel_max,
    }
    return render(request, "crm/leads_dashboard.html", context)


def lead_intake_dashboard(request):
    qs = Lead.objects.all()

    total_leads = qs.count()
    status_counts = []
    for value, label in LEAD_QUAL_STATUS_CHOICES:
        status_counts.append(
            {"label": label, "count": qs.filter(qualification_status=value).count()}
        )

    missing_website = qs.exclude(Q(website__gt="") | Q(company_website__gt="")).count()

    has_contact_ids = qs.filter(
        Q(email__gt="")
        | Q(phone__gt="")
        | Q(instagram_handle__gt="")
        | Q(linkedin_url__gt="")
        | Q(contact_points__isnull=False)
    ).values_list("id", flat=True).distinct()
    missing_contact = qs.exclude(id__in=list(has_contact_ids)).count()

    outreach_ready = qs.filter(qualification_status__in=["Outreach Ready", "Strong Fit"]).count()

    recent_jobs = LeadImportJob.objects.all()[:10]

    context = {
        "total_leads": total_leads,
        "status_counts": status_counts,
        "missing_website": missing_website,
        "missing_contact": missing_contact,
        "outreach_ready": outreach_ready,
        "recent_jobs": recent_jobs,
    }
    return render(request, "crm/lead_intake_dashboard.html", context)


def lead_import_outbound(request):
    if request.method == "POST":
        file = request.FILES.get("csv_file")
        if not file:
            messages.error(request, "Please upload a CSV or Excel file.")
            return redirect("lead_import_outbound")

        job = LeadImportJob.objects.create(
            file=file,
            created_by=request.user if request.user.is_authenticated else None,
            status="queued",
        )
        messages.success(request, "Import job queued. Processing can take a few minutes.")
        return redirect("lead_import_job_detail", job_id=job.pk)

    jobs = LeadImportJob.objects.all()[:20]
    return render(request, "crm/lead_import.html", {"jobs": jobs})


def lead_import_job_detail(request, job_id):
    job = get_object_or_404(LeadImportJob, pk=job_id)
    leads = job.leads.all().order_by("-id")[:100]
    return render(
        request,
        "crm/lead_import_job_detail.html",
        {"job": job, "leads": leads},
    )


def _resolve_user_for_import(value):
    if not value:
        return None
    user_model = get_user_model()
    value = value.strip()
    if "@" in value:
        return user_model.objects.filter(email__iexact=value).first()
    return user_model.objects.filter(username__iexact=value).first()


def lead_research_start(request):
    if request.method == "POST":
        website = (request.POST.get("website") or "").strip()
        brand = (request.POST.get("brand") or "").strip()
        country = (request.POST.get("country") or "").strip()
        assigned_to = (request.POST.get("assigned_to") or "").strip()

        if not website:
            messages.error(request, "Please enter a website or domain.")
            return redirect("lead_research_start")

        lead = Lead.objects.create(
            account_brand=brand,
            website=website,
            country=country,
            lead_type="outbound",
            outbound_status="Not Contacted",
            qualification_status="Researching",
            assigned_to=_resolve_user_for_import(assigned_to),
        )

        job = LeadResearchJob.objects.create(
            lead=lead,
            website=website,
            created_by=request.user if request.user.is_authenticated else None,
        )
        messages.success(request, "Research job queued.")
        return redirect("lead_research_job_detail", job_id=job.pk)

    users = get_user_model().objects.filter(
        is_active=True,
        employee_profile__is_archived=False,
    ).order_by("first_name", "last_name", "username")
    return render(request, "crm/lead_research.html", {"users": users})


def lead_research_job_detail(request, job_id):
    job = get_object_or_404(LeadResearchJob, pk=job_id)
    lead = job.lead
    contact_points = lead.contact_points.all()
    insights = lead.ai_insights.all()[:5]
    return render(
        request,
        "crm/lead_research_detail.html",
        {
            "job": job,
            "lead": lead,
            "contact_points": contact_points,
            "insights": insights,
        },
    )


@require_POST
def lead_auto_score(request, pk):
    lead = get_object_or_404(Lead, pk=pk)

    score, strengths = lead.compute_fit_score()

    lead.brand_fit_score = score
    lead.ideal_customer_profile_match = score >= 70
    lead.fit_score_locked = False
    lead.save(update_fields=["brand_fit_score", "ideal_customer_profile_match", "fit_score_locked"])

    LeadActivity.objects.create(
        lead=lead,
        activity_type="ai_summary",
        description=f"Auto scored lead: {score}. Signals: {', '.join(strengths)}",
        user=request.user if request.user.is_authenticated else None,
    )

    return JsonResponse({"ok": True, "score": score, "signals": strengths})


from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.contrib import messages

from .models import Lead, LeadActivity
from .forms import LeadForm, QuickOutboundLeadForm


def _apply_utm_fields(request, lead):
    fields = [
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_content",
        "utm_term",
    ]
    for f in fields:
        val = (request.POST.get(f) or request.GET.get(f) or "").strip()
        if val:
            setattr(lead, f, val)

    if getattr(lead, "utm_source", "") and not getattr(lead, "first_touch_channel", ""):
        lead.first_touch_channel = lead.utm_source
    if getattr(lead, "utm_source", ""):
        lead.last_touch_channel = lead.utm_source


def add_lead(request):
    if request.method == "POST":
        form = LeadForm(request.POST, request.FILES)
        if form.is_valid():
            lead = form.save(commit=False)
            _apply_utm_fields(request, lead)

            if "brand_fit_score" in form.changed_data and form.cleaned_data.get("brand_fit_score") is not None:
                lead.fit_score_locked = True

            if lead.lead_type == "outbound" and not lead.outbound_status:
                lead.outbound_status = "Not Contacted"

            if not lead.created_date:
                lead.created_date = timezone.now().date()

            duplicates = _possible_duplicate_leads(lead)
            if duplicates.exists() and request.POST.get("confirm_duplicate") != "1":
                messages.warning(request, "Possible duplicates found. Review before saving.")
                return render(
                    request,
                    "crm/lead_form.html",
                    {"form": form, "duplicate_leads": duplicates, "confirm_required": True},
                )

            customer = _find_or_create_customer_for_lead(lead)
            lead.customer = customer
            lead.save()
            reference_images, reference_upload_count = save_reference_images_for_lead(
                lead,
                reference_image_payload_from_cleaned_data(form.cleaned_data),
                request.user,
            )

            LeadActivity.objects.create(
                lead=lead,
                activity_type="lead_created",
                description="Lead created from form.",
            )

            _record_customer_event(
                customer=customer,
                event_type="lead_created",
                title="Lead created",
                details=f"Lead {lead.lead_id} created.",
            )

            if getattr(lead, "attachment", None):
                LeadActivity.objects.create(
                    lead=lead,
                    activity_type="file_uploaded",
                    description=f"File uploaded: {lead.attachment.name}",
                )

            if reference_upload_count:
                LeadActivity.objects.create(
                    lead=lead,
                    activity_type="file_uploaded",
                    description=f"{reference_upload_count} product reference image(s) uploaded.",
                )

            messages.success(request, "Lead saved successfully.")
            return redirect(f"{redirect('lead_detail', pk=lead.pk).url}?saved=1")
        else:
            messages.error(request, "Could not save. Please fix the errors below.")
            print("LEAD FORM ERRORS:", form.errors.as_json())
    else:
        form = LeadForm()

    return render(request, "crm/lead_form.html", {"form": form})


@require_POST
def lead_reference_images_update(request, pk):
    lead = get_object_or_404(Lead, pk=pk)
    try:
        _saved_images, upload_count = save_reference_images_for_lead(
            lead,
            reference_image_payload_from_request(request),
            request.user,
        )
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
        return redirect("lead_detail", pk=lead.pk)

    if upload_count:
        LeadActivity.objects.create(
            lead=lead,
            activity_type="file_uploaded",
            description=f"{upload_count} product reference image(s) updated.",
            user=request.user if request.user.is_authenticated else None,
        )
        messages.success(request, "Product reference images updated.")
    else:
        messages.success(request, "Product reference captions updated.")
    return redirect("lead_detail", pk=lead.pk)


def quick_add_outbound_lead(request):
    if request.method == "POST":
        form = QuickOutboundLeadForm(request.POST)
        if form.is_valid():
            lead = form.save(commit=False)
            lead.lead_type = "outbound"
            if not lead.outbound_status:
                lead.outbound_status = "Not Contacted"

            if not lead.created_date:
                lead.created_date = timezone.now().date()

            duplicates = _possible_duplicate_leads(lead)
            if duplicates.exists() and request.POST.get("confirm_duplicate") != "1":
                messages.warning(request, "Possible duplicates found. Review before saving.")
                return render(
                    request,
                    "crm/lead_quick_add.html",
                    {"form": form, "duplicate_leads": duplicates, "confirm_required": True},
                )

            customer = _find_or_create_customer_for_lead(lead)
            lead.customer = customer
            lead.save()

            LeadActivity.objects.create(
                lead=lead,
                activity_type="lead_created",
                description="Outbound lead created (quick add).",
            )

            messages.success(request, "Outbound lead created.")
            return redirect(f"{redirect('lead_detail', pk=lead.pk).url}?saved=1")
        else:
            messages.error(request, "Could not save. Please fix the errors below.")
    else:
        form = QuickOutboundLeadForm()

    return render(request, "crm/lead_quick_add.html", {"form": form})


def edit_lead(request, pk):
    lead = get_object_or_404(scope_owned_sales_leads(Lead.objects.all(), request.user), pk=pk)

    if request.method == "POST":
        form = LeadForm(request.POST, request.FILES, instance=lead)
        if form.is_valid():
            updated = form.save(commit=False)

            if "brand_fit_score" in form.changed_data and form.cleaned_data.get("brand_fit_score") is not None:
                updated.fit_score_locked = True

            duplicates = _possible_duplicate_leads(updated, exclude_id=lead.pk)
            if duplicates.exists() and request.POST.get("confirm_duplicate") != "1":
                messages.warning(request, "Possible duplicates found. Review before saving.")
                return render(
                    request,
                    "crm/lead_form.html",
                    {"form": form, "lead": lead, "duplicate_leads": duplicates, "confirm_required": True},
                )

            lead = updated
            lead.save()
            if lead.lead_type == "outbound" and not lead.outbound_status:
                lead.outbound_status = "Not Contacted"
                lead.save(update_fields=["outbound_status"])

            if getattr(lead, "attachment", None):
                LeadActivity.objects.create(
                    lead=lead,
                    activity_type="file_uploaded",
                    description=f"File uploaded or updated: {lead.attachment.name}",
                )

            messages.success(request, "Lead updated successfully.")
            return redirect(f"{redirect('lead_detail', pk=lead.pk).url}?saved=1")
        else:
            messages.error(request, "Could not save. Please fix the errors below.")
            print("LEAD FORM ERRORS:", form.errors.as_json())
    else:
        form = LeadForm(instance=lead)

    return render(request, "crm/lead_form.html", {"form": form, "lead": lead})


@require_POST
def lead_merge(request, pk):
    primary = get_object_or_404(Lead, pk=pk)
    duplicate_id = (request.POST.get("duplicate_id") or "").strip()
    if not duplicate_id:
        messages.error(request, "Select a duplicate lead to merge.")
        return redirect("lead_detail", pk=primary.pk)
    if str(primary.pk) == str(duplicate_id):
        messages.error(request, "Cannot merge the same lead.")
        return redirect("lead_detail", pk=primary.pk)

    duplicate = get_object_or_404(Lead, pk=duplicate_id)
    _merge_leads(primary, duplicate, request.user if request.user.is_authenticated else None)
    messages.success(request, f"Merged lead {duplicate.lead_id} into {primary.lead_id}.")
    return redirect("lead_detail", pk=primary.pk)

from .models import Lead, Customer, Opportunity

def opportunity_create_manual(request):
    leads = Lead.objects.all().order_by("-created_date")
    customers = Customer.objects.all().order_by("account_brand")

    if request.method == "POST":
        lead_id = request.POST.get("lead_id")
        customer_id = request.POST.get("customer_id")

        stage = request.POST.get("stage") or "Prospecting"
        product_type = request.POST.get("product_type") or "Other"
        product_category = request.POST.get("product_category") or "Other"
        notes = (request.POST.get("notes") or "").strip()
        order_currency = (request.POST.get("order_currency") or "CAD").upper()
        if order_currency not in {"CAD", "USD", "BDT"}:
            order_currency = "CAD"
        moq_units_raw = request.POST.get("moq_units")
        order_value_raw = request.POST.get("order_value")
        order_value_usd_raw = request.POST.get("order_value_usd")
        fx_rate_raw = request.POST.get("fx_rate_bdt_per_usd")

        lead = get_object_or_404(Lead, pk=lead_id)

        selected_customer = None
        if customer_id:
            selected_customer = get_object_or_404(Customer, pk=customer_id)

        customer = lead.customer if lead.customer_id else selected_customer
        if not customer:
            customer = _find_or_create_customer_for_lead(lead)

        if not lead.customer_id and customer:
            lead.customer = customer
            lead.save(update_fields=["customer"])

        moq_units = None
        if moq_units_raw:
            try:
                moq_units = int(moq_units_raw)
            except ValueError:
                moq_units = None

        order_value = _safe_decimal_or_none(order_value_raw)
        order_value_usd = _safe_decimal_or_none(order_value_usd_raw)
        fx_rate = _safe_decimal_or_none(fx_rate_raw)

        if order_value_usd is not None:
            order_value = _calc_order_value_bdt(order_value_usd, fx_rate, order_currency)

        opp = Opportunity.objects.create(
            lead=lead,
            customer=customer,
            stage=stage,
            product_type=product_type,
            product_category=product_category,
            moq_units=moq_units,
            order_currency=order_currency,
            order_value=order_value,
            order_value_usd=order_value_usd,
            fx_rate_bdt_per_usd=fx_rate,
            notes=notes,
        )
        link_reference_images_to_opportunity(lead, opp)
        if lead:
            lead.lead_status = "Converted"
            lead.save(update_fields=["lead_status"])

        _record_customer_event(
            customer=customer,
            event_type="opportunity_created",
            title="Opportunity created",
            details=f"Opportunity {opp.opportunity_id} created.",
            opportunity=opp,
        )

        return redirect("opportunity_detail", pk=opp.pk)

    context = {
        "leads": leads,
        "customers": customers,
        "stage_choices": Opportunity.STAGE_CHOICES,
        "product_type_choices": Opportunity.PRODUCT_TYPE_CHOICES,
        "product_category_choices": Opportunity.PRODUCT_CATEGORY_CHOICES,
        "currency_choices": Opportunity.ORDER_CURRENCY_CHOICES,
        "default_currency": "CAD",
    }
    return render(request, "crm/opportunity_create_manual.html", context)

# ===================================================
# LEAD DETAIL PAGE (COMMENTS, TASKS, AI CHAT)
# ===================================================

from datetime import datetime
from decimal import Decimal

from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from crm.models import (
    Lead,
    Customer,
    LeadComment,
    LeadTask,
    LeadActivity,
    Event,
    ExchangeRate,
)
from aihub.models import AIAgent, AIConversation, AIMessage

# If you are using OpenAI client in this file already, keep your existing import/client setup.


def _to_decimal(value) -> Decimal:
    try:
        if value is None:
            return Decimal("0")
        if isinstance(value, Decimal):
            return value
        s = str(value).strip()
        if not s:
            return Decimal("0")
        # allow commas
        s = s.replace(",", "")
        return Decimal(s)
    except Exception:
        return Decimal("0")


def _repair_lead_schema_if_needed() -> None:
    """
    Repair drifted crm_lead columns used by lead detail.
    Safe no-op when schema is already correct.
    """
    required_sql = {
        "confidence_level": "ALTER TABLE crm_lead ADD COLUMN confidence_level INTEGER NOT NULL DEFAULT 0",
        "last_enriched_at": "ALTER TABLE crm_lead ADD COLUMN last_enriched_at DATETIME NULL",
        "product_category_guess": "ALTER TABLE crm_lead ADD COLUMN product_category_guess VARCHAR(120) NOT NULL DEFAULT ''",
        "qualification_reason": "ALTER TABLE crm_lead ADD COLUMN qualification_reason TEXT NOT NULL DEFAULT ''",
        "qualification_status": "ALTER TABLE crm_lead ADD COLUMN qualification_status VARCHAR(40) NOT NULL DEFAULT 'Raw Imported'",
        "recommended_channel": "ALTER TABLE crm_lead ADD COLUMN recommended_channel VARCHAR(120) NOT NULL DEFAULT ''",
        "recommended_next_action": "ALTER TABLE crm_lead ADD COLUMN recommended_next_action VARCHAR(200) NOT NULL DEFAULT ''",
        "target_order_range_estimate": "ALTER TABLE crm_lead ADD COLUMN target_order_range_estimate VARCHAR(120) NOT NULL DEFAULT ''",
        "import_job_id": "ALTER TABLE crm_lead ADD COLUMN import_job_id BIGINT NULL",
    }
    with connection.cursor() as cursor:
        cursor.execute("PRAGMA table_info('crm_lead')")
        existing = {row[1] for row in cursor.fetchall()}
        for col, sql in required_sql.items():
            if col not in existing:
                cursor.execute(sql)
        cursor.execute("CREATE INDEX IF NOT EXISTS crm_lead_qualification_status_idx ON crm_lead(qualification_status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS crm_lead_import_job_id_idx ON crm_lead(import_job_id)")


def _get_latest_cad_to_bdt_rate() -> Decimal:
    """
    Returns latest 1 CAD -> BDT rate from ExchangeRate table.
    Safe fallback to 0 if not found.
    """
    try:
        row = ExchangeRate.objects.order_by("-updated_at").first()
        if not row:
            return Decimal("0")
        rate = _to_decimal(getattr(row, "cad_to_bdt", None))
        return rate
    except Exception:
        return Decimal("0")


def _active_opportunity_stages():
    inactive = {"Production", "Closed Won", "Closed Lost", "Shipment Complete"}
    return [value for value, _ in Opportunity.STAGE_CHOICES if value not in inactive]


def _production_completed_statuses():
    return {"done", "completed", "closed_won"}


def _production_active_statuses():
    return {"planning", "in_progress", "hold"}


PRODUCTION_CONTROL_BUCKET_LABELS = {
    "sampling": "Sampling",
    "fabric": "Fabric sourcing",
    "cutting": "Cutting",
    "printing": "Printing",
    "sewing": "Sewing",
    "qc": "QC",
    "packing": "Packing",
    "shipped": "Shipped",
}


def _production_assignee_label(order):
    opportunity = getattr(order, "opportunity", None)
    lead = getattr(order, "lead", None)
    if opportunity and getattr(opportunity, "assigned_to", None):
        return str(opportunity.assigned_to)
    if lead and getattr(lead, "assigned_to", None):
        return str(lead.assigned_to)
    if lead and getattr(lead, "owner", None):
        return str(lead.owner)
    return "Factory team"


def _stage_status_label(status):
    return dict(ProductionStage.STATUS_CHOICES).get(status, status.replace("_", " ").title() if status else "Planned")


def _production_stage_lookup(stages):
    return {stage.stage_key: stage for stage in stages}


def _production_any_stage_started(stage_lookup, keys):
    return any(
        stage_lookup.get(key) and stage_lookup[key].status in {"in_progress", "hold", "delay", "done"}
        for key in keys
    )


def _production_any_stage_done(stage_lookup, keys):
    return any(stage_lookup.get(key) and stage_lookup[key].status == "done" for key in keys)


def _production_stage_card(label, key, status, *, date_value=None, assigned_to=None, note="", stage=None):
    if stage:
        status = stage.status or status
        date_value = stage.actual_end or stage.actual_start or stage.planned_end or stage.planned_start
        note = stage.notes or note
        label = stage.display_name or label
        if stage.is_late and status != "done":
            status = "delay"

    return SimpleNamespace(
        key=key,
        label=label,
        status=status or "planned",
        status_label=_stage_status_label(status or "planned"),
        date=date_value,
        assigned_to=assigned_to or "Factory team",
        note=note,
        stage=stage,
    )


def _production_order_needs_print(order):
    print_text = " ".join(
        [
            getattr(order, "title", "") or "",
            getattr(order, "style_name", "") or "",
            getattr(order, "notes", "") or "",
            getattr(order, "accessories_note", "") or "",
            getattr(order, "extra_order_note", "") or "",
        ]
    ).lower()
    return any(token in print_text for token in ["print", "embroidery", "screen", "sublimation", "puff"])


def _production_visual_stages(order, stages, shipments=None):
    shipments = shipments or []
    stage_lookup = _production_stage_lookup(stages)
    assigned_to = _production_assignee_label(order)
    cards = []

    sampling_stage = stage_lookup.get("sampling") or stage_lookup.get("development")
    cards.append(_production_stage_card("Sampling", "sampling", "planned", assigned_to=assigned_to, stage=sampling_stage))

    required_kg = _safe_decimal_or_none(getattr(order, "fabric_required_kg", None)) or Decimal("0")
    received_kg = _safe_decimal_or_none(getattr(order, "fabric_received_kg", None)) or Decimal("0")
    if required_kg and received_kg >= required_kg:
        fabric_status = "done"
        fabric_note = f"{received_kg} kg received"
    elif received_kg:
        fabric_status = "in_progress"
        fabric_note = f"{received_kg} kg received"
    elif _production_any_stage_started(stage_lookup, ["cutting", "sewing", "qc", "finishing", "packing", "shipping"]):
        fabric_status = "done"
        fabric_note = "Fabric cleared for production"
    else:
        fabric_status = "planned"
        fabric_note = "Fabric sourcing"
    cards.append(
        _production_stage_card(
            "Fabric",
            "fabric",
            fabric_status,
            date_value=getattr(order, "updated_at", None) if fabric_status != "planned" else None,
            assigned_to=assigned_to,
            note=fabric_note,
        )
    )

    cutting_stage = stage_lookup.get("cutting")
    cards.append(_production_stage_card("Cutting", "cutting", "planned", assigned_to=assigned_to, stage=cutting_stage))

    needs_print = _production_order_needs_print(order)
    if not needs_print:
        print_status = "planned"
        print_note = "Not specified"
        print_date = None
    elif _production_any_stage_started(stage_lookup, ["sewing", "qc", "finishing", "packing", "shipping"]):
        print_status = "done"
        print_note = "Artwork step cleared"
        print_date = getattr(order, "updated_at", None)
    elif _production_any_stage_done(stage_lookup, ["cutting"]):
        print_status = "in_progress"
        print_note = "Ready after cutting"
        print_date = getattr(order, "updated_at", None)
    else:
        print_status = "planned"
        print_note = "Awaiting cutting"
        print_date = None
    cards.append(
        _production_stage_card(
            "Printing",
            "printing",
            print_status,
            date_value=print_date,
            assigned_to=assigned_to,
            note=print_note,
        )
    )

    sewing_stage = stage_lookup.get("sewing")
    cards.append(_production_stage_card("Sewing", "sewing", "planned", assigned_to=assigned_to, stage=sewing_stage))

    qc_stage = stage_lookup.get("qc")
    cards.append(_production_stage_card("QC", "qc", "planned", assigned_to=assigned_to, stage=qc_stage))

    packing_stage = stage_lookup.get("packing") or stage_lookup.get("finishing") or stage_lookup.get("ironing")
    cards.append(_production_stage_card("Packing", "packing", "planned", assigned_to=assigned_to, stage=packing_stage))

    shipping_stage = stage_lookup.get("shipping")
    delivered_or_shipped = [s for s in shipments if s.status in {"shipped", "out_for_delivery", "delivered"}]
    if delivered_or_shipped:
        latest = sorted(delivered_or_shipped, key=lambda s: s.ship_date or timezone.localdate(), reverse=True)[0]
        shipment_status = "done" if latest.status == "delivered" else "in_progress"
        shipment_note = latest.get_status_display()
        shipment_date = latest.ship_date or latest.updated_at
    elif shipping_stage:
        shipment_status = shipping_stage.status
        shipment_note = shipping_stage.notes or shipping_stage.get_status_display()
        shipment_date = shipping_stage.actual_end or shipping_stage.actual_start or shipping_stage.planned_end
    else:
        shipment_status = "planned"
        shipment_note = "Shipment pending"
        shipment_date = None
    cards.append(
        _production_stage_card(
            "Shipment",
            "shipment",
            shipment_status,
            date_value=shipment_date,
            assigned_to=assigned_to,
            note=shipment_note,
            stage=shipping_stage,
        )
    )

    return cards


def _production_stage_bucket(order, stages, shipments=None):
    shipments = shipments or []
    completed_statuses = _production_completed_statuses()
    if order.status in completed_statuses:
        return "shipped"
    if any(s.status in {"shipped", "out_for_delivery", "delivered"} for s in shipments):
        return "shipped"

    stage_lookup = _production_stage_lookup(stages)
    for stage in stages:
        if stage.status in {"in_progress", "hold", "delay"}:
            if stage.stage_key in {"development", "sampling"}:
                return "sampling"
            if stage.stage_key == "cutting":
                return "cutting"
            if stage.stage_key == "sewing":
                return "sewing"
            if stage.stage_key == "qc":
                return "qc"
            if stage.stage_key in {"ironing", "finishing", "packing"}:
                return "packing"
            if stage.stage_key == "shipping":
                return "shipped"

    required_kg = _safe_decimal_or_none(getattr(order, "fabric_required_kg", None)) or Decimal("0")
    received_kg = _safe_decimal_or_none(getattr(order, "fabric_received_kg", None)) or Decimal("0")
    if required_kg and received_kg < required_kg:
        return "fabric"
    if not _production_any_stage_done(stage_lookup, ["sampling", "development"]):
        return "sampling"
    if not _production_any_stage_done(stage_lookup, ["cutting"]):
        return "cutting"
    if _production_order_needs_print(order) and not _production_any_stage_started(stage_lookup, ["sewing", "qc", "finishing", "packing", "shipping"]):
        return "printing"
    if not _production_any_stage_done(stage_lookup, ["sewing"]):
        return "sewing"
    if not _production_any_stage_done(stage_lookup, ["qc"]):
        return "qc"
    if not _production_any_stage_done(stage_lookup, ["packing", "finishing", "ironing"]):
        return "packing"
    return "shipped"


PRODUCTION_WORKFLOW_STEP_STATUSES = [
    OPERATIONAL_STATUS_PLANNING,
    OPERATIONAL_STATUS_SAMPLE_DEVELOPMENT,
    OPERATIONAL_STATUS_SAMPLE_SENT,
    OPERATIONAL_STATUS_APPROVED,
    OPERATIONAL_STATUS_FABRIC_SOURCING,
    OPERATIONAL_STATUS_CUTTING,
    OPERATIONAL_STATUS_PRINTING,
    OPERATIONAL_STATUS_SEWING,
    OPERATIONAL_STATUS_QC,
    OPERATIONAL_STATUS_PACKING,
    OPERATIONAL_STATUS_READY_TO_SHIP,
    OPERATIONAL_STATUS_SHIPPED,
]


def _production_workflow_steps(current_status):
    current_status = current_status if current_status in OPERATIONAL_STATUS_VALUES else OPERATIONAL_STATUS_PLANNING
    if current_status == OPERATIONAL_STATUS_CANCELLED:
        return [
            SimpleNamespace(
                key=status,
                label=OPERATIONAL_STATUS_LABELS[status],
                state="cancelled",
            )
            for status in PRODUCTION_WORKFLOW_STEP_STATUSES
        ]

    try:
        current_index = PRODUCTION_WORKFLOW_STEP_STATUSES.index(current_status)
    except ValueError:
        current_index = 0

    steps = []
    for index, status in enumerate(PRODUCTION_WORKFLOW_STEP_STATUSES):
        if index < current_index:
            state = "done"
        elif index == current_index:
            state = "current"
        else:
            state = "planned"
        steps.append(
            SimpleNamespace(
                key=status,
                label=OPERATIONAL_STATUS_LABELS[status],
                state=state,
            )
        )
    return steps


def _production_priority(order, percent_done, has_delay, late_shipment, shipment_pending, today, operational_status=None):
    completed_statuses = _production_completed_statuses()
    if operational_status == OPERATIONAL_STATUS_CANCELLED:
        return {"key": "low", "label": "Low", "tone": "neutral", "reason": "Cancelled"}
    if operational_status == OPERATIONAL_STATUS_SHIPPED:
        return {"key": "low", "label": "Low", "tone": "good", "reason": "Completed"}
    if not operational_status and order.status in completed_statuses:
        return {"key": "low", "label": "Low", "tone": "good", "reason": "Completed"}

    deadline = getattr(order, "bulk_deadline", None)
    if has_delay or late_shipment:
        return {"key": "urgent", "label": "Urgent", "tone": "risk", "reason": "Delayed"}
    if operational_status == OPERATIONAL_STATUS_READY_TO_SHIP:
        return {"key": "high", "label": "High", "tone": "warning", "reason": "Ready to ship"}
    if operational_status == OPERATIONAL_STATUS_SAMPLE_SENT:
        return {"key": "high", "label": "High", "tone": "warning", "reason": "Sample approval"}
    if deadline and deadline <= today + timedelta(days=2):
        return {"key": "urgent", "label": "Urgent", "tone": "risk", "reason": "Deadline close"}
    if not operational_status and order.status == "hold":
        return {"key": "high", "label": "High", "tone": "warning", "reason": "On hold"}
    if shipment_pending:
        return {"key": "high", "label": "High", "tone": "warning", "reason": "Shipment pending"}
    if deadline and deadline <= today + timedelta(days=7):
        return {"key": "high", "label": "High", "tone": "warning", "reason": "Due this week"}
    if percent_done == 0 and (operational_status == OPERATIONAL_STATUS_PLANNING or order.status == "planning"):
        return {"key": "normal", "label": "Normal", "tone": "neutral", "reason": "Planning"}
    return {"key": "normal", "label": "Normal", "tone": "neutral", "reason": "On track"}


def _production_activity_timeline(order, stages, shipments, lifecycle=None, comments=None):
    items = [
        SimpleNamespace(
            time=order.created_at,
            actor="System",
            action="Production order created",
            status=order.get_status_display(),
            tone="neutral",
        )
    ]
    if order.updated_at and order.updated_at != order.created_at:
        items.append(
            SimpleNamespace(
                time=order.updated_at,
                actor="System",
                action="Production order updated",
                status=order.get_status_display(),
                tone="neutral",
            )
        )

    for stage in stages:
        if not stage.updated_at:
            continue
        tone = "good" if stage.status == "done" else "risk" if stage.status == "delay" else "warning" if stage.status == "hold" else "neutral"
        items.append(
            SimpleNamespace(
                time=stage.updated_at,
                actor="Factory team",
                action=f"{stage.display_name or stage.get_stage_key_display()} stage updated",
                status=stage.get_status_display(),
                tone=tone,
            )
        )

    for shipment in shipments:
        if not shipment.updated_at:
            continue
        tone = "good" if shipment.status == "delivered" else "neutral"
        items.append(
            SimpleNamespace(
                time=shipment.updated_at,
                actor="Shipping",
                action=f"{shipment.get_carrier_display()} shipment updated",
                status=shipment.get_status_display(),
                tone=tone,
            )
        )

    if lifecycle and getattr(lifecycle, "updated_at", None):
        items.append(
            SimpleNamespace(
                time=lifecycle.updated_at,
                actor="Lifecycle",
                action="Order lifecycle updated",
                status=lifecycle.get_status_display() if hasattr(lifecycle, "get_status_display") else lifecycle.status,
                tone="neutral",
            )
        )

    for comment in (comments or [])[:5]:
        items.append(
            SimpleNamespace(
                time=getattr(comment, "created_at", None),
                actor=getattr(comment, "author", "") or "User",
                action="Production note added",
                status="Note",
                tone="neutral",
            )
        )

    items.sort(key=lambda item: item.time or order.created_at, reverse=True)
    return items[:12]


def _find_or_create_customer_for_lead(lead):
    if lead.customer_id:
        return lead.customer

    email = (lead.email or "").strip()
    if email:
        customer = Customer.objects.filter(email__iexact=email).first()
        if customer:
            return customer

    if lead.account_brand:
        customer = Customer.objects.filter(
            account_brand__iexact=lead.account_brand,
            phone=lead.phone or "",
        ).first()
        if customer:
            return customer

    display_name = lead.account_brand or lead.contact_name or "Customer"

    customer = Customer.objects.create(
        account_brand=display_name,
        contact_name=lead.contact_name or "",
        email=lead.email or "",
        phone=lead.phone or "",
        market=getattr(lead, "market", "") or "",
        website=getattr(lead, "website", "") or getattr(lead, "company_website", "") or "",
        city=getattr(lead, "city", "") or "",
        country=getattr(lead, "country", "") or "",
        notes=lead.notes or "",
    )
    return customer


def _ensure_customer_for_opportunity(opportunity):
    if opportunity.customer_id:
        return opportunity.customer

    lead = opportunity.lead
    if lead and lead.customer_id:
        opportunity.customer = lead.customer
        opportunity.save(update_fields=["customer"])
        return opportunity.customer

    if lead:
        customer = _find_or_create_customer_for_lead(lead)
        lead.customer = customer
        lead.save(update_fields=["customer"])
        opportunity.customer = customer
        opportunity.save(update_fields=["customer"])
        return customer

    return None


def _customer_default_salesperson(customer):
    if not customer:
        return None
    latest_opportunity = (
        Opportunity.objects.filter(customer=customer, assigned_to__isnull=False)
        .select_related("assigned_to")
        .order_by("-updated_at", "-id")
        .first()
    )
    if latest_opportunity and latest_opportunity.assigned_to_id:
        return latest_opportunity.assigned_to
    latest_lead = (
        Lead.objects.filter(customer=customer, assigned_to__isnull=False)
        .select_related("assigned_to")
        .order_by("-created_date", "-id")
        .first()
    )
    if latest_lead and latest_lead.assigned_to_id:
        return latest_lead.assigned_to
    return None


def _customer_address_summary(customer):
    if not customer:
        return ""
    parts = [
        customer.address_line1 or customer.shipping_address1,
        customer.address_line2 or customer.shipping_address2,
        customer.city or customer.shipping_city,
        customer.state or customer.shipping_state,
        customer.postal_code or customer.shipping_postcode,
        customer.country or customer.shipping_country,
    ]
    return ", ".join(part for part in parts if part)


_CUSTOMER_CONTEXT_UNSET = object()


def _customer_opportunity_prefill(
    customer,
    *,
    latest_lead=_CUSTOMER_CONTEXT_UNSET,
    latest_opportunity=_CUSTOMER_CONTEXT_UNSET,
    latest_note=_CUSTOMER_CONTEXT_UNSET,
    default_salesperson=_CUSTOMER_CONTEXT_UNSET,
):
    prefill = {
        "product_type": "",
        "product_category": "",
        "product_interest": "",
        "lead_source": "",
        "latest_customer_note": "",
        "notes": "",
        "address": _customer_address_summary(customer),
        "salesperson": None if default_salesperson is _CUSTOMER_CONTEXT_UNSET else default_salesperson,
    }
    if not customer:
        return prefill

    if latest_lead is _CUSTOMER_CONTEXT_UNSET:
        latest_lead = (
            Lead.objects.filter(customer=customer)
            .select_related("assigned_to")
            .order_by("-created_date", "-id")
            .first()
        )
    if latest_opportunity is _CUSTOMER_CONTEXT_UNSET:
        latest_opportunity = (
            _customer_related_opportunities(customer)
            .order_by("-updated_at", "-id")
            .first()
        )
    if default_salesperson is _CUSTOMER_CONTEXT_UNSET:
        if latest_opportunity and latest_opportunity.assigned_to_id:
            prefill["salesperson"] = latest_opportunity.assigned_to
        elif latest_lead and getattr(latest_lead, "assigned_to_id", None):
            prefill["salesperson"] = getattr(latest_lead, "assigned_to", None)
        else:
            prefill["salesperson"] = _customer_default_salesperson(customer)

    if latest_opportunity:
        prefill["product_type"] = latest_opportunity.product_type or ""
        prefill["product_category"] = latest_opportunity.product_category or ""
    if latest_lead:
        prefill["product_interest"] = latest_lead.product_interest or ""
        source_parts = [
            latest_lead.source,
            latest_lead.source_channel,
            latest_lead.first_touch_channel,
        ]
        prefill["lead_source"] = " / ".join(part for part in source_parts if part)
        prefill["product_type"] = prefill["product_type"] or latest_lead.primary_product_type or ""
        prefill["product_category"] = prefill["product_category"] or latest_lead.product_category or ""

    note_lines = [f"Created from customer {customer.customer_code}."]
    if customer.notes:
        note_lines.append(f"Customer notes: {customer.notes}")
    if latest_note is _CUSTOMER_CONTEXT_UNSET:
        latest_note = customer.notes_list.order_by("-created_at").first()
    if latest_note:
        latest_note_content = latest_note if isinstance(latest_note, str) else latest_note.content
        prefill["latest_customer_note"] = latest_note_content
        note_lines.append(f"Latest customer note: {latest_note_content}")
    if prefill["product_interest"]:
        note_lines.append(f"Previous product interest: {prefill['product_interest']}")
    if prefill["address"]:
        note_lines.append(f"Address reference: {prefill['address']}")
    prefill["notes"] = "\n".join(note_lines)
    return prefill


def _customer_related_opportunities(customer):
    if not customer:
        return Opportunity.objects.none()
    return (
        Opportunity.objects
        .filter(Q(customer=customer) | Q(lead__customer=customer))
        .select_related("assigned_to", "lead", "customer")
        .order_by("-updated_at", "-id")
        .distinct()
    )


def _customer_opportunity_amount_and_currency(opportunity):
    currency = (getattr(opportunity, "order_currency", "") or "CAD").upper().strip()
    if currency not in {"CAD", "USD", "BDT"}:
        currency = "CAD"
    if currency == "USD" and opportunity.order_value_usd is not None:
        return _ceo_decimal(opportunity.order_value_usd), "USD"
    return _ceo_decimal(opportunity.order_value), currency


def _annotated_customer_latest_lead(customer):
    if not customer or not hasattr(customer, "context_latest_lead_source"):
        return _CUSTOMER_CONTEXT_UNSET
    has_context = any(
        getattr(customer, attr, None)
        for attr in (
            "context_latest_lead_source",
            "context_latest_lead_source_channel",
            "context_latest_lead_first_touch_channel",
            "context_latest_lead_product_interest",
            "context_latest_lead_primary_product_type",
            "context_latest_lead_product_category",
            "context_latest_lead_assigned_to_id",
        )
    )
    if not has_context:
        return None
    return SimpleNamespace(
        source=getattr(customer, "context_latest_lead_source", "") or "",
        source_channel=getattr(customer, "context_latest_lead_source_channel", "") or "",
        first_touch_channel=getattr(customer, "context_latest_lead_first_touch_channel", "") or "",
        product_interest=getattr(customer, "context_latest_lead_product_interest", "") or "",
        primary_product_type=getattr(customer, "context_latest_lead_primary_product_type", "") or "",
        product_category=getattr(customer, "context_latest_lead_product_category", "") or "",
        assigned_to_id=getattr(customer, "context_latest_lead_assigned_to_id", None),
        assigned_to=None,
    )


def _customer_opportunity_context(customer, *, salesperson_options=None):
    empty_stats = {
        "total_opportunities": 0,
        "open_opportunities": 0,
        "won_opportunities": 0,
        "lost_opportunities": 0,
        "total_revenue_rows": [],
        "outstanding_rows": [],
    }
    context = {
        "stats": empty_stats,
        "previous_opportunities": [],
        "active_opportunities": [],
        "default_salesperson": None,
        "prefill": _customer_opportunity_prefill(None),
    }
    if not customer:
        return context

    opportunities = list(_customer_related_opportunities(customer))
    latest_opportunity = opportunities[0] if opportunities else None
    latest_lead = _annotated_customer_latest_lead(customer)
    if latest_lead is _CUSTOMER_CONTEXT_UNSET:
        latest_lead = (
            Lead.objects.filter(customer=customer)
            .select_related("assigned_to")
            .order_by("-created_date", "-id")
            .first()
        )
    if hasattr(customer, "context_latest_customer_note"):
        latest_note = getattr(customer, "context_latest_customer_note", "") or None
    else:
        latest_note = customer.notes_list.order_by("-created_at").first()
    salesperson_by_id = {
        user.pk: user for user in salesperson_options or []
    }
    default_salesperson = next(
        (opportunity.assigned_to for opportunity in opportunities if opportunity.assigned_to_id),
        None,
    )
    if not default_salesperson and latest_lead and getattr(latest_lead, "assigned_to_id", None):
        default_salesperson = (
            getattr(latest_lead, "assigned_to", None)
            or salesperson_by_id.get(latest_lead.assigned_to_id)
        )

    revenue_totals = defaultdict(lambda: {"amount": Decimal("0")})
    open_opportunities = []
    won_count = 0
    lost_count = 0
    for opportunity in opportunities:
        is_active_open = (
            opportunity.is_open
            and not opportunity.is_archived
            and opportunity.stage not in {"Closed Won", "Closed Lost", "Shipment Complete"}
        )
        if is_active_open:
            open_opportunities.append(opportunity)
        if opportunity.stage == "Closed Won":
            won_count += 1
        elif opportunity.stage == "Closed Lost":
            lost_count += 1
        if opportunity.order_value is None and opportunity.order_value_usd is None:
            continue
        amount, currency_code = _customer_opportunity_amount_and_currency(opportunity)
        revenue_totals[currency_code]["amount"] += amount

    outstanding_totals = defaultdict(lambda: {"amount": Decimal("0")})
    invoices = (
        Invoice.objects
        .filter(Q(customer=customer) | Q(order__customer=customer))
        .exclude(status__in={"paid", "cancelled"})
        .select_related("order", "customer")
        .distinct()
    )
    for invoice in invoices:
        currency_code = (invoice.currency or "").upper().strip() or "CAD"
        outstanding_totals[currency_code]["amount"] += _ceo_decimal(invoice.balance)

    previous_opportunities = []
    for opportunity in opportunities[:8]:
        amount, currency_code = _customer_opportunity_amount_and_currency(opportunity)
        has_value = opportunity.order_value is not None or opportunity.order_value_usd is not None
        previous_opportunities.append(
            {
                "opportunity": opportunity,
                "amount": amount,
                "currency": currency_code,
                "has_value": has_value,
                "salesperson_name": _user_display_name(opportunity.assigned_to),
                "lead_source": getattr(opportunity.lead, "source", "") if opportunity.lead_id else "",
            }
        )

    context["stats"] = {
        "total_opportunities": len(opportunities),
        "open_opportunities": len(open_opportunities),
        "won_opportunities": won_count,
        "lost_opportunities": lost_count,
        "total_revenue_rows": currency_summary_rows(revenue_totals),
        "outstanding_rows": currency_summary_rows(outstanding_totals),
    }
    context["previous_opportunities"] = previous_opportunities
    context["active_opportunities"] = open_opportunities[:5]
    context["default_salesperson"] = default_salesperson
    context["prefill"] = _customer_opportunity_prefill(
        customer,
        latest_lead=latest_lead,
        latest_opportunity=latest_opportunity,
        latest_note=latest_note,
        default_salesperson=default_salesperson,
    )
    return context


def _record_customer_event(*, customer, event_type, title, details="", opportunity=None, production=None):
    if not customer:
        return
    CustomerEvent.objects.create(
        customer=customer,
        event_type=event_type,
        title=title,
        details=details or "",
        opportunity=opportunity,
        production=production,
    )


def _send_chatter_mentions(request, comment):
    if comment and request.user.is_authenticated:
        notify_chatter_mentions(comment, request.user)


def _chatter_for_lead(lead, user=None):
    if user is not None and not can_access_chatter_record(user, "leads", lead):
        return LeadComment.objects.none()
    return (
        LeadComment.objects.select_related("lead", "opportunity", "production", "author_user", "author_user__employee_profile")
        .filter(
            Q(lead=lead)
            | Q(opportunity__lead=lead)
            | Q(production__lead=lead)
        )
        .order_by("-pinned", "-created_at")
        .distinct()
    )


def _chatter_for_opportunity(opportunity, user=None):
    if user is not None and not can_access_chatter_record(user, "opportunities", opportunity):
        return LeadComment.objects.none()
    lead = opportunity.lead
    return (
        LeadComment.objects.select_related("lead", "opportunity", "production", "author_user", "author_user__employee_profile")
        .filter(
            Q(opportunity=opportunity)
            | Q(production__opportunity=opportunity)
            | Q(lead=lead, opportunity__isnull=True, production__isnull=True)
        )
        .order_by("-pinned", "-created_at")
        .distinct()
    )


def _chatter_for_production(order, user=None):
    if user is not None and not can_access_chatter_record(user, "production", order):
        return LeadComment.objects.none()
    lead = _safe_related_attr(order, "lead")
    opportunity = _safe_related_attr(order, "opportunity")
    filters = Q(production=order)
    if opportunity:
        filters |= Q(opportunity=opportunity)
    if lead:
        filters |= Q(lead=lead, opportunity__isnull=True, production__isnull=True)
    return (
        LeadComment.objects.select_related("lead", "opportunity", "production", "author_user", "author_user__employee_profile")
        .filter(filters)
        .order_by("-pinned", "-created_at")
        .distinct()
    )


def _safe_related_attr(instance, attr_name, *, default=None):
    try:
        return getattr(instance, attr_name)
    except (ObjectDoesNotExist, AttributeError, OperationalError, ProgrammingError):
        return default


def _first_from_queryset_or_list(records):
    try:
        if isinstance(records, list):
            return records[0] if records else None
        return records.first()
    except (AttributeError, IndexError, OperationalError, ProgrammingError):
        return None


def _lead_lifecycle_banner(lead, opportunities):
    if not lead:
        return None
    is_converted = (
        getattr(lead, "lead_status", "") == "Converted"
        or getattr(lead, "outbound_status", "") == "Converted to Opportunity"
    )
    if not is_converted:
        return None

    opportunity = _first_from_queryset_or_list(opportunities)
    if not opportunity:
        return None

    return {
        "opportunity": opportunity,
        "date": getattr(opportunity, "created_date", None) or getattr(lead, "created_date", None),
    }


def _opportunity_lifecycle_banner(production_orders):
    order = _first_from_queryset_or_list(production_orders)
    if not order:
        return None

    return {
        "order": order,
        "date": getattr(order, "created_at", None),
    }


def _production_lifecycle_banner(shipments):
    delivered = None
    for shipment in shipments or []:
        if getattr(shipment, "status", "") == "delivered":
            delivered = shipment
            break
    if not delivered:
        return None

    return {
        "shipment": delivered,
        "date": (
            getattr(delivered, "delivered_at", None)
            or getattr(delivered, "ship_date", None)
            or getattr(delivered, "updated_at", None)
            or getattr(delivered, "created_at", None)
        ),
        "reference": getattr(delivered, "tracking_number", "") or f"SHP-{delivered.pk:05d}",
    }


def _lead_detail_impl(request, pk):
    def _safe_fetch(fetcher, fallback, label: str):
        try:
            return fetcher()
        except (OperationalError, ProgrammingError):
            logger.exception("lead_detail: failed to load %s", label)
            return fallback

    def _load_visible_lead():
        return scope_sales_lead_queue(
            Lead.objects.select_related("assigned_to"),
            request.user,
        ).filter(pk=pk).first()

    try:
        lead = _load_visible_lead()
    except OperationalError as exc:
        # Self-heal known schema drift that causes lead detail 500 on stale DB files.
        if "no such column: crm_lead." in str(exc):
            _repair_lead_schema_if_needed()
            lead = _load_visible_lead()
        else:
            raise

    if lead is None:
        if request.method == "POST" and Lead.objects.filter(pk=pk).exists():
            return HttpResponseForbidden("You do not have permission to update this lead.")
        raise Http404

    if request.method == "POST" and not (
        can_manage_all_sales_records(request.user) or lead.assigned_to_id == request.user.pk
    ):
        return HttpResponseForbidden("Claim this lead before updating it.")

    opportunities = _safe_fetch(
        lambda: lead.opportunities.all().order_by("-created_date", "-id"),
        [],
        "opportunities",
    )
    if not isinstance(opportunities, list):
        opportunities = list(opportunities)
    comments = _safe_fetch(lambda: _chatter_for_lead(lead, request.user), [], "comments")
    tasks = _safe_fetch(lambda: lead.tasks.all(), [], "tasks")
    activities = _safe_fetch(lambda: lead.activities.all(), [], "activities")

    customer = lead.customer if lead.customer_id else None

    agents = _safe_fetch(lambda: list(AIAgent.objects.all()), [], "agents")
    selected_agent = None
    chat_messages = []

    # -------------------------
    # Budget display helpers
    # -------------------------
    budget_raw = getattr(lead, "budget", None)
    budget_cad = _to_decimal(budget_raw)

    cad_to_bdt = _get_latest_cad_to_bdt_rate()
    budget_bdt = Decimal("0")
    if cad_to_bdt and cad_to_bdt > 0 and budget_cad and budget_cad > 0:
        budget_bdt = (budget_cad * cad_to_bdt).quantize(Decimal("0.01"))

    # -------------------------
    # POST actions
    # -------------------------
    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()

        # comments
        if action == "add_comment":
            if not can_access_chatter_record(request.user, "leads", lead):
                return HttpResponseForbidden("You do not have access to this lead's chatter.")
            comment_text = (request.POST.get("comment_text") or "").strip()
            attachment = request.FILES.get("attachment")
            if not comment_text and not attachment:
                messages.error(request, "Please write a note or attach a file first.")
            else:
                author_name = employee_display_name(request.user)
                content = comment_text or f"Attachment: {attachment.name}"
                comment = LeadComment.objects.create(
                    lead=lead,
                    author=author_name,
                    author_user=request.user,
                    content=content,
                    attachment=attachment,
                )
                _send_chatter_mentions(request, comment)
                LeadActivity.objects.create(
                    lead=lead,
                    activity_type="note_added",
                    description=content[:200],
                )
            comments = _safe_fetch(lambda: _chatter_for_lead(lead, request.user), [], "comments")

        elif action == "toggle_pin_comment":
            if not can_access_chatter_record(request.user, "leads", lead):
                return HttpResponseForbidden("You do not have access to this lead's chatter.")
            comment_id = (request.POST.get("comment_id") or "").strip()
            if comment_id:
                c = LeadComment.objects.filter(
                    Q(id=comment_id, lead=lead)
                    | Q(id=comment_id, opportunity__lead=lead)
                    | Q(id=comment_id, production__lead=lead)
                ).first()
                if c:
                    c.pinned = not c.pinned
                    c.save(update_fields=["pinned"])
            comments = _safe_fetch(lambda: _chatter_for_lead(lead, request.user), [], "comments")

        # tasks
        elif action == "add_task":
            title = (request.POST.get("task_title") or "").strip()
            due_str = (request.POST.get("task_due_date") or "").strip()
            priority = (request.POST.get("task_priority") or "Medium").strip()
            assigned_to = (request.POST.get("task_assigned_to") or "").strip()
            description = (request.POST.get("task_description") or "").strip()

            if title:
                due_date = None
                if due_str:
                    try:
                        due_date = datetime.fromisoformat(due_str).date()
                    except Exception:
                        due_date = None

                task = LeadTask.objects.create(
                    lead=lead,
                    title=title,
                    description=description,
                    due_date=due_date,
                    priority=priority,
                    assigned_to=assigned_to,
                )
                LeadActivity.objects.create(
                    lead=lead,
                    activity_type="task_created",
                    description=f"Task created: {task.title}"[:200],
                )
            tasks = _safe_fetch(lambda: lead.tasks.all(), [], "tasks")

        elif action == "complete_task":
            task_id = (request.POST.get("task_id") or "").strip()
            if task_id:
                try:
                    task = LeadTask.objects.get(id=task_id, lead=lead)
                    task.status = "Done"
                    task.completed_at = timezone.now()
                    task.save(update_fields=["status", "completed_at"])
                    LeadActivity.objects.create(
                        lead=lead,
                        activity_type="task_completed",
                        description=f"Task completed: {task.title}"[:200],
                    )
                except LeadTask.DoesNotExist:
                    pass
            tasks = _safe_fetch(lambda: lead.tasks.all(), [], "tasks")

        elif action == "add_activity":
            activity_type = (request.POST.get("activity_type") or "").strip()
            channel = (request.POST.get("activity_channel") or "").strip()
            note = (request.POST.get("activity_note") or "").strip()
            message_copy = (request.POST.get("activity_message_copy") or "").strip()
            outcome = (request.POST.get("activity_outcome") or "").strip()
            follow_up_date_raw = (request.POST.get("activity_follow_up_date") or "").strip()

            follow_up_date = None
            if follow_up_date_raw:
                try:
                    follow_up_date = datetime.fromisoformat(follow_up_date_raw).date()
                except Exception:
                    follow_up_date = None

            if activity_type:
                LeadActivity.objects.create(
                    lead=lead,
                    activity_type=activity_type,
                    channel=channel,
                    user=request.user if request.user.is_authenticated else None,
                    note=note,
                    message_copy=message_copy,
                    outcome=outcome,
                    follow_up_date=follow_up_date,
                    description=note[:200] if note else "",
                )
                if activity_type in [
                    "cold_email_sent",
                    "linkedin_message_sent",
                    "instagram_dm_sent",
                    "call_made",
                    "follow_up_sent",
                    "meeting_booked",
                    "quote_shared",
                    "sample_discussion",
                ]:
                    lead.last_outreach_date = timezone.localdate()
                    lead.save(update_fields=["last_outreach_date"])

                if activity_type == "cold_email_sent" and lead.outbound_status in ["", "Not Contacted"]:
                    lead.outbound_status = "First Contact Sent"
                    lead.save(update_fields=["outbound_status"])

                if activity_type == "follow_up_sent":
                    if lead.outbound_status == "Follow Up 1 Sent":
                        lead.outbound_status = "Follow Up 2 Sent"
                    elif lead.outbound_status == "Follow Up 2 Sent":
                        lead.outbound_status = "Follow Up 3 Sent"
                    else:
                        lead.outbound_status = "Follow Up 1 Sent"
                    lead.save(update_fields=["outbound_status"])

                if activity_type == "reply_received":
                    lead.last_reply_date = timezone.localdate()
                    lead.outbound_status = "Replied"
                    lead.save(update_fields=["last_reply_date", "outbound_status"])

                if activity_type == "meeting_booked":
                    lead.outbound_status = "Meeting Booked"
                    lead.save(update_fields=["outbound_status"])

                if activity_type == "quote_shared":
                    lead.outbound_status = "Quote Requested"
                    lead.save(update_fields=["outbound_status"])

                if activity_type == "sample_discussion":
                    lead.outbound_status = "Sample Discussion"
                    lead.save(update_fields=["outbound_status"])

                if follow_up_date:
                    lead.next_follow_up_date = follow_up_date
                    lead.next_followup = follow_up_date
                    lead.save(update_fields=["next_follow_up_date", "next_followup"])
                activities = _safe_fetch(lambda: lead.activities.all(), [], "activities")
                messages.success(request, "Activity logged.")
            else:
                messages.error(request, "Please choose an activity type.")

        # shipping from lead page
        elif action == "save_shipping":
            shipping_name = (request.POST.get("shipping_name") or "").strip()
            shipping_address1 = (request.POST.get("shipping_address1") or "").strip()
            shipping_address2 = (request.POST.get("shipping_address2") or "").strip()
            shipping_city = (request.POST.get("shipping_city") or "").strip()
            shipping_state = (request.POST.get("shipping_state") or "").strip()
            shipping_postcode = (request.POST.get("shipping_postcode") or "").strip()
            shipping_country = (request.POST.get("shipping_country") or "").strip()

            if customer is None:
                customer = Customer.objects.create(
                    lead=lead,
                    account_brand=lead.account_brand,
                    contact_name=lead.contact_name,
                    email=lead.email,
                    phone=lead.phone,
                    market=lead.market,
                )

            customer.shipping_name = shipping_name
            customer.shipping_address1 = shipping_address1
            customer.shipping_address2 = shipping_address2
            customer.shipping_city = shipping_city
            customer.shipping_state = shipping_state
            customer.shipping_postcode = shipping_postcode
            customer.shipping_country = shipping_country
            customer.save()

            LeadActivity.objects.create(
                lead=lead,
                activity_type="shipping_updated",
                description="Shipping address updated from lead page.",
            )

        # manual AI chat
        elif action == "ai_chat":
            agent_id = (request.POST.get("agent_id") or "").strip()
            user_text = (request.POST.get("user_message") or "").strip()

            if agent_id and user_text:
                selected_agent = get_object_or_404(AIAgent, pk=agent_id)
                current_user = request.user if request.user.is_authenticated else None

                conversation, _ = AIConversation.objects.get_or_create(
                    agent=selected_agent,
                    user=current_user,
                    lead=lead,
                    opportunity=None,
                )

                AIMessage.objects.create(
                    conversation=conversation,
                    sender="user",
                    content=user_text,
                )

                history = []
                for msg in conversation.messages.order_by("created_at"):
                    role = "user" if msg.sender == "user" else "assistant"
                    history.append({"role": role, "content": msg.content})

                messages_for_model = [{"role": "system", "content": selected_agent.system_prompt}] + history

                try:
                    resp = client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=messages_for_model,
                    )
                    ai_text = resp.choices[0].message.content
                except Exception as e:
                    ai_text = f"AI error: {e}"

                AIMessage.objects.create(
                    conversation=conversation,
                    sender="ai",
                    content=ai_text,
                )

                chat_messages = conversation.messages.order_by("created_at")

        # quick AI templates
        elif action == "ai_quick":
            quick_action = (request.POST.get("quick_action") or "").strip()

            selected_agent = agents[0] if agents else None
            if selected_agent and quick_action:
                current_user = request.user if request.user.is_authenticated else None

                conversation, _ = AIConversation.objects.get_or_create(
                    agent=selected_agent,
                    user=current_user,
                    lead=lead,
                    opportunity=None,
                )

                lead_info = (
                    f"Brand: {lead.account_brand}. "
                    f"Contact: {lead.contact_name}. "
                    f"Email: {lead.email}. "
                    f"Phone: {lead.phone}. "
                    f"Market: {lead.get_market_display()}. "
                    f"Product interest: {lead.product_interest}. "
                    f"Order quantity: {lead.order_quantity}. "
                    f"Budget: {lead.budget}."
                )

                if quick_action == "cold_email":
                    user_text = "Write a short cold email for this lead. Use friendly tone. Lead info: " + lead_info
                elif quick_action == "warm_followup":
                    user_text = "Write a warm follow up email to this lead. Lead info: " + lead_info
                elif quick_action == "summary":
                    user_text = "Give a short summary of this lead and what they want. Lead info: " + lead_info
                elif quick_action == "client_profile":
                    user_text = "Create a simple client profile for internal use. Lead info: " + lead_info
                elif quick_action == "next_steps":
                    user_text = "Suggest clear next steps for sales follow up for this lead. Lead info: " + lead_info
                elif quick_action == "mood":
                    user_text = "Guess the mood or intent of this lead and how we should reply. Lead info: " + lead_info
                elif quick_action == "product_reco":
                    user_text = "Suggest product ideas from a clothing factory that would fit this lead. Lead info: " + lead_info
                else:
                    user_text = "Help me with this lead: " + lead_info

                AIMessage.objects.create(
                    conversation=conversation,
                    sender="user",
                    content=user_text,
                )

                history = []
                for msg in conversation.messages.order_by("created_at"):
                    role = "user" if msg.sender == "user" else "assistant"
                    history.append({"role": role, "content": msg.content})

                messages_for_model = [{"role": "system", "content": selected_agent.system_prompt}] + history

                try:
                    resp = client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=messages_for_model,
                    )
                    ai_text = resp.choices[0].message.content
                except Exception as e:
                    ai_text = f"AI error: {e}"

                AIMessage.objects.create(
                    conversation=conversation,
                    sender="ai",
                    content=ai_text,
                )

                if quick_action in ["summary", "client_profile", "next_steps"]:
                    LeadComment.objects.create(
                        lead=lead,
                        author="AI",
                        content=ai_text,
                        is_ai=True,
                    )
                    LeadActivity.objects.create(
                        lead=lead,
                        activity_type="ai_summary",
                        description=f"AI quick action: {quick_action}"[:200],
                    )

                chat_messages = conversation.messages.order_by("created_at")

    # -------------------------
    # GET: load latest conversation messages
    # -------------------------
    if request.method != "POST":
        if agents:
            selected_agent = agents[0]
            conversation = (
                AIConversation.objects.filter(agent=selected_agent, lead=lead)
                .order_by("-created_at")
                .first()
            )
            if conversation:
                chat_messages = conversation.messages.order_by("created_at")

    # upcoming events for this lead
    upcoming_events = _safe_fetch(
        lambda: Event.objects.filter(lead=lead, start_datetime__gte=timezone.now()).order_by("start_datetime")[:5],
        [],
        "upcoming_events",
    )
    potential_duplicates = _safe_fetch(
        lambda: _possible_duplicate_leads(lead, exclude_id=lead.pk)[:10],
        [],
        "potential_duplicates",
    )
    wa_inbox_url = ""
    if getattr(settings, "WHATSAPP_ENABLED", False):
        try:
            wa_inbox_url = reverse("wa_api_inbox")
        except NoReverseMatch:
            wa_inbox_url = ""

    def _build_iconic_ai_brain_context():
        from .ai.lead_brain import build_iconic_ai_brain

        latest_insights = _safe_fetch(
            lambda: list(lead.ai_insights.all()[:1]),
            [],
            "iconic_ai_brain_insights",
        )
        return build_iconic_ai_brain(
            lead=lead,
            opportunities=opportunities,
            comments=comments,
            tasks=tasks,
            activities=activities,
            insights=latest_insights,
        )

    try:
        iconic_ai_brain = _build_iconic_ai_brain_context()
    except Exception:
        logger.exception("lead_detail: failed to load iconic_ai_brain")
        iconic_ai_brain = {}

    reference_images = list(reference_images_for_lead(lead))
    primary_reference_image = reference_images[0] if reference_images else None
    reference_images_by_slot = {image.slot: image for image in reference_images}
    reference_image_slots = [
        {"slot": slot, "reference": reference_images_by_slot.get(slot)}
        for slot in (1, 2, 3)
    ]
    workflow_visibility = build_workflow_visibility_context(
        "lead",
        user=request.user,
        lead=lead,
    )

    context = {
        "lead": lead,
        "opportunities": opportunities,
        "customer": customer,
        "comments": comments,
        "tasks": tasks,
        "activities": activities,
        "activity_choices": LeadActivity.ACTIVITY_TYPE_CHOICES,
        "potential_duplicates": potential_duplicates,
        "upcoming_events": upcoming_events,
        "agents": agents,
        "selected_agent": selected_agent,
        "messages": chat_messages,
        "reference_images": reference_images,
        "primary_reference_image": primary_reference_image,
        "product_snapshot": product_snapshot_for_lead(lead, primary_reference_image),
        "reference_image_slots": reference_image_slots,
        # new
        "budget_cad": budget_cad,
        "budget_bdt": budget_bdt,
        "cad_to_bdt": cad_to_bdt,
        "wa_inbox_url": wa_inbox_url,
        "iconic_ai_brain": iconic_ai_brain,
        "can_archive_records": _can_archive_workflow_record(request.user),
        "can_claim_lead": can_claim_sales_lead(request.user) and is_available_sales_lead(lead),
        "can_release_lead": can_release_sales_lead(request.user, lead),
        "can_edit_lead": can_manage_all_sales_records(request.user) or lead.assigned_to_id == request.user.pk,
        "lead_can_hard_delete": not _lead_linked_record_labels(lead),
        "lead_lifecycle_banner": _lead_lifecycle_banner(lead, opportunities),
        **workflow_visibility,
    }

    return render(request, "crm/lead_detail.html", context)


def _render_lead_detail_failsafe(request, lead, error_text: str = ""):
    safe_context = {
        "lead": lead,
        "error_text": error_text if getattr(settings, "DEBUG", False) else "",
    }
    return render(request, "crm/lead_detail_safe.html", safe_context, status=200)


def lead_detail(request, pk):
    try:
        return _lead_detail_impl(request, pk)
    except Http404:
        raise
    except Exception as exc:
        logger.exception("lead_detail hard-fail for lead %s", pk)
        try:
            lead = (
                Lead.objects.only(
                    "pk",
                    "lead_id",
                    "account_brand",
                    "contact_name",
                    "email",
                    "phone",
                    "lead_status",
                    "priority",
                    "country",
                )
                .filter(pk=pk)
                .first()
            )
        except Exception:
            lead = None

        if lead is None:
            lead = SimpleNamespace(
                pk=pk,
                lead_id=str(pk),
                account_brand=f"Lead #{pk}",
                contact_name="",
                email="",
                phone="",
                lead_status="",
                priority="",
                country="",
            )

        try:
            messages.error(
                request,
                "Part of this lead page failed to load. Showing safe view while we recover details.",
            )
        except Exception:
            pass

        return _render_lead_detail_failsafe(request, lead, str(exc))

## ===================================================
# OPPORTUNITY DETAIL PAGE
# ===================================================
from decimal import Decimal
from datetime import datetime

from django.db.models import Sum
from django.shortcuts import get_object_or_404, render, redirect
from django.utils import timezone

from .models import (
    Customer,
    Opportunity,
    Shipment,
    ProductionOrder,
    OpportunityTask,
    OpportunityFile,
    LeadComment,
    LeadActivity,
    AIAgent,

)

from decimal import Decimal
from datetime import datetime
from django.db.models import Sum
from django.shortcuts import get_object_or_404, render, redirect
from django.utils import timezone


def _format_quick_opportunity_money(value, exchange_rate, currency="BDT", is_legacy_currency=True):
    amount = Decimal(value or 0)
    if not is_legacy_currency:
        return format_finance_money(amount, currency)
    bdt = format_finance_money(amount, "BDT")
    if not exchange_rate:
        return f"{bdt} / CAD N/A"
    try:
        cad = convert_currency(amount, "BDT", "CAD", bdt_per_cad=exchange_rate)
    except CurrencyConversionError:
        return f"{bdt} / CAD N/A"
    return f"{bdt} / {format_finance_money(cad, 'CAD')}"


def _format_quick_opportunity_money_lines(value, exchange_rate, currency="BDT", is_legacy_currency=True):
    amount = Decimal(value or 0)
    if not is_legacy_currency:
        return {
            "bdt": _format_quick_opportunity_money(amount, exchange_rate, currency, False),
            "cad": "",
        }
    bdt = format_finance_money(amount, "BDT")
    if not exchange_rate:
        return {"bdt": bdt, "cad": "CAD N/A"}
    try:
        cad = convert_currency(amount, "BDT", "CAD", bdt_per_cad=exchange_rate)
    except CurrencyConversionError:
        return {"bdt": bdt, "cad": "CAD N/A"}
    return {"bdt": bdt, "cad": format_finance_money(cad, "CAD")}


def _format_quick_opportunity_percent(value):
    if value is None:
        return "0.00%"
    return f"{Decimal(value).quantize(Decimal('0.01'))}%"


def _quick_costing_opportunity_row(quick_costing):
    summary = quick_costing.calculation_summary()
    exchange_rate = summary.get("exchange_rate")
    currency = summary["currency"]
    is_legacy_currency = summary["is_legacy_currency"]
    cost_available = (summary.get("total_cost") or Decimal("0")) > Decimal("0")
    return {
        "record": quick_costing,
        "number": f"QC-{quick_costing.pk}",
        "costing_type": "Quick Costing",
        "purpose": quick_costing.get_costing_purpose_display(),
        "purpose_label": quick_costing.purpose_label,
        "purpose_key": quick_costing.costing_purpose,
        "date": quick_costing.created_at,
        "quantity": summary["quantity"],
        "total_cost": _format_quick_opportunity_money(summary["total_cost"], exchange_rate, currency, is_legacy_currency),
        "total_cost_lines": _format_quick_opportunity_money_lines(summary["total_cost"], exchange_rate, currency, is_legacy_currency),
        "revenue": _format_quick_opportunity_money(summary["revenue"], exchange_rate, currency, is_legacy_currency),
        "revenue_lines": _format_quick_opportunity_money_lines(summary["revenue"], exchange_rate, currency, is_legacy_currency),
        "net_profit": _format_quick_opportunity_money(summary["net_profit_total"], exchange_rate, currency, is_legacy_currency),
        "net_profit_lines": _format_quick_opportunity_money_lines(summary["net_profit_total"], exchange_rate, currency, is_legacy_currency),
        "margin_percent": (
            _format_quick_opportunity_percent(summary["net_profit_margin_percent"])
            if cost_available
            else "Margin N/A"
        ),
        "status": quick_costing.get_status_display(),
        "target_margin_percent": (
            _format_quick_opportunity_percent(summary["target_margin_percent"])
            if summary["target_margin_percent"] is not None
            else "N/A"
        ),
        "margin_status": summary["margin_status"] if cost_available else "Cost unavailable",
    }


def _preferred_quick_costing_opportunity_row(rows):
    if not rows:
        return None
    approved = [
        row for row in rows
        if getattr(row.get("record"), "status", "") in QuickCosting.ACTIVE_APPROVED_STATUSES
    ]
    approved.sort(
        key=lambda row: (
            getattr(row.get("record"), "revision_number", 1) or 1,
            getattr(row.get("record"), "approved_at", None) or getattr(row.get("record"), "created_at", None),
            getattr(row.get("record"), "pk", 0) or 0,
        ),
        reverse=True,
    )
    return approved[0] if approved else None


def _opportunity_costing_status(advanced_count, quick_count):
    total = advanced_count + quick_count
    if total == 0:
        return "No Costing"
    if total > 1:
        return "Multiple Costings"
    if advanced_count:
        return "Advanced Costing"
    return "Quick Costing"


def opportunity_detail(request, pk):
    opportunity = get_object_or_404(
        scope_sales_opportunities(
            _with_opportunity_kpi_value(Opportunity.objects.select_related("lead", "lead__assigned_to", "customer", "assigned_to")),
            request.user,
        ),
        pk=pk,
    )
    lead = opportunity.lead
    can_view_internal_financials = can_view_lifecycle_profit(request.user)

    customer_param = (request.GET.get("customer") or "").strip()
    if customer_param and opportunity.customer_id and str(opportunity.customer_id) != customer_param:
        raise Http404("Opportunity does not belong to this customer.")

    # Customer for this opportunity
    customer = opportunity.customer or (lead.customer if lead and lead.customer_id else None)

    # Tasks
    opp_tasks = OpportunityTask.objects.filter(opportunity=opportunity).order_by(
        "status", "due_date", "-created_at"
    )

    # Files
    opp_files = OpportunityFile.objects.filter(opportunity=opportunity).order_by("-uploaded_at")
    if can_view_internal_financials:
        opportunity_documents = OpportunityDocument.objects.filter(
            opportunity=opportunity,
            doc_type__in=["costing_pdf", "costing_excel", "costing_other"],
        ).order_by("-uploaded_at")
    else:
        opportunity_documents = OpportunityDocument.objects.none()
    active_cost_sheet = CostSheet.objects.filter(opportunity=opportunity, is_active=True).order_by(
        "-updated_at", "-id"
    ).first()
    costing_header = CostingHeader.objects.filter(opportunity=opportunity).order_by(
        "-updated_at", "-id"
    ).first()
    quick_costings = list(
        QuickCosting.objects.filter(opportunity=opportunity)
        .select_related("created_by", "previous_revision", "superseded_by", "revision_root")
        .order_by("-updated_at", "-id")
    )
    quick_costing_rows = [
        _quick_costing_opportunity_row(quick_costing)
        for quick_costing in quick_costings
    ] if can_view_internal_financials else []
    latest_quick_costing_row = quick_costing_rows[0] if quick_costing_rows else None
    reporting_quick_costing_row = _preferred_quick_costing_opportunity_row(quick_costing_rows)
    advanced_costing_count = CostingHeader.objects.filter(opportunity=opportunity).count()
    quick_costing_count = len(quick_costings)
    opportunity_costing_status = _opportunity_costing_status(
        advanced_costing_count,
        quick_costing_count,
    )
    opportunity_can_hard_delete = not _opportunity_linked_record_labels(opportunity)
    task_assignee_options = [
        {
            "value": _user_display_name(user),
            "label": _user_display_name(user),
        }
        for user in _active_crm_user_options()
    ]

    # Comments and activity
    comments = _chatter_for_opportunity(opportunity, request.user)

    activities = LeadActivity.objects.filter(lead=lead).order_by("-created_at") if lead else LeadActivity.objects.none()

    stage_choices = Opportunity.STAGE_CHOICES

    agents = AIAgent.objects.all()
    selected_agent = None
    ai_messages_qs = []

    # Shipments list and totals for this opportunity
    shipments = (
        opportunity.shipments.all()
        .select_related("order", "customer")
        .order_by("-ship_date", "-created_at")
    )

    shipping_cost_bdt = Decimal("0")
    shipping_cost_cad = Decimal("0")
    if can_view_internal_financials:
        for s in shipments:
            shipping_cost_bdt += s.cost_bdt or Decimal("0")
            shipping_cost_cad += s.cost_cad or Decimal("0")

    # Helper: shipping values for template
    ship = {
        "name": (customer.shipping_name if customer else "") or "",
        "address1": (customer.shipping_address1 if customer else "") or "",
        "address2": (customer.shipping_address2 if customer else "") or "",
        "city": (customer.shipping_city if customer else "") or "",
        "state": (customer.shipping_state if customer else "") or "",
        "post_code": (customer.shipping_postcode if customer else "") or "",
        "country": (customer.shipping_country if customer else "") or "",
    }

    def ship_has_any_data():
        return any((v or "").strip() for v in ship.values())

    # Handle post actions
    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()

        # Create task
        if action == "add_opp_task":
            title = (request.POST.get("task_title") or "").strip()
            due_str = (request.POST.get("task_due_date") or "").strip()
            priority = (request.POST.get("task_priority") or "Medium").strip()
            assigned_to = (request.POST.get("task_assigned_to") or "").strip()
            description = (request.POST.get("task_description") or "").strip()

            if title:
                due_date = None
                if due_str:
                    try:
                        due_date = datetime.fromisoformat(due_str).date()
                    except ValueError:
                        due_date = None

                OpportunityTask.objects.create(
                    opportunity=opportunity,
                    title=title,
                    description=description,
                    due_date=due_date,
                    priority=priority,
                    assigned_to=assigned_to,
                )

                if lead:
                    LeadActivity.objects.create(
                        lead=lead,
                        activity_type="task_created",
                        description=f"Opportunity task created: {title}",
                    )

            return redirect("opportunity_detail", pk=opportunity.pk)

        # Complete task
        if action == "complete_opp_task":
            task_id = (request.POST.get("task_id") or "").strip()
            if task_id:
                t = OpportunityTask.objects.filter(id=task_id, opportunity=opportunity).first()
                if t:
                    t.status = "Done"
                    t.completed_at = timezone.now()
                    t.save()

                    if lead:
                        LeadActivity.objects.create(
                            lead=lead,
                            activity_type="task_completed",
                            description=f"Opportunity task completed: {t.title}",
                        )

            return redirect("opportunity_detail", pk=opportunity.pk)

        # Save shipping address (SAVE TO CUSTOMER ONLY)
        if action == "save_shipping":
            # IMPORTANT: these names must match your HTML input names
            shipping_name = (request.POST.get("shipping_name") or "").strip()
            shipping_address1 = (request.POST.get("shipping_address1") or "").strip()
            shipping_address2 = (request.POST.get("shipping_address2") or "").strip()
            shipping_city = (request.POST.get("shipping_city") or "").strip()
            shipping_state = (request.POST.get("shipping_state") or "").strip()
            shipping_postcode = (request.POST.get("shipping_postcode") or "").strip()
            shipping_country = (request.POST.get("shipping_country") or "").strip()

            customer = _ensure_customer_for_opportunity(opportunity)

            if customer:
                customer.shipping_name = shipping_name
                customer.shipping_address1 = shipping_address1
                customer.shipping_address2 = shipping_address2
                customer.shipping_city = shipping_city
                customer.shipping_state = shipping_state
                customer.shipping_postcode = shipping_postcode
                customer.shipping_country = shipping_country
                customer.save()

                if lead:
                    LeadActivity.objects.create(
                        lead=lead,
                        activity_type="shipping_updated",
                        description="Shipping address updated from opportunity page.",
                    )

            return redirect("opportunity_detail", pk=opportunity.pk)

        # Add comment
        if action == "add_comment":
            if not can_access_chatter_record(request.user, "opportunities", opportunity):
                return HttpResponseForbidden("You do not have access to this opportunity's chatter.")
            comment_text = (request.POST.get("comment_text") or "").strip()
            attachment = request.FILES.get("attachment")
            if not comment_text and not attachment:
                messages.error(request, "Please write a note or attach a file first.")
            elif lead:
                author_name = employee_display_name(request.user)
                content = comment_text or f"Attachment: {attachment.name}"
                comment = LeadComment.objects.create(
                    lead=lead,
                    opportunity=opportunity,
                    author=author_name,
                    author_user=request.user,
                    content=content,
                    attachment=attachment,
                )
                _send_chatter_mentions(request, comment)
                LeadActivity.objects.create(
                    lead=lead,
                    activity_type="note_added",
                    description=f"Opportunity note: {content[:200]}",
                )

            return redirect("opportunity_detail", pk=opportunity.pk)

        # Toggle pin comment
        if action == "toggle_pin_comment":
            if not can_access_chatter_record(request.user, "opportunities", opportunity):
                return HttpResponseForbidden("You do not have access to this opportunity's chatter.")
            comment_id = (request.POST.get("comment_id") or "").strip()
            if comment_id and lead:
                c = LeadComment.objects.filter(
                    Q(id=comment_id, opportunity=opportunity)
                    | Q(id=comment_id, production__opportunity=opportunity)
                    | Q(id=comment_id, lead=lead, opportunity__isnull=True, production__isnull=True)
                ).first()
                if c:
                    c.pinned = not c.pinned
                    c.save()

            return redirect("opportunity_detail", pk=opportunity.pk)

        # Update stage
        if action == "update_stage":
            old_stage = opportunity.stage
            new_stage = (request.POST.get("stage") or "").strip()
            next_followup_str = (request.POST.get("next_followup") or "").strip()

            stage_values = [s[0] for s in Opportunity.STAGE_CHOICES]
            if new_stage in stage_values:
                opportunity.stage = new_stage

            if next_followup_str:
                try:
                    opportunity.next_followup = datetime.fromisoformat(next_followup_str).date()
                except ValueError:
                    pass

            opportunity.is_open = request.POST.get("is_open") == "on"
            opportunity.save()

            if lead:
                LeadActivity.objects.create(
                    lead=lead,
                    activity_type="stage_updated",
                    description=f"Opportunity stage changed to {opportunity.stage}",
                )

            if lead and old_stage != "Production" and opportunity.stage == "Production":
                has_po = ProductionOrder.objects.filter(opportunity=opportunity).exists()
                if not has_po:
                    po_title = f"{lead.account_brand} production for {opportunity.opportunity_id}"
                    qty_guess = opportunity.moq_units
                    if not qty_guess and lead.order_quantity:
                        raw_qty = str(lead.order_quantity or "").strip()
                        qty_match = re.search(r"\d+(?:\.\d+)?", raw_qty.replace(",", ""))
                        if qty_match:
                            qty_guess = int(Decimal(qty_match.group(0)))
                        else:
                            message = (
                                "Production order was not created because the lead order "
                                f"quantity is invalid: {raw_qty}."
                            )
                            messages.error(request, message)
                            LeadActivity.objects.create(
                                lead=lead,
                                activity_type="production_error",
                                description=message,
                            )
                            return redirect("opportunity_detail", pk=opportunity.pk)
                    qty_guess = qty_guess or 0
                    try:
                        po = ProductionOrder.objects.create(
                            opportunity=opportunity,
                            title=po_title,
                            qty_total=qty_guess,
                            cost_sheet_active=active_cost_sheet,
                            costing_header=costing_header if costing_header and costing_header.status == "approved" else None,
                        )
                        link_reference_images_to_production(opportunity=opportunity, production_order=po)
                        LeadActivity.objects.create(
                            lead=lead,
                            activity_type="production_created",
                            description="Auto work order created from opportunity stage set to Production.",
                        )
                    except Exception:
                        LeadActivity.objects.create(
                            lead=lead,
                            activity_type="production_error",
                            description="Tried to auto create work order but model fields need check.",
                        )

            return redirect("opportunity_detail", pk=opportunity.pk)

        # Upload file
        if action == "upload_file":
            file_obj = request.FILES.get("file_obj")
            if file_obj and lead:
                uploaded_by = request.user.username if request.user.is_authenticated else "User"
                OpportunityFile.objects.create(
                    opportunity=opportunity,
                    original_name=file_obj.name,
                    uploaded_by=uploaded_by,
                    file=file_obj,
                )
                LeadActivity.objects.create(
                    lead=lead,
                    activity_type="file_uploaded",
                    description=f"File uploaded for opportunity: {file_obj.name}",
                )

            return redirect("opportunity_detail", pk=opportunity.pk)

        # Upload document (costing)
        if action == "upload_document":
            file_obj = request.FILES.get("doc_file")
            doc_type = (request.POST.get("doc_type") or "other").strip()

            if file_obj and lead:
                doc = OpportunityDocument.objects.create(
                    opportunity=opportunity,
                    file=file_obj,
                    original_name=file_obj.name,
                    doc_type=doc_type,
                    costing_header=costing_header,
                    uploaded_by=request.user if request.user.is_authenticated else None,
                )
                LeadActivity.objects.create(
                    lead=lead,
                    activity_type="file_uploaded",
                    description=f"Document uploaded for opportunity: {doc.original_name}",
                )
                messages.success(request, "Document uploaded.")
            else:
                messages.error(request, "Please select a file to upload.")

            return redirect("opportunity_detail", pk=opportunity.pk)

        # Delete file
        if action == "delete_file":
            file_id = (request.POST.get("file_id") or "").strip()
            f = OpportunityFile.objects.filter(id=file_id, opportunity=opportunity).first()
            if f and lead:
                file_name = f.original_name or f.file.name
                f.file.delete(save=False)
                f.delete()
                LeadActivity.objects.create(
                    lead=lead,
                    activity_type="file_deleted",
                    description=f"File deleted for opportunity: {file_name}",
                )

            return redirect("opportunity_detail", pk=opportunity.pk)

        # AI quick
        if action == "ai_quick":
            if agents:
                selected_agent = agents.first()
            return redirect("opportunity_detail", pk=opportunity.pk)

    # AI messages on GET
    if request.method != "POST":
        if agents and lead:
            selected_agent = agents.first()
            conversation = (
                AIConversation.objects.filter(
                    agent=selected_agent,
                    lead=lead,
                    opportunity=opportunity,
                )
                .order_by("-created_at")
                .first()
            )
            if conversation:
                ai_messages_qs = conversation.messages.order_by("created_at")

    # Production totals
    prod_orders_qs = ProductionOrder.objects.filter(opportunity=opportunity)
    prod_totals = prod_orders_qs.aggregate(
        total_qty=Sum("qty_total"),
        total_reject=Sum("qty_reject"),
    )

    prod_total_qty = prod_totals.get("total_qty") or 0
    prod_total_reject = prod_totals.get("total_reject") or 0
    prod_total_actual_cost = None
    if can_view_internal_financials:
        prod_total_actual_cost = (
            prod_orders_qs.aggregate(total_actual_cost=Sum("actual_total_cost_bdt")).get("total_actual_cost") or 0
        )

    costing_calc = compute_costing(costing_header.id) if costing_header and can_view_internal_financials else None
    variance_placeholder = None
    variance_display = None
    if costing_calc:
        latest_po = prod_orders_qs.order_by("-created_at", "-id").first()
        actual_cost_per_piece = None
        produced_qty = 0
        if latest_po and latest_po.actual_cost_per_piece_bdt is not None:
            actual_cost_per_piece = latest_po.actual_cost_per_piece_bdt
            produced_qty = latest_po.qty_total or 0

        standard_cost = costing_calc["total_cost_per_piece"] if costing_calc else Decimal("0")
        variance_per_piece = None
        total_variance = None
        if actual_cost_per_piece is not None:
            variance_per_piece = actual_cost_per_piece - standard_cost
            if produced_qty:
                total_variance = variance_per_piece * Decimal(produced_qty)

        variance_placeholder = {
            "standard_cost_per_piece": standard_cost,
            "actual_cost_per_piece": actual_cost_per_piece,
            "variance_per_piece": variance_per_piece,
            "total_variance": total_variance,
        }
        if variance_placeholder:
            def _fmt(value):
                if value is None:
                    return None
                return Decimal(value).quantize(Decimal("0.01"))

            variance_display = {
                "standard_cost_per_piece": _fmt(standard_cost),
                "actual_cost_per_piece": _fmt(actual_cost_per_piece),
                "variance_per_piece": _fmt(variance_per_piece),
                "total_variance": _fmt(total_variance),
            }

    prod_orders = list(prod_orders_qs.order_by("-created_at", "-id"))

    order_value = opportunity.order_value or 0
    total_cost_bdt = (prod_total_actual_cost or 0) + (shipping_cost_bdt or 0) if can_view_internal_financials else None

    profit_after_shipping = None
    profit_after_shipping_percent = None
    if can_view_internal_financials and order_value:
        profit_after_shipping = order_value - total_cost_bdt
        profit_after_shipping_percent = (profit_after_shipping / order_value) * 100

    order_value_usd = opportunity.order_value_usd
    fx_rate = opportunity.fx_rate_bdt_per_usd
    order_value_bdt = opportunity.order_value
    currency_summary = _opportunity_currency_summary(opportunity)
    bdt_per_piece = currency_summary["bdt_per_piece"]

    reference_images = list(reference_images_for_opportunity(opportunity))
    primary_reference_image = reference_images[0] if reference_images else None
    workflow_visibility = build_workflow_visibility_context(
        "opportunity",
        user=request.user,
        lead=lead,
        opportunity=opportunity,
        costing=costing_header,
    )
    if reporting_quick_costing_row and workflow_visibility.get("workflow_order_summary"):
        workflow_order_summary = dict(workflow_visibility["workflow_order_summary"])
        workflow_order_summary.update(
            {
                "value": "",
                "value_label": "Sales value",
                "value_lines": reporting_quick_costing_row["revenue_lines"],
                "costing_purpose": reporting_quick_costing_row["purpose_label"],
                "costing_purpose_key": reporting_quick_costing_row["purpose_key"],
                "costing_reference": reporting_quick_costing_row["number"],
            }
        )
        workflow_visibility["workflow_order_summary"] = workflow_order_summary

    context = {
        "opportunity": opportunity,
        "lead": lead,
        "customer": customer,
        "reference_images": reference_images,
        "primary_reference_image": primary_reference_image,
        "product_snapshot": product_snapshot_for_opportunity(opportunity, primary_reference_image),

        "opp_tasks": opp_tasks,
        "comments": comments,
        "activities": activities,
        "stage_choices": stage_choices,

        "agents": agents,
        "selected_agent": selected_agent,
        "messages": ai_messages_qs,

        "opp_files": opp_files,
        "opportunity_documents": opportunity_documents,
        "costing_header": costing_header,
        "costing_calc": costing_calc,
        "advanced_costing_count": advanced_costing_count,
        "quick_costing_count": quick_costing_count,
        "quick_costing_rows": quick_costing_rows,
        "latest_quick_costing_row": latest_quick_costing_row,
        "opportunity_costing_status": opportunity_costing_status,
        "task_assignee_options": task_assignee_options,
        "can_archive_records": _can_archive_workflow_record(request.user),
        "opportunity_can_hard_delete": opportunity_can_hard_delete,
        "opportunity_lifecycle_banner": _opportunity_lifecycle_banner(prod_orders),
        "variance_placeholder": variance_placeholder,
        "variance_display": variance_display,

        "prod_orders": prod_orders,
        "prod_total_qty": prod_total_qty,
        "prod_total_reject": prod_total_reject,
        "prod_total_actual_cost": prod_total_actual_cost,

        "shipments": shipments,
        "shipping_cost_bdt": shipping_cost_bdt,
        "shipping_cost_cad": shipping_cost_cad,
        "total_cost_bdt": total_cost_bdt,

        "profit_after_shipping": profit_after_shipping,
        "profit_after_shipping_percent": profit_after_shipping_percent,
        "can_view_internal_financials": can_view_internal_financials,

        "order_value_usd": order_value_usd,
        "fx_rate_bdt_per_usd": fx_rate,
        "order_value_bdt": order_value_bdt,
        "bdt_per_piece": bdt_per_piece,
        "currency_summary": currency_summary,

        # Shipping for the template
        "ship": ship,
        "ship_locked": ship_has_any_data(),
        **workflow_visibility,
    }

    return render(request, "crm/opportunity_detail.html", context)

# CUSTOMERS AND CUSTOMER AI
# ===================================================

@require_POST
def customer_ai_detail(request, pk):
    """
    AI brain for a single customer.
    Returns JSON with short clear text.
    Also appends answer into customer.notes.
    """
    customer = get_object_or_404(Customer, pk=pk)
    leads = customer.leads.all().order_by("created_date", "id")
    lead = leads.last() if leads.exists() else None

    mode = request.POST.get("mode", "overview")
    user_question = request.POST.get("question", "").strip()
    email_purpose = request.POST.get("email_purpose", "").strip()
    email_tone = request.POST.get("email_tone", "").strip()

    base_info = []
    base_info.append(f"Customer code: {customer.customer_code}")
    base_info.append(f"Brand: {customer.account_brand}")
    base_info.append(f"Contact: {customer.contact_name}")
    base_info.append(f"Email: {customer.email}")
    base_info.append(f"Phone: {customer.phone}")
    base_info.append(f"Market: {customer.market}")
    base_info.append(
        "Shipping: "
        f"{customer.shipping_name or ''}, "
        f"{customer.shipping_address1 or ''} "
        f"{customer.shipping_city or ''} "
        f"{customer.shipping_country or ''}"
    )
    base_info.append(f"Active: {'yes' if customer.is_active else 'no'}")

    opps = customer.opportunities.all().order_by("created_date", "id")
    total_value = opps.aggregate(s=Sum("order_value"))["s"] or 0
    order_count = opps.count()
    open_count = opps.exclude(stage__in=["Closed Won", "Closed Lost", "Production", "Shipment Complete"]).count()
    won_count = opps.filter(stage="Closed Won").count()

    base_info.append(f"Total opportunities: {order_count}")
    base_info.append(f"Open opportunities: {open_count}")
    base_info.append(f"Closed won: {won_count}")
    base_info.append(f"Total order value: {total_value}")

    if opps:
        last_opp = opps.last()
        base_info.append(
            f"Latest stage: {last_opp.stage} "
            f"on {last_opp.created_date or 'unknown'} "
            f"product type {last_opp.product_type} "
            f"category {last_opp.product_category}"
        )

    activities = LeadActivity.objects.filter(lead__in=leads).order_by("-created_at")[:10] if lead else []
    if activities:
        base_info.append("Recent activities:")
        for a in activities:
            base_info.append(
                f"- {a.created_at.date()} {a.get_activity_type_display()}: "
                f"{(a.description or '')[:100]}"
            )

    comments = LeadComment.objects.filter(lead__in=leads).order_by("-created_at")[:5] if lead else []
    if comments:
        base_info.append("Recent notes:")
        for c in comments:
            base_info.append(
                f"- {c.created_at.date()} by {c.author}: {(c.content or '')[:120]}"
            )

    context_text = "\n".join(base_info)

    if mode == "overview":
        task = (
            "Give a short clear overview of this customer. "
            "Explain what they buy, how active they are, and what stage they are in. "
            "Keep it under 10 lines."
        )
    elif mode == "followup":
        task = (
            "Suggest the best next follow up. "
            "Give a clear time frame and what to talk about. "
            "Keep it under 8 lines."
        )
    elif mode == "order_size":
        task = (
            "Predict a realistic next order size in units. "
            "Use past orders and stages. "
            "Explain in 3 to 6 lines."
        )
    elif mode == "product_ideas":
        task = (
            "Suggest 3 to 6 product ideas to pitch next. "
            "Use the product types, categories, and market. "
            "Keep it short with bullets."
        )
    elif mode == "email_followup":
        task = (
            "Write a short professional email to this customer. "
            "Goal: "
            + (email_purpose or "warm follow up and keep the order moving")
            + ". Tone: "
            + (email_tone or "friendly and clear")
            + ". "
            "Keep it under 180 words."
        )
    elif mode == "risk_score":
        task = (
            "Give a risk and potential view. "
            "Score risk from 1 to 10 and potential from 1 to 10. "
            "Explain why in 5 to 8 lines. "
            "Say if we should invest more time or keep light contact."
        )
    elif mode == "full_summary":
        task = (
            "Create a full summary of this customer for a new account manager. "
            "Include history, order pattern, best products, risk, and next steps. "
            "Keep it under 18 lines, simple English."
        )
    elif mode == "custom" and user_question:
        task = (
            "Answer this custom question about the customer: "
            + user_question
            + ". Keep it short and clear."
        )
    else:
        task = (
            "Give a short clear overview of this customer with next steps. "
            "Keep it under 12 lines."
        )

    messages_for_model = [
        {
            "role": "system",
            "content": (
                "You are a CRM assistant for Iconic Apparel House. "
                "You help with sales and production planning. "
                "Use simple English. Be clear and practical. "
                "Do not invent wild numbers. "
                "If you guess, say it is an estimate."
            ),
        },
        {
            "role": "user",
            "content": (
                task
                + "\n\nHere is the customer data from the CRM:\n\n"
                + context_text
            ),
        },
    ]

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages_for_model,
        )
        ai_text = resp.choices[0].message.content.strip()

        timestamp = timezone.now().strftime("%Y-%m-%d %H:%M")
        header = f"\n\n[AI {mode} {timestamp}]\n"
        customer.notes = (customer.notes or "") + header + ai_text
        customer.save(update_fields=["notes"])

        return JsonResponse({"ok": True, "text": ai_text})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)})


def customers_list(request):
    q = (request.GET.get("q") or "").strip()
    has_active = (request.GET.get("has_active") or "").strip() == "1"
    has_production = (request.GET.get("has_production") or "").strip() == "1"
    has_completed = (request.GET.get("has_completed") or "").strip() == "1"
    country = (request.GET.get("country") or "").strip()
    status = (request.GET.get("status") or "").strip().lower()
    sort = (request.GET.get("sort") or "recent").strip().lower()
    archive_filter = (request.GET.get("archive") or "active").strip().lower()

    completed_statuses = _production_completed_statuses()
    active_prod_statuses = _production_active_statuses()

    qs = Customer.objects.all()
    if archive_filter == "archived":
        qs = qs.filter(is_archived=True)
    elif archive_filter != "all":
        qs = qs.filter(is_archived=False)
    countries = (
        Customer.objects
        .exclude(country="")
        .order_by("country")
        .values_list("country", flat=True)
        .distinct()
    )

    if q:
        qs = qs.filter(
            Q(account_brand__icontains=q)
            | Q(contact_name__icontains=q)
            | Q(email__icontains=q)
            | Q(phone__icontains=q)
            | Q(country__icontains=q)
        )
    if country:
        qs = qs.filter(country__iexact=country)
    if status == "active":
        qs = qs.filter(is_active=True)
    elif status == "inactive":
        qs = qs.filter(is_active=False)

    qs = qs.annotate(
        total_opps=Count("opportunities", distinct=True),
        active_opps=Count(
            "opportunities",
            filter=(
                Q(opportunities__is_open=True, opportunities__is_archived=False)
                & ~Q(opportunities__stage__in=tuple(CLOSED_PIPELINE_STAGES) + ("Production", "Shipment Complete"))
            ),
            distinct=True,
        ),
        production_active=Count(
            "production_orders",
            filter=Q(production_orders__status__in=active_prod_statuses),
            distinct=True,
        ),
        production_completed=Count(
            "production_orders",
            filter=Q(production_orders__status__in=completed_statuses),
            distinct=True,
        ),
        last_opp_date=Max("opportunities__updated_at"),
        last_prod_date=Max("production_orders__updated_at"),
        last_lead_date=Max("leads__created_date"),
    )

    if has_active:
        qs = qs.filter(active_opps__gt=0)
    if has_production:
        qs = qs.filter(production_active__gt=0)
    if has_completed:
        qs = qs.filter(production_completed__gt=0)

    customers = list(qs)
    customer_ids = [customer.pk for customer in customers]
    customer_revenue_map = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    if customer_ids:
        revenue_rows = (
            AccountingEntry.objects.exclude(main_type="TRANSFER")
            .exclude(status__iexact="CANCELLED")
            .filter(
                direction=AccountingEntry.DIR_IN,
                main_type="INCOME",
            )
            .filter(Q(customer_id__in=customer_ids) | Q(production_order__customer_id__in=customer_ids))
            .annotate(revenue_customer_id=Coalesce("customer_id", "production_order__customer_id"))
            .values("revenue_customer_id", "currency")
            .annotate(amount=Sum("amount_original"))
        )
        for row in revenue_rows:
            code = (row.get("currency") or "CAD").upper()
            amount = _ceo_decimal(row.get("amount"))
            if amount:
                customer_revenue_map[row["revenue_customer_id"]][code] += amount
    summary_revenue_totals = defaultdict(lambda: {"amount": Decimal("0")})

    def _to_date(value):
        if not value:
            return None
        if isinstance(value, timezone.datetime):
            return value.date()
        return value

    for c in customers:
        dates = [
            _to_date(c.updated_at),
            _to_date(c.created_date),
            _to_date(getattr(c, "last_opp_date", None)),
            _to_date(getattr(c, "last_prod_date", None)),
            _to_date(getattr(c, "last_lead_date", None)),
        ]
        dates = [d for d in dates if d]
        c.last_activity = max(dates) if dates else None
        c.display_name = c.account_brand or c.contact_name or "Unnamed customer"
        initials_source = c.display_name.replace("/", " ").replace("-", " ").split()
        c.initials = "".join(part[0] for part in initials_source[:2]).upper() or "C"
        c.status_key = "active" if c.is_active else "inactive"
        c.status_label = "Active" if c.is_active else "Inactive"
        c.revenue_rows = currency_summary_rows(
            {
                code: {"amount": amount}
                for code, amount in customer_revenue_map[c.pk].items()
            }
        )
        c.revenue_sort_value = max(
            (row["amount"] for row in c.revenue_rows), default=Decimal("0")
        )
        for row in c.revenue_rows:
            summary_revenue_totals[row["currency"]]["amount"] += row["amount"]

    if sort == "name":
        customers.sort(key=lambda c: ((c.account_brand or "").lower(), (c.contact_name or "").lower()))
    elif sort == "revenue":
        customers.sort(key=lambda c: c.revenue_sort_value, reverse=True)
    else:
        customers.sort(key=lambda c: c.last_activity or date.min, reverse=True)

    shared_pipeline = summarize_pipeline(
        _active_opportunity_list_queryset(_with_opportunity_production_flag(Opportunity.objects.all())),
        apply_open_definition=False,
    )
    local_customer_orders = ProductionOrder.objects.filter(customer_id__in=customer_ids)
    local_sewing_summary = summarize_local_sewing_orders(local_customer_orders)
    summary = {
        "total": len(customers),
        "active": sum(1 for c in customers if c.is_active),
        "with_active_opps": sum(1 for c in customers if (c.active_opps or 0) > 0),
        "pipeline_count": shared_pipeline["count"],
        "pipeline_rows": shared_pipeline["rows"],
        "in_production": sum(1 for c in customers if (c.production_active or 0) > 0),
        "revenue_rows": currency_summary_rows(summary_revenue_totals),
        "local_sewing": local_sewing_summary,
        "can_view_local_sewing_financials": can_view_local_sewing_financials(request.user),
    }

    paginator = Paginator(customers, 25)
    page_obj = paginator.get_page(request.GET.get("page") or 1)
    query_params = request.GET.copy()
    query_params.pop("page", None)

    context = {
        "customers": page_obj.object_list,
        "page_obj": page_obj,
        "summary": summary,
        "countries": countries,
        "q": q,
        "has_active": has_active,
        "has_production": has_production,
        "has_completed": has_completed,
        "country": country,
        "status": status,
        "sort": sort,
        "archive_filter": archive_filter,
        "query_params": query_params.urlencode(),
        "can_archive_customer": _can_archive_customer(request.user),
    }
    return render(request, "crm/customers_list.html", context)


@require_POST
def customer_ai_focus(request):
    """
    AI helper for a single customer
    """
    customer_id = request.POST.get("customer_id")
    mode = request.POST.get("mode", "summary")

    if not customer_id:
        return JsonResponse({"ok": False, "error": "Missing customer id."})

    try:
        customer = Customer.objects.get(id=customer_id)
    except Customer.DoesNotExist:
        return JsonResponse({"ok": False, "error": "Customer not found."})

    lead = customer.leads.order_by("-created_date", "-id").first()
    opps = customer.opportunities.all().order_by("-created_date")

    total_orders = opps.count()
    total_value = (
        opps.aggregate(total=Sum("order_value")).get("total") or Decimal("0.00")
    )

    base_info = (
        f"Customer code: {customer.customer_code}. "
        f"Brand: {customer.account_brand}. "
        f"Contact: {customer.contact_name}. "
        f"Email: {customer.email}. "
        f"Phone: {customer.phone}. "
        f"Market: {customer.market}. "
        f"Total opportunities: {total_orders}. "
        f"Total order value: {total_value}. "
    )

    if lead:
        base_info += (
            f"Lead id: {lead.lead_id}. "
            f"Product interest: {lead.product_interest}. "
            f"Order quantity: {lead.order_quantity}. "
            f"Budget: {lead.budget}. "
        )

    if mode == "next_steps":
        user_prompt = (
            "You are a senior sales advisor at a clothing factory. "
            "Read this customer info and suggest clear next follow up steps "
            "for the sales team. Keep it short and practical, 5 to 8 lines.\n\n"
            f"{base_info}"
        )
    elif mode == "risk":
        user_prompt = (
            "You are a senior account manager at a clothing factory. "
            "Read this customer info and point out any risk of losing this client, "
            "plus how to reduce that risk. Keep it short, 5 to 8 lines.\n\n"
            f"{base_info}"
        )
    elif mode == "growth":
        user_prompt = (
            "You are a growth advisor for a clothing factory. "
            "Read this customer info and suggest how we can grow revenue with this client. "
            "Think about new product types, better service, and repeat orders. "
            "Keep it short, 5 to 8 lines.\n\n"
            f"{base_info}"
        )
    else:
        user_prompt = (
            "Give a short internal summary for this customer for the sales team. "
            "Include who they are, what they buy, order level, and what we should focus on next. "
            "Keep it under 10 lines.\n\n"
            f"{base_info}"
        )

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert clothing factory account manager.",
                },
                {"role": "user", "content": user_prompt},
            ],
        )
        ai_text = resp.choices[0].message.content
        return JsonResponse({"ok": True, "suggestion": ai_text})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)})


def customer_detail(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    can_view_internal_financials = can_view_lifecycle_profit(request.user)
    leads = customer.leads.all().order_by("-created_date", "-id")

    opportunities = _with_opportunity_kpi_value(
        Opportunity.objects.filter(Q(customer=customer) | Q(lead__customer=customer))
        .select_related("lead")
        .order_by("-updated_at", "-id")
        .distinct()
    )

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        if action == "add_note":
            content = (request.POST.get("note_content") or "").strip()
            if content:
                author = request.user.username if request.user.is_authenticated else "User"
                CustomerNote.objects.create(
                    customer=customer,
                    author=author,
                    content=content,
                )
                messages.success(request, "Note added.")
            return redirect("customer_detail", pk=pk)

    active_opps = open_pipeline_queryset(opportunities)

    accounting_revenue_qs = (
        AccountingEntry.objects.exclude(main_type="TRANSFER")
        .exclude(status__iexact="CANCELLED")
        .filter(
            Q(customer=customer) | Q(production_order__customer=customer),
            direction=AccountingEntry.DIR_IN,
            main_type="INCOME",
        )
        .distinct()
    )
    revenue_totals = defaultdict(lambda: {"amount": Decimal("0")})
    total_orders = 0
    for row in accounting_revenue_qs.values("currency").annotate(amount=Sum("amount_original"), count=Count("id")):
        code = (row.get("currency") or "CAD").upper()
        amount = _ceo_decimal(row.get("amount"))
        if amount:
            revenue_totals[code]["amount"] += amount
        total_orders += int(row.get("count") or 0)
    revenue_currency_rows = currency_summary_rows(revenue_totals)

    prod_orders_qs = (
        customer.production_orders
        .select_related("opportunity", "lead")
        .prefetch_related("shipments")
        .order_by("-created_at", "-id")
    )
    local_sewing_summary = summarize_local_sewing_orders(prod_orders_qs)
    prod_orders = list(prod_orders_qs)

    production_active = []
    production_completed = []
    for order in prod_orders:
        operational_status = get_production_operational_status(order)
        order.history_operational_status = operational_status
        order.history_operational_status_label = OPERATIONAL_STATUS_LABELS.get(
            operational_status,
            order.get_status_display(),
        )
        if operational_status in OPERATIONAL_ACTIVE_STATUSES:
            production_active.append(order)
        elif operational_status in OPERATIONAL_FINISHED_STATUSES:
            production_completed.append(order)

    invoice_model = globals().get("Invoice")
    if invoice_model is not None:
        invoices = (
            invoice_model.objects
            .filter(Q(customer=customer) | Q(order__customer=customer))
            .select_related("order", "customer")
            .order_by("-issue_date", "-created_at")
            .distinct()
        )
        unpaid_invoices = invoices.exclude(status__in=["paid", "cancelled"])
        invoice_totals = defaultdict(
            lambda: {"total": Decimal("0"), "paid": Decimal("0"), "outstanding": Decimal("0")}
        )
        for invoice in invoices:
            code = (invoice.currency or "CAD").upper().strip()
            invoice_totals[code]["total"] += _ceo_decimal(invoice.total_amount)
            invoice_totals[code]["paid"] += _ceo_decimal(invoice.paid_amount)
            if invoice.status not in {"paid", "cancelled"}:
                invoice_totals[code]["outstanding"] += _ceo_decimal(invoice.balance)
        invoice_currency_rows = currency_summary_rows(
            invoice_totals, ("total", "paid", "outstanding")
        )
    else:
        invoices = []
        unpaid_invoices = []
        invoice_currency_rows = []

    shipments = (
        Shipment.objects
        .filter(
            Q(customer=customer)
            | Q(order__customer=customer)
            | Q(opportunity__customer=customer)
            | Q(opportunity__lead__customer=customer)
        )
        .select_related("order", "opportunity")
        .order_by("-ship_date", "-created_at")
        .distinct()
    )

    payment_history = (
        InvoicePayment.objects
        .filter(Q(invoice__customer=customer) | Q(invoice__order__customer=customer))
        .select_related("invoice", "production_order")
        .order_by("-payment_date", "-id")
        .distinct()
    )

    profit_estimate = None
    profit_margin = None
    total_cost_bdt = None
    prod_cost_map = {}
    if can_view_internal_financials:
        prod_costs = (
            ProductionOrder.objects
            .filter(opportunity__in=opportunities)
            .values("opportunity_id")
            .annotate(total_cost=Sum("actual_total_cost_bdt"))
        )
        prod_cost_map = {row["opportunity_id"]: (row["total_cost"] or Decimal("0")) for row in prod_costs}

        total_cost_bdt = (
            ProductionOrder.objects
            .filter(customer=customer)
            .aggregate(total_cost=Sum("actual_total_cost_bdt"))
            .get("total_cost") or Decimal("0.00")
        )

        bdt_revenue = next(
            (row["amount"] for row in revenue_currency_rows if row["currency"] == "BDT"),
            Decimal("0"),
        )
        profit_estimate = bdt_revenue - total_cost_bdt if bdt_revenue > 0 and total_cost_bdt > 0 else None
        if bdt_revenue > 0 and total_cost_bdt > 0:
            try:
                profit_margin = (profit_estimate / bdt_revenue) * 100
            except Exception:
                profit_margin = None

    for opp in opportunities:
        cost = prod_cost_map.get(opp.id)
        if can_view_internal_financials and opp.kpi_currency == "BDT" and opp.kpi_order_value and cost is not None and cost > 0:
            try:
                opp.profit_margin_pct = ((opp.kpi_order_value - cost) / opp.kpi_order_value) * 100
            except Exception:
                opp.profit_margin_pct = None
        else:
            opp.profit_margin_pct = None

    notes_list = customer.notes_list.all().order_by("-created_at")
    events = customer.customer_events.all().order_by("-created_at")[:50]

    activity_dates = [
        customer.updated_at.date() if customer.updated_at else None,
        customer.created_date,
    ]
    activity_dates.extend([opp.updated_at.date() for opp in opportunities if opp.updated_at])
    activity_dates.extend([order.updated_at.date() for order in prod_orders if order.updated_at])
    activity_dates.extend([note.created_at.date() for note in notes_list if note.created_at])
    activity_dates.extend([event.created_at.date() for event in events if event.created_at])
    activity_dates.extend([invoice.updated_at.date() for invoice in invoices if getattr(invoice, "updated_at", None)])
    activity_dates = [item for item in activity_dates if item]
    last_activity = max(activity_dates) if activity_dates else None

    display_name = customer.account_brand or customer.contact_name or "Customer"
    initials_source = display_name.replace("/", " ").replace("-", " ").split()
    customer_initials = "".join(part[0] for part in initials_source[:2]).upper() or "C"

    context = {
        "customer": customer,
        "customer_display_name": display_name,
        "customer_initials": customer_initials,
        "customer_status_key": "active" if customer.is_active else "inactive",
        "customer_status_label": "Active" if customer.is_active else "Inactive",
        "leads": leads,
        "opportunities": opportunities,
        "active_opps": active_opps,
        "production_active": production_active,
        "production_completed": production_completed,
        "shipments": shipments,
        "payment_history": payment_history,
        "invoices": invoices,
        "unpaid_invoices": unpaid_invoices,
        "invoice_currency_rows": invoice_currency_rows,
        "revenue_currency_rows": revenue_currency_rows,
        "total_orders": total_orders,
        "total_cost_bdt": total_cost_bdt,
        "profit_estimate": profit_estimate,
        "profit_margin": profit_margin,
        "can_view_internal_financials": can_view_internal_financials,
        "last_activity": last_activity,
        "notes_list": notes_list,
        "events": events,
        "prod_orders": prod_orders,
        "local_sewing_summary": local_sewing_summary,
        "can_view_local_sewing_financials": can_view_local_sewing_financials(request.user),
        "can_archive_customer": _can_archive_customer(request.user),
        "customer_linked_records": _customer_linked_record_labels(customer),
    }
    return render(request, "crm/customer_detail.html", context)


@require_POST
def customer_archive(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    if not _can_archive_customer(request.user):
        messages.error(request, "Only CEO/Admin can archive customers.")
        return redirect("customer_detail", pk=pk)

    linked_labels = _customer_linked_record_labels(customer)
    if not customer.is_archived:
        customer.is_archived = True
        customer.is_active = False
        customer.archived_at = timezone.now()
        customer.archived_by = request.user if request.user.is_authenticated else None
        customer.save(update_fields=["is_archived", "is_active", "archived_at", "archived_by", "updated_at"])
    _log_workflow_safety_action(
        request,
        action="archive",
        record=customer,
        message=f"Customer {customer.customer_code} archived.",
        meta={"linked_records": linked_labels},
    )
    _record_customer_event(
        customer=customer,
        event_type="customer_archived",
        title="Customer archived",
        details=(
            f"Archived by {_user_display_name(request.user)}. "
            f"Linked records preserved: {', '.join(linked_labels) if linked_labels else 'none'}."
        ),
    )
    if linked_labels:
        messages.warning(
            request,
            f"Customer archived. Linked records were preserved: {', '.join(linked_labels)}.",
        )
    else:
        messages.success(request, "Customer archived.")
    return redirect("customers_list")


@require_POST
def customer_ai_overview(request):
    """
    AI helper for the customer list page.
    Gives a short overview of the whole customer base.
    """
    try:
        total_customers = Customer.objects.count()
        active_customers = Customer.objects.filter(is_active=True).count()

        paid_opps = Opportunity.objects.filter(order_value__isnull=False)

        totals = paid_opps.aggregate(
            total_revenue=Sum("order_value"),
            total_orders=Count("id"),
        )

        total_revenue = totals.get("total_revenue") or Decimal("0.00")
        total_orders = totals.get("total_orders") or 0

        prompt = (
            "You are a sales advisor for a clothing factory.\n"
            "Here is the current customer base summary:\n"
            f"Total customers: {total_customers}\n"
            f"Active customers: {active_customers}\n"
            f"Total paid orders: {total_orders}\n"
            f"Total revenue across all customers: {total_revenue}.\n\n"
            "Give clear and practical advice on:\n"
            "1. Which type of customers the team should focus on first.\n"
            "2. What follow up rhythm to keep with current customers.\n"
            "3. One or two ideas to grow repeat orders.\n"
            "Keep it short, friendly, and written as bullet style tips."
        )

        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a practical sales coach."},
                {"role": "user", "content": prompt},
            ],
        )

        ai_text = resp.choices[0].message.content
        return JsonResponse({"ok": True, "suggestion": ai_text})

    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)})


@require_POST
def customer_ai_insight(request, pk):
    """
    AI helper for a single customer detail page.
    Gives account summary and next steps.
    """
    customer = get_object_or_404(Customer, pk=pk)
    lead = customer.leads.order_by("-created_date", "-id").first()

    try:
        paid_opps = customer.opportunities.filter(order_value__isnull=False).order_by(
            "-created_date"
        )

        totals = paid_opps.aggregate(
            total_revenue=Sum("order_value"),
            total_orders=Count("id"),
        )

        total_revenue = totals.get("total_revenue") or Decimal("0.00")
        total_orders = totals.get("total_orders") or 0

        last_order_date = paid_opps[0].created_date if paid_opps.exists() else None

        product_stats = (
            paid_opps
            .values("product_type")
            .annotate(num=Count("id"))
            .order_by("-num")
        )
        top_types = ", ".join(
            f"{p['product_type']} ({p['num']} orders)" for p in product_stats[:3]
        ) or "no paid orders yet"

        info_text = (
            f"Customer name: {customer.account_brand}. "
            f"Contact: {customer.contact_name}. "
            f"Email: {customer.email}. "
            f"Phone: {customer.phone}. "
            f"Market: {customer.market}. "
            f"Total paid orders: {total_orders}. "
            f"Total revenue: {total_revenue}. "
        )

        if last_order_date:
            info_text += f"Last order date: {last_order_date}. "

        info_text += f"Top product types by count: {top_types}."

        prompt = (
            "You are an account manager for a clothing factory.\n"
            "Based on the customer account data below, write:\n"
            "1. A very short summary of this customer.\n"
            "2. Three clear follow up steps the team should take next.\n"
            "3. One idea for future collection or product direction for them.\n"
            "Keep it short and practical.\n\n"
            f"Customer account data: {info_text}"
        )

        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful account manager."},
                {"role": "user", "content": prompt},
            ],
        )

        ai_text = resp.choices[0].message.content
        return JsonResponse({"ok": True, "suggestion": ai_text})

    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)})


# ===================================================
# PRODUCT LIBRARY AND AI
# ===================================================

def products_list(request):
    qs = Product.objects.all().order_by("-created_at")

    q = request.GET.get("q") or ""
    product_type = request.GET.get("product_type") or ""
    product_category = request.GET.get("product_category") or ""

    if q:
        qs = qs.filter(name__icontains=q)

    if product_type:
        qs = qs.filter(product_type=product_type)

    if product_category:
        qs = qs.filter(product_category=product_category)

    context = {
        "products": qs,
        "q": q,
        "product_type": product_type,
        "product_category": product_category,
        "type_choices": Opportunity.PRODUCT_TYPE_CHOICES,
        "category_choices": Opportunity.PRODUCT_CATEGORY_CHOICES,
    }
    return render(request, "crm/products_list.html", context)


def product_add(request):
    if request.method == "POST":
        form = ProductForm(request.POST, request.FILES)
        if form.is_valid():
            product = form.save()

            if product.product_type:
                ProductTypeMaster.objects.get_or_create(
                    name=product.product_type.strip(),
                    defaults={"is_active": True},
                )
            if product.product_category:
                ProductCategoryMaster.objects.get_or_create(
                    name=product.product_category.strip(),
                    defaults={"is_active": True},
                )
            if product.default_fabric:
                FabricNameMaster.objects.get_or_create(
                    name=product.default_fabric.strip(),
                    defaults={"is_active": True},
                )
            if product.default_gsm:
                GSMRangeMaster.objects.get_or_create(
                    name=product.default_gsm.strip(),
                    defaults={"is_active": True},
                )

            return redirect("product_detail", pk=product.pk)
    else:
        form = ProductForm()

    type_master = ProductTypeMaster.objects.filter(is_active=True).order_by("name")
    category_master = ProductCategoryMaster.objects.filter(is_active=True).order_by("name")
    fabric_master = FabricNameMaster.objects.filter(is_active=True).order_by("name")
    gsm_master = GSMRangeMaster.objects.filter(is_active=True).order_by("name")

    context = {
        "form": form,
        "mode": "add",
        "type_master": type_master,
        "category_master": category_master,
        "fabric_master": fabric_master,
        "gsm_master": gsm_master,
    }
    return render(request, "crm/product_form.html", context)


def product_edit(request, pk):
    product = get_object_or_404(Product, pk=pk)

    if request.method == "POST":
        form = ProductForm(request.POST, request.FILES, instance=product)
        if form.is_valid():
            product = form.save()

            if product.product_type:
                ProductTypeMaster.objects.get_or_create(
                    name=product.product_type.strip(),
                    defaults={"is_active": True},
                )
            if product.product_category:
                ProductCategoryMaster.objects.get_or_create(
                    name=product.product_category.strip(),
                    defaults={"is_active": True},
                )
            if product.default_fabric:
                FabricNameMaster.objects.get_or_create(
                    name=product.default_fabric.strip(),
                    defaults={"is_active": True},
                )
            if product.default_gsm:
                GSMRangeMaster.objects.get_or_create(
                    name=product.default_gsm.strip(),
                    defaults={"is_active": True},
                )

            return redirect("product_detail", pk=product.pk)
    else:
        form = ProductForm(instance=product)

    type_master = ProductTypeMaster.objects.filter(is_active=True).order_by("name")
    category_master = ProductCategoryMaster.objects.filter(is_active=True).order_by("name")
    fabric_master = FabricNameMaster.objects.filter(is_active=True).order_by("name")
    gsm_master = GSMRangeMaster.objects.filter(is_active=True).order_by("name")

    context = {
        "form": form,
        "mode": "edit",
        "product": product,
        "type_master": type_master,
        "category_master": category_master,
        "fabric_master": fabric_master,
        "gsm_master": gsm_master,
    }
    return render(request, "crm/product_form.html", context)


def product_detail(request, pk):
    product = get_object_or_404(Product, pk=pk)

    context = {
        "product": product,
    }
    return render(request, "crm/product_detail.html", context)


@require_POST
def product_ai_detail(request, pk):
    """
    AI helper for a single product.
    It uses product fields and saves answers into product.notes.
    """
    product = get_object_or_404(Product, pk=pk)
    mode = request.POST.get("mode", "summary").strip() or "summary"
    user_text = request.POST.get("user_text", "")

    base_info = (
        f"Product code: {product.product_code}. "
        f"Name: {product.name}. "
        f"Type: {product.product_type}. "
        f"Category: {product.product_category}. "
        f"Default GSM: {product.default_gsm}. "
        f"Default fabric: {product.default_fabric}. "
        f"Default MOQ: {product.default_moq}. "
        f"Default price: {product.default_price}. "
    )

    if mode == "summary":
        user_prompt = (
            "Give a short summary of this product for internal use. "
            "Cover the key fabric, GSM, price level, and when we should offer it. "
            "Use 4 to 6 lines. "
            + base_info
        )
    elif mode == "use_cases":
        user_prompt = (
            "Suggest use cases and target customers for this product. "
            "Mention season, age group, and selling angle. "
            "Use short bullet style lines. "
            + base_info
        )
    elif mode == "costing":
        user_prompt = (
            "Think like a merchandiser. Give a costing view for this product. "
            "Talk about fabric weight, estimated fabric cost band, work level, "
            "and what price range we can position for small to medium brands. "
            + base_info
        )
    elif mode == "bundle":
        user_prompt = (
            "Suggest simple bundle or collection ideas where this product is the hero. "
            "Include 3 to 5 ideas with product names and set concepts. "
            + base_info
        )
    elif mode == "email":
        user_prompt = (
            "Write a short email paragraph we can send to a client who is looking for this type "
            "of product. Focus on benefits and why our factory is a good fit. "
            + base_info
        )
    elif mode == "spec":
        user_prompt = (
            "List key spec points the team must confirm before sampling or production for this product. "
            "Use bullet style points. "
            + base_info
        )
    elif mode == "chat" and user_text:
        user_prompt = (
            "You are a senior apparel merchandiser and product developer. "
            "Answer the question about this product. "
            f"Question: {user_text} "
            + base_info
        )
    else:
        user_prompt = (
            "Give a short helpful note about this product for internal use. "
            + base_info
        )

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a senior apparel merchandiser and production planner "
                        "for a clothing factory. Keep answers short and practical."
                    ),
                },
                {"role": "user", "content": user_prompt},
            ],
        )
        ai_text = resp.choices[0].message.content or ""
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)})

    header = f"\n\n[AI {mode}]\n"
    product.notes = (product.notes or "") + header + ai_text
    product.save(update_fields=["notes"])

    return JsonResponse({"ok": True, "text": ai_text})


@require_POST
def product_ai_suggest(request):
    """
    Small AI helper for the product form.
    Used by product_form.html with fetch.
    """
    name = request.POST.get("name", "").strip()
    product_type = request.POST.get("product_type", "").strip()
    product_category = request.POST.get("product_category", "").strip()
    default_gsm = request.POST.get("default_gsm", "").strip()
    default_fabric = request.POST.get("default_fabric", "").strip()
    notes = request.POST.get("notes", "").strip()

    if not name:
        return JsonResponse(
            {"ok": False, "error": "Please add a product name first."}
        )

    info = (
        f"Name: {name}. "
        f"Type: {product_type or 'not set'}. "
        f"Category: {product_category or 'not set'}. "
        f"Default GSM: {default_gsm or 'not set'}. "
        f"Default fabric: {default_fabric or 'not set'}. "
        f"Notes: {notes or 'not given'}."
    )

    prompt = (
        "You help a clothing factory set up a product library.\n"
        "Based on this product info, give short and clear suggestions.\n"
        "Return 5 to 7 short lines:\n"
        "- Target customer and use case\n"
        "- Suggested fabric and GSM range\n"
        "- Fit and key design points\n"
        "- Recommended MOQ range\n"
        "- Price band idea (low, medium, high)\n"
        "- Any extra notes for production team\n\n"
        f"Product info: {info}"
    )

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a senior apparel product developer."},
                {"role": "user", "content": prompt},
            ],
        )
        ai_text = resp.choices[0].message.content
        return JsonResponse({"ok": True, "suggestion": ai_text})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)})


# ===================================================
# FABRIC LIBRARY AND AI
# ===================================================

@require_POST
def fabric_ai_suggest(request):
    name = request.POST.get("name", "").strip()
    group = request.POST.get("group", "").strip()
    fabric_type = request.POST.get("fabric_type", "").strip()

    if not name:
        return JsonResponse(
            {"ok": False, "error": "Please type a fabric name first."}
        )

    user_info = (
        f"Name: {name}. "
        f"Group: {group or 'not set'}. "
        f"Type: {fabric_type or 'not set'}."
    )

    prompt = (
        "You are a senior textile technician helping a clothing factory team.\n"
        "Based on the fabric data below, give short helpful suggestions.\n"
        "Return:\n"
        "- Likely composition\n"
        "- GSM range\n"
        "- Stretch level\n"
        "- Hand feel\n"
        "- Best uses\n"
        "- Price level (low, medium, high)\n\n"
        "Keep it very short, 4 to 6 lines.\n\n"
        f"Fabric info: {user_info}"
    )

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a textile expert."},
                {"role": "user", "content": prompt},
            ],
        )

        ai_text = resp.choices[0].message.content

        return JsonResponse({"ok": True, "suggestion": ai_text})

    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)})


@require_POST
def fabric_ai_focus(request, pk):
    fabric = get_object_or_404(Fabric, pk=pk)

    info = (
        f"Name: {fabric.name}. "
        f"Group: {fabric.fabric_group or 'not set'}. "
        f"Type: {fabric.fabric_type or 'not set'}. "
        f"Structure: {fabric.knit_structure or fabric.weave or 'not set'}. "
        f"Composition: {fabric.composition or 'not set'}. "
        f"GSM: {fabric.gsm or 'not set'}. "
        f"Stretch: {fabric.stretch_type or 'not set'}. "
        f"Surface: {fabric.surface or 'not set'}. "
        f"Handfeel: {fabric.handfeel or 'not set'}. "
        f"Drape: {fabric.drape or 'not set'}. "
        f"Weight class: {fabric.weight_class or 'not set'}. "
        f"Warmth: {fabric.warmth or 'not set'}. "
        f"Breathability: {fabric.breathability or 'not set'}. "
        f"Sheerness: {fabric.sheerness or 'not set'}. "
        f"Durability: {fabric.durability or 'not set'}. "
        f"Typical uses: {getattr(fabric, 'typical_uses', '') or 'not set'}."
    )

    prompt = (
        "You are a senior textile technician in a garment factory.\n"
        "Based on the fabric data below, answer in short points:\n"
        "- Best product types to use this fabric for\n"
        "- Main pros and cons\n"
        "- Care and washing tips\n"
        "- Pricing notes for buyers\n"
        "- Any risk or warning for production\n"
        "Keep answer under 10 lines.\n\n"
        f"Fabric data: {info}"
    )

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a textile expert for a clothing factory."},
                {"role": "user", "content": prompt},
            ],
        )
        ai_text = resp.choices[0].message.content
        return JsonResponse({"ok": True, "suggestion": ai_text})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)})


def fabrics_list(request):
    qs = Fabric.objects.all().order_by("-created_at")

    q = request.GET.get("q") or ""
    fabric_group = request.GET.get("fabric_group") or ""
    fabric_type = request.GET.get("fabric_type") or ""

    if q:
        qs = qs.filter(name__icontains=q)

    if fabric_group:
        qs = qs.filter(fabric_group__icontains=fabric_group)

    if fabric_type:
        qs = qs.filter(fabric_type__icontains=fabric_type)

    context = {
        "fabrics": qs,
        "q": q,
        "fabric_group": fabric_group,
        "fabric_type": fabric_type,
    }
    return render(request, "crm/fabric_list.html", context)


def fabric_add(request):
    if request.method == "POST":
        form = FabricForm(request.POST, request.FILES)
        if form.is_valid():
            fabric = form.save()
            sync_fabric_masters(fabric)
            return redirect("fabric_detail", pk=fabric.pk)
    else:
        form = FabricForm()

    context = {
        "form": form,
        "mode": "add",
        "fabric": None,
        "fabric_groups": FabricGroupMaster.objects.all(),
        "fabric_types": FabricTypeMaster.objects.all(),
        "knit_structures": KnitStructureMaster.objects.all(),
        "weaves": WeaveMaster.objects.all(),
        "surfaces": SurfaceMaster.objects.all(),
        "handfeels": HandfeelMaster.objects.all(),
    }
    return render(request, "crm/fabric_form.html", context)


def fabric_edit(request, pk):
    fabric = get_object_or_404(Fabric, pk=pk)

    if request.method == "POST":
        form = FabricForm(request.POST, request.FILES, instance=fabric)
        if form.is_valid():
            fabric = form.save()
            sync_fabric_masters(fabric)
            return redirect("fabric_detail", pk=fabric.pk)
    else:
        form = FabricForm(instance=fabric)

    context = {
        "form": form,
        "mode": "edit",
        "fabric": fabric,
        "fabric_groups": FabricGroupMaster.objects.all(),
        "fabric_types": FabricTypeMaster.objects.all(),
        "knit_structures": KnitStructureMaster.objects.all(),
        "weaves": WeaveMaster.objects.all(),
        "surfaces": SurfaceMaster.objects.all(),
        "handfeels": HandfeelMaster.objects.all(),
    }
    return render(request, "crm/fabric_form.html", context)


@require_POST
def fabric_ai_detail(request, pk):
    fabric = get_object_or_404(Fabric, pk=pk)

    mode = request.POST.get("mode", "summary")
    user_text = request.POST.get("user_text", "").strip()
    compare_text = request.POST.get("compare_text", "").strip()

    info_parts = [
        f"Name: {fabric.name}",
        f"Code: {fabric.fabric_code}",
        f"Group: {fabric.fabric_group or 'not set'}",
        f"Type: {fabric.fabric_type or 'not set'}",
        f"Weave: {fabric.weave or 'not set'}",
        f"Knit structure: {fabric.knit_structure or 'not set'}",
        f"Construction: {fabric.construction or 'not set'}",
        f"Composition: {fabric.composition or 'not set'}",
        f"GSM: {fabric.gsm or 'not set'}",
        f"Stretch: {fabric.stretch_type or 'not set'}",
        f"Surface: {fabric.surface or 'not set'}",
        f"Handfeel: {fabric.handfeel or 'not set'}",
        f"Drape: {fabric.drape or 'not set'}",
        f"Warmth: {fabric.warmth or 'not set'}",
        f"Weight class: {fabric.weight_class or 'not set'}",
        f"Breathability: {fabric.breathability or 'not set'}",
        f"Sheerness: {fabric.sheerness or 'not set'}",
        f"Shrinkage: {fabric.shrinkage or 'not set'}",
        f"Durability: {fabric.durability or 'not set'}",
        f"Colors: {fabric.color_options or 'not set'}",
    ]

    if fabric.price_per_kg:
        info_parts.append(f"Price per kg: {fabric.price_per_kg}")
    if fabric.price_per_meter:
        info_parts.append(f"Price per meter: {fabric.price_per_meter}")

    fabric_info = "\n".join(info_parts)

    if mode == "summary":
        task = (
            "Give a very short summary of this fabric for internal use. "
            "Two or three short lines. No marketing style, only clear facts."
        )
    elif mode == "use_cases":
        task = (
            "Suggest the best end uses for this fabric. "
            "List three to six idea lines that are clear for a garment factory."
        )
    elif mode == "ideal_products":
        task = (
            "Suggest ideal product types and garment styles that this fabric is good for. "
            "Think like a clothing factory that does activewear, streetwear, kids, and corporate."
        )
    elif mode == "costing":
        task = (
            "Give a simple costing view. Explain if this fabric feels low, medium, or high cost, "
            "and how a factory should think about margin and MOQ when using it."
        )
    elif mode == "properties":
        task = (
            "Explain the key properties of this fabric in simple language. "
            "Focus on stretch, handfeel, warmth, drape, and care points."
        )
    elif mode == "compare":
        other = compare_text or "Another generic fabric used for similar end use."
        task = (
            "Compare this fabric with the other fabric given. "
            "Explain pros and cons for each and when to pick one over the other.\n\n"
            f"Other fabric: {other}"
        )
    elif mode == "bom":
        task = (
            "Suggest a simple bill of material idea using this fabric as main body. "
            "Include fabric main body, rib or cuff, lining if needed, and basic trims."
        )
    elif mode == "moq_lead":
        task = (
            "Suggest a simple view of MOQ and lead time a factory might use with this fabric. "
            "Keep it in two to four short lines."
        )
    else:
        if not user_text:
            return JsonResponse(
                {"ok": False, "error": "Please type a question for AI."}
            )
        task = (
            "You are a senior textile expert helping a garment factory. "
            "Answer the user question based on the fabric info below.\n\n"
            f"User question: {user_text}"
        )

    prompt = (
        "Fabric info:\n"
        f"{fabric_info}\n\n"
        "Task:\n"
        f"{task}\n\n"
        "Answer in short clear English. Use bullet points if helpful."
    )

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a textile expert for a clothing factory."},
                {"role": "user", "content": prompt},
            ],
        )
        ai_text = resp.choices[0].message.content or ""
    except Exception as e:
        return JsonResponse(
            {"ok": False, "error": f"AI error: {str(e)}"}
        )

    save_modes = {
        "summary": "AI summary",
        "use_cases": "AI use cases",
        "ideal_products": "AI ideal products",
        "costing": "AI costing view",
        "properties": "AI properties",
        "compare": "AI compare",
        "bom": "AI BOM",
        "moq_lead": "AI MOQ and lead time",
        "chat": "AI chat note",
    }

    label = save_modes.get(mode, "AI note")
    note_block = f"\n\n[{label}] \n{ai_text}".strip()

    if fabric.notes:
        fabric.notes = f"{fabric.notes.rstrip()}\n\n{note_block}"
    else:
        fabric.notes = note_block
    fabric.save()

    return JsonResponse({"ok": True, "text": ai_text})


def fabric_detail(request, pk):
    fabric = get_object_or_404(Fabric, pk=pk)

    context = {
        "fabric": fabric,
    }
    return render(request, "crm/fabric_detail.html", context)


def sync_fabric_masters(fabric):
    """Make sure new values are stored in master tables."""
    def upsert(model_cls, value):
        if not value:
            return
        v = value.strip()
        if not v:
            return
        exists = model_cls.objects.filter(name__iexact=v).first()
        if not exists:
            model_cls.objects.create(name=v)

    upsert(FabricGroupMaster, fabric.fabric_group)
    upsert(FabricTypeMaster, fabric.fabric_type)
    upsert(KnitStructureMaster, fabric.knit_structure)
    upsert(WeaveMaster, fabric.weave)
    upsert(SurfaceMaster, fabric.surface)
    upsert(HandfeelMaster, fabric.handfeel)


# ===================================================
# ACCESSORY LIBRARY AND AI
# ===================================================

@require_POST
def accessory_ai_suggest(request):
    name = request.POST.get("name", "").strip()
    acc_type = request.POST.get("accessory_type", "").strip()
    color = request.POST.get("color", "").strip()

    if not name:
        return JsonResponse({"ok": False, "error": "Please type a name first."})

    prompt = (
        "You are a textile and garment accessories expert.\n"
        "Based on the data below, suggest:\n"
        "- Material\n"
        "- Best use case\n"
        "- Durability level\n"
        "- Price level (low, medium, high)\n"
        "- Short production notes\n"
        "Keep answer under 6 lines.\n\n"
        f"Accessory name: {name}\n"
        f"Type: {acc_type or 'not specified'}\n"
        f"Color: {color or 'not specified'}"
    )

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are an accessory expert."},
                {"role": "user", "content": prompt}
            ]
        )
        ai_text = resp.choices[0].message.content
        return JsonResponse({"ok": True, "suggestion": ai_text})

    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)})


def accessories_list(request):
    qs = Accessory.objects.all().order_by("-created_at")

    q = request.GET.get("q") or ""
    accessory_type = request.GET.get("accessory_type") or ""
    color = request.GET.get("color") or ""

    if q:
        qs = qs.filter(name__icontains=q)
    if accessory_type:
        qs = qs.filter(accessory_type__icontains=accessory_type)
    if color:
        qs = qs.filter(color__icontains=color)

    context = {
        "accessories": qs,
        "q": q,
        "accessory_type": accessory_type,
        "color": color,
    }
    return render(request, "crm/accessory_list.html", context)


def _accessory_basics():
    qs = Accessory.objects.all()

    type_list = (
        qs.exclude(accessory_type="")
        .values_list("accessory_type", flat=True)
        .distinct()
        .order_by("accessory_type")
    )
    size_list = (
        qs.exclude(size="")
        .values_list("size", flat=True)
        .distinct()
        .order_by("size")
    )
    color_list = (
        qs.exclude(color="")
        .values_list("color", flat=True)
        .distinct()
        .order_by("color")
    )
    material_list = (
        qs.exclude(material="")
        .values_list("material", flat=True)
        .distinct()
        .order_by("material")
    )
    finish_list = (
        qs.exclude(finish="")
        .values_list("finish", flat=True)
        .distinct()
        .order_by("finish")
    )
    supplier_list = (
        qs.exclude(supplier="")
        .values_list("supplier", flat=True)
        .distinct()
        .order_by("supplier")
    )

    return {
        "acc_type_list": type_list,
        "acc_size_list": size_list,
        "acc_color_list": color_list,
        "acc_material_list": material_list,
        "acc_finish_list": finish_list,
        "acc_supplier_list": supplier_list,
    }


def accessory_add(request):
    if request.method == "POST":
        form = AccessoryForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            return redirect("accessories_list")
    else:
        form = AccessoryForm()

    context = {"form": form, "mode": "add"}
    context.update(_accessory_basics())
    return render(request, "crm/accessory_form.html", context)


def accessory_edit(request, pk):
    accessory = get_object_or_404(Accessory, pk=pk)

    if request.method == "POST":
        form = AccessoryForm(request.POST, request.FILES, instance=accessory)
        if form.is_valid():
            form.save()
            return redirect("accessory_detail", pk=pk)
    else:
        form = AccessoryForm(instance=accessory)

    context = {"form": form, "mode": "edit", "accessory": accessory}
    context.update(_accessory_basics())
    return render(request, "crm/accessory_form.html", context)


def accessory_detail(request, pk):
    accessory = get_object_or_404(Accessory, pk=pk)
    context = {
        "accessory": accessory,
    }
    return render(request, "crm/accessory_detail.html", context)


# ===================================================
# TRIM LIBRARY AND AI
# ===================================================

def trims_list(request):
    qs = Trim.objects.all().order_by("-created_at")

    q = request.GET.get("q") or ""
    trim_type = request.GET.get("trim_type") or ""
    color = request.GET.get("color") or ""

    if q:
        qs = qs.filter(name__icontains=q)
    if trim_type:
        qs = qs.filter(trim_type__icontains=trim_type)
    if color:
        qs = qs.filter(color__icontains=color)

    context = {
        "trims": qs,
        "q": q,
        "trim_type": trim_type,
        "color": color,
    }
    return render(request, "crm/trim_list.html", context)


def _trim_basics():
    qs = Trim.objects.all()

    type_list = (
        qs.exclude(trim_type="")
        .values_list("trim_type", flat=True)
        .distinct()
        .order_by("trim_type")
    )
    width_list = (
        qs.exclude(width="")
        .values_list("width", flat=True)
        .distinct()
        .order_by("width")
    )
    color_list = (
        qs.exclude(color="")
        .values_list("color", flat=True)
        .distinct()
        .order_by("color")
    )
    material_list = (
        qs.exclude(material="")
        .values_list("material", flat=True)
        .distinct()
        .order_by("material")
    )

    return {
        "trim_type_list": type_list,
        "trim_width_list": width_list,
        "trim_color_list": color_list,
        "trim_material_list": material_list,
    }


def trim_add(request):
    if request.method == "POST":
        form = TrimForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            return redirect("trims_list")
    else:
        form = TrimForm()

    context = {"form": form, "mode": "add"}
    context.update(_trim_basics())
    return render(request, "crm/trim_form.html", context)


def trim_edit(request, pk):
    trim = get_object_or_404(Trim, pk=pk)

    if request.method == "POST":
        form = TrimForm(request.POST, request.FILES, instance=trim)
        if form.is_valid():
            form.save()
            return redirect("trim_detail", pk=pk)
    else:
        form = TrimForm(instance=trim)

    context = {"form": form, "mode": "edit", "trim": trim}
    context.update(_trim_basics())
    return render(request, "crm/trim_form.html", context)


def trim_detail(request, pk):
    trim = get_object_or_404(Trim, pk=pk)
    context = {
        "trim": trim,
    }
    return render(request, "crm/trim_detail.html", context)


@require_POST
def trim_ai_suggest(request):
    name = request.POST.get("name", "").strip()
    trim_type = request.POST.get("trim_type", "").strip()
    material = request.POST.get("material", "").strip()
    width = request.POST.get("width", "").strip()

    if not name:
        return JsonResponse(
            {"ok": False, "error": "Please type a trim name first."}
        )

    trim_info = (
        f"Name: {name}. "
        f"Type: {trim_type or 'not set'}. "
        f"Material: {material or 'not set'}. "
        f"Width: {width or 'not set'}."
    )

    prompt = (
        "You are a senior garment trim expert helping a clothing factory team.\n"
        "Based on the trim data below, give short useful suggestions.\n"
        "Return:\n"
        "- Best use cases\n"
        "- Sewing or application notes\n"
        "- Durability and care notes\n"
        "- Price level (low, medium, high)\n\n"
        "Keep it very short, 4 to 6 lines.\n\n"
        f"Trim info: {trim_info}"
    )

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a trim and accessories expert."},
                {"role": "user", "content": prompt},
            ],
        )

        ai_text = resp.choices[0].message.content
        return JsonResponse({"ok": True, "suggestion": ai_text})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)})


# ===================================================
# THREAD LIBRARY
# ===================================================

def threads_list(request):
    qs = ThreadOption.objects.all().order_by("-created_at")

    q = request.GET.get("q") or ""
    thread_type = request.GET.get("thread_type") or ""
    color = request.GET.get("color") or ""

    if q:
        qs = qs.filter(name__icontains=q)
    if thread_type:
        qs = qs.filter(thread_type__icontains=thread_type)
    if color:
        qs = qs.filter(color__icontains=color)

    context = {
        "threads": qs,
        "q": q,
        "thread_type": thread_type,
        "color": color,
    }
    return render(request, "crm/thread_list.html", context)


def thread_add(request):
    if request.method == "POST":
        form = ThreadOptionForm(request.POST, request.FILES)
        if form.is_valid():
            thread = form.save()
            return redirect("thread_detail", pk=thread.pk)
    else:
        form = ThreadOptionForm()

    return render(request, "crm/thread_form.html", {"form": form, "mode": "add"})


def thread_edit(request, pk):
    thread = get_object_or_404(ThreadOption, pk=pk)

    if request.method == "POST":
        form = ThreadOptionForm(request.POST, request.FILES, instance=thread)
        if form.is_valid():
            form.save()
            return redirect("thread_detail", pk=thread.pk)
    else:
        form = ThreadOptionForm(instance=thread)

    context = {
        "form": form,
        "mode": "edit",
        "thread": thread,
    }
    return render(request, "crm/thread_form.html", context)


def thread_detail(request, pk):
    thread = get_object_or_404(ThreadOption, pk=pk)
    context = {
        "thread": thread,
    }
    return render(request, "crm/thread_detail.html", context)





# =========================
# INVENTORY VIEWS
# =========================

INVENTORY_GROUP_FILTERS = {
    "fabric": Q(material_group="fabric") | Q(category="fabric_roll"),
    "trim": Q(material_group="trim") | Q(category="trim"),
    "label": Q(material_group="label"),
    "packaging": Q(material_group="packaging") | Q(category__in=["polybag", "carton"]),
    "printing_material": Q(material_group="printing_material"),
    "accessories": Q(material_group="accessories") | Q(category="accessory"),
    "sample_material": Q(material_group="sample_material") | Q(category__in=["thread", "needle"]),
}

INVENTORY_GROUP_LABELS = [
    ("fabric", "Fabric"),
    ("trim", "Trim"),
    ("label", "Label"),
    ("packaging", "Packaging"),
    ("printing_material", "Printing Material"),
    ("accessories", "Accessories"),
    ("sample_material", "Sample Material"),
    ("other", "Other"),
]


def _inventory_can_view_financials(user):
    return can_view_lifecycle_profit(user)


def _inventory_decimal(value):
    if value in ("", None):
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _inventory_group_key(item):
    return getattr(item, "effective_material_group", None) or "other"


def _inventory_group_label(key):
    return dict(INVENTORY_GROUP_LABELS).get(key, "Other")


def _inventory_reorder_level(item):
    return getattr(item, "effective_reorder_level", None) or Decimal("0")


def _inventory_minimum_stock(item):
    return getattr(item, "effective_minimum_stock", None) or Decimal("0")


def _inventory_is_low(item):
    quantity = _inventory_decimal(getattr(item, "quantity", 0))
    return quantity <= _inventory_reorder_level(item)


def _inventory_value(item):
    return getattr(item, "stock_value", Decimal("0")) or Decimal("0")


def _inventory_waste_percent(item):
    quantity = _inventory_decimal(getattr(item, "quantity", 0))
    waste = _inventory_decimal(getattr(item, "waste_quantity", 0)) + _inventory_decimal(getattr(item, "damaged_quantity", 0))
    total = quantity + waste
    if total <= 0:
        return Decimal("0")
    return ((waste / total) * Decimal("100")).quantize(Decimal("0.01"))


def _inventory_record_movement(item, movement_type, quantity, *, request=None, production_order=None, production_material=None, reason="", notes=""):
    qty = _inventory_decimal(quantity)
    if qty <= 0:
        return None
    return InventoryMovement.objects.create(
        inventory_item=item,
        movement_type=movement_type,
        quantity=qty,
        reason=reason or "",
        production_order=production_order,
        production_material=production_material,
        created_by=request.user if request and request.user.is_authenticated else None,
        notes=notes or "",
    )


def _inventory_apply_item_movement(item, movement_type, quantity):
    qty = _inventory_decimal(quantity)
    if qty <= 0:
        return
    if movement_type == "received":
        item.quantity = _inventory_decimal(item.quantity) + qty
        item.incoming_quantity = max(_inventory_decimal(item.incoming_quantity) - qty, Decimal("0"))
    elif movement_type == "allocated":
        item.reserved_quantity = _inventory_decimal(item.reserved_quantity) + qty
    elif movement_type == "consumed":
        item.quantity = _inventory_decimal(item.quantity) - qty
        item.reserved_quantity = max(_inventory_decimal(item.reserved_quantity) - qty, Decimal("0"))
    elif movement_type == "damaged":
        item.quantity = _inventory_decimal(item.quantity) - qty
        item.reserved_quantity = max(_inventory_decimal(item.reserved_quantity) - qty, Decimal("0"))
        item.damaged_quantity = _inventory_decimal(item.damaged_quantity) + qty
        item.waste_quantity = _inventory_decimal(item.waste_quantity) + qty
    elif movement_type == "adjusted":
        item.quantity = _inventory_decimal(item.quantity) + qty
    item.save(update_fields=["quantity", "incoming_quantity", "reserved_quantity", "damaged_quantity", "waste_quantity", "updated_at"])


def _inventory_movement_rows(item=None, limit=20):
    qs = InventoryMovement.objects.select_related("inventory_item", "production_order", "created_by")
    if item is not None:
        qs = qs.filter(inventory_item=item)
    return qs.order_by("-created_at", "-id")[:limit]


def _production_reserve_inventory(order, item, quantity, note, request):
    qty = _inventory_decimal(quantity)
    if qty <= 0:
        messages.warning(request, "Please enter a material quantity bigger than zero.")
        return None

    line = ProductionOrderMaterial.objects.filter(order=order, inventory_item=item).first()
    old_allocated = _inventory_decimal(getattr(line, "allocated_quantity", None) or getattr(line, "quantity", 0)) if line else Decimal("0")
    if line:
        line.quantity = qty
        line.allocated_quantity = qty
        if note:
            line.notes = note
        line.save()
    else:
        line = ProductionOrderMaterial.objects.create(
            order=order,
            inventory_item=item,
            quantity=qty,
            allocated_quantity=qty,
            unit_type=item.unit_type,
            notes=note,
        )

    diff = qty - old_allocated
    if diff > 0:
        item.reserved_quantity = _inventory_decimal(item.reserved_quantity) + diff
        item.save(update_fields=["reserved_quantity", "updated_at"])
        _inventory_record_movement(
            item,
            "allocated",
            diff,
            request=request,
            production_order=order,
            production_material=line,
            reason="Reserved for production",
            notes=note,
        )
    elif diff < 0:
        item.reserved_quantity = max(_inventory_decimal(item.reserved_quantity) + diff, Decimal("0"))
        item.save(update_fields=["reserved_quantity", "updated_at"])
        _inventory_record_movement(
            item,
            "adjusted",
            abs(diff),
            request=request,
            production_order=order,
            production_material=line,
            reason="Production reservation reduced",
            notes=note,
        )
    return line


def _production_consume_inventory(line, quantity, request, movement_type="consumed"):
    qty = _inventory_decimal(quantity)
    if qty <= 0:
        messages.warning(request, "Please enter a quantity bigger than zero.")
        return

    item = line.inventory_item
    if movement_type == "damaged":
        line.damaged_quantity = _inventory_decimal(line.damaged_quantity) + qty
    else:
        line.consumed_quantity = _inventory_decimal(line.consumed_quantity) + qty
    line.save()

    _inventory_apply_item_movement(item, movement_type, qty)
    _inventory_record_movement(
        item,
        movement_type,
        qty,
        request=request,
        production_order=line.order,
        production_material=line,
        reason="Production material usage" if movement_type == "consumed" else "Production material damage",
    )
    if _inventory_decimal(item.quantity) < 0:
        messages.warning(request, "Material updated. Inventory is now negative for this item.")
    else:
        messages.success(request, "Production material movement saved.")


def _production_remove_inventory_reservation(line, request):
    item = line.inventory_item
    remaining = max(_inventory_decimal(line.remaining_quantity), Decimal("0"))
    if remaining > 0:
        item.reserved_quantity = max(_inventory_decimal(item.reserved_quantity) - remaining, Decimal("0"))
        item.save(update_fields=["reserved_quantity", "updated_at"])
        _inventory_record_movement(
            item,
            "adjusted",
            remaining,
            request=request,
            production_order=line.order,
            production_material=line,
            reason="Production reservation removed",
        )
    line.delete()


def inventory_list(request):
    can_view_financials = _inventory_can_view_financials(request.user)
    base_items = InventoryItem.objects.all().prefetch_related("production_materials")
    items = base_items.order_by("name")

    search = request.GET.get("q", "").strip()
    category = request.GET.get("category", "").strip()
    status = request.GET.get("status", "all").strip() or "all"
    stock_filter = request.GET.get("stock", "all").strip() or "all"

    if search:
        items = items.filter(
            Q(name__icontains=search)
            | Q(code__icontains=search)
            | Q(sku__icontains=search)
            | Q(location__icontains=search)
        )

    if category:
        if category in INVENTORY_GROUP_FILTERS:
            items = items.filter(INVENTORY_GROUP_FILTERS[category])
        elif category == "other":
            combined = Q()
            for group_q in INVENTORY_GROUP_FILTERS.values():
                combined |= group_q
            items = items.exclude(combined)
        else:
            items = items.filter(category=category)

    if status == "active":
        items = items.filter(is_active=True)
    elif status == "inactive":
        items = items.filter(is_active=False)

    item_rows = []
    for item in items:
        low_stock = _inventory_is_low(item)
        negative_stock = _inventory_decimal(item.quantity) < 0
        available_quantity = getattr(item, "available_quantity", Decimal("0"))
        waste_percent = _inventory_waste_percent(item)
        row = {
            "item": item,
            "material_group": _inventory_group_key(item),
            "material_group_label": _inventory_group_label(_inventory_group_key(item)),
            "low_stock": low_stock,
            "negative_stock": negative_stock,
            "available_quantity": available_quantity,
            "reorder_level": _inventory_reorder_level(item),
            "minimum_stock": _inventory_minimum_stock(item),
            "waste_percent": waste_percent,
            "needs_reorder": low_stock,
        }
        if stock_filter == "low" and not low_stock:
            continue
        if stock_filter == "negative" and not negative_stock:
            continue
        if stock_filter == "reserved" and not (_inventory_decimal(item.reserved_quantity) > 0):
            continue
        item_rows.append(row)

    filtered_items = [row["item"] for row in item_rows]
    total_items = len(item_rows)
    total_quantity = sum((_inventory_decimal(row["item"].quantity) for row in item_rows), Decimal("0"))

    total_value = sum((_inventory_value(row["item"]) for row in item_rows), Decimal("0")) if can_view_financials else None
    low_stock_count = len([row for row in item_rows if row["low_stock"]])
    negative_stock_count = len([row for row in item_rows if row["negative_stock"]])
    incoming_stock = sum((_inventory_decimal(row["item"].incoming_quantity) for row in item_rows), Decimal("0"))
    reserved_stock = sum((_inventory_decimal(row["item"].reserved_quantity) for row in item_rows), Decimal("0"))
    active_materials = len([row for row in item_rows if row["item"].is_active])
    low_by_group = {
        key: len([row for row in item_rows if row["low_stock"] and row["material_group"] == key])
        for key, _label in INVENTORY_GROUP_LABELS
    }
    reorder_alerts = [row for row in item_rows if row["needs_reorder"]][:8]
    negative_alerts = [row for row in item_rows if row["negative_stock"]][:8]
    delayed_incoming = [
        row for row in item_rows
        if _inventory_decimal(row["item"].incoming_quantity) > 0 and row["low_stock"]
    ][:8]
    recent_movements = list(_inventory_movement_rows(limit=12))
    allocated_qty = ProductionOrderMaterial.objects.aggregate(s=Sum("allocated_quantity"))["s"] or Decimal("0")
    consumed_qty = ProductionOrderMaterial.objects.aggregate(s=Sum("consumed_quantity"))["s"] or Decimal("0")
    pending_allocation = sum((max(_inventory_decimal(line.remaining_quantity), Decimal("0")) for line in ProductionOrderMaterial.objects.all()), Decimal("0"))
    dead_stock_count = len([
        row for row in item_rows
        if row["item"].is_active and not row["item"].production_materials.exists() and _inventory_decimal(row["item"].quantity) > 0
    ])
    dead_stock_value = sum(
        (_inventory_value(row["item"]) for row in item_rows if row["item"].is_active and not row["item"].production_materials.exists()),
        Decimal("0"),
    ) if can_view_financials else None
    waste_estimate = sum((getattr(row["item"], "waste_value", Decimal("0")) or Decimal("0") for row in item_rows), Decimal("0")) if can_view_financials else None

    if low_stock_count > 0:
        smartbrain_message = (
            f"You have {low_stock_count} items at or below minimum. "
            "Plan a reorder for these first."
        )
    else:
        smartbrain_message = (
            "Stock levels look okay. "
            "Watch high value items and fast moving items."
        )

    context = {
        "items": filtered_items,
        "item_rows": item_rows,
        "search": search,
        "selected_category": category,
        "selected_status": status,
        "selected_stock": stock_filter,
        "total_items": total_items,
        "total_quantity": total_quantity,
        "total_value": total_value,
        "low_stock_count": low_stock_count,
        "negative_stock_count": negative_stock_count,
        "incoming_stock": incoming_stock,
        "reserved_stock": reserved_stock,
        "active_materials": active_materials,
        "low_by_group": low_by_group,
        "reorder_alerts": reorder_alerts,
        "negative_alerts": negative_alerts,
        "delayed_incoming": delayed_incoming,
        "recent_movements": recent_movements,
        "allocated_qty": allocated_qty,
        "consumed_qty": consumed_qty,
        "pending_allocation": pending_allocation,
        "dead_stock_count": dead_stock_count,
        "dead_stock_value": dead_stock_value,
        "waste_estimate": waste_estimate,
        "category_groups": INVENTORY_GROUP_LABELS,
        "can_view_inventory_financials": can_view_financials,
        "smartbrain_message": smartbrain_message,
    }
    return render(request, "crm/inventory_list.html", context)


def inventory_add(request):
    can_view_financials = _inventory_can_view_financials(request.user)
    if request.method == "POST":
        form = InventoryItemForm(
            request.POST,
            request.FILES,
            can_edit_internal_costing=can_view_financials,
        )
        if form.is_valid():
            item = form.save()
            if _inventory_decimal(item.quantity) > 0:
                _inventory_record_movement(
                    item,
                    "received",
                    item.quantity,
                    request=request,
                    reason="Initial stock entry",
                )
            messages.success(request, "Inventory item created.")
            return redirect("inventory_detail", pk=item.pk)
    else:
        form = InventoryItemForm(can_edit_internal_costing=can_view_financials)

    return render(
        request,
        "crm/inventory_form.html",
        {"form": form, "mode": "add", "item": None, "can_view_inventory_financials": can_view_financials},
    )


def inventory_edit(request, pk):
    item = get_object_or_404(InventoryItem, pk=pk)
    can_view_financials = _inventory_can_view_financials(request.user)

    if request.method == "POST":
        old_quantity = _inventory_decimal(item.quantity)
        form = InventoryItemForm(
            request.POST,
            request.FILES,
            instance=item,
            can_edit_internal_costing=can_view_financials,
        )
        if form.is_valid():
            item = form.save()
            new_quantity = _inventory_decimal(item.quantity)
            delta = new_quantity - old_quantity
            if delta:
                _inventory_record_movement(
                    item,
                    "adjusted",
                    abs(delta),
                    request=request,
                    reason="Manual inventory edit",
                    notes=f"Quantity changed from {old_quantity} to {new_quantity}.",
                )
            messages.success(request, "Inventory item updated.")
            return redirect("inventory_detail", pk=item.pk)
    else:
        form = InventoryItemForm(instance=item, can_edit_internal_costing=can_view_financials)

    return render(
        request,
        "crm/inventory_form.html",
        {"form": form, "mode": "edit", "item": item, "can_view_inventory_financials": can_view_financials},
    )


def inventory_detail(request, pk):
    item = get_object_or_404(InventoryItem, pk=pk)
    can_view_financials = _inventory_can_view_financials(request.user)

    total_value = None
    if can_view_financials and item.unit_cost and item.quantity is not None:
        total_value = item.unit_cost * item.quantity

    # handle quick reorder post from detail page
    if request.method == "POST" and request.POST.get("inventory_action") == "movement":
        movement_type = (request.POST.get("movement_type") or "").strip()
        qty = _inventory_decimal(request.POST.get("movement_quantity"))
        reason = (request.POST.get("movement_reason") or "").strip()
        production_id = (request.POST.get("production_order") or "").strip()
        production_order = ProductionOrder.objects.filter(pk=production_id).first() if production_id else None

        if movement_type not in {key for key, _label in InventoryMovement.MOVEMENT_CHOICES}:
            messages.error(request, "Please choose a valid movement type.")
        elif qty <= 0:
            messages.warning(request, "Please enter a movement quantity bigger than zero.")
        else:
            _inventory_apply_item_movement(item, movement_type, qty)
            _inventory_record_movement(
                item,
                movement_type,
                qty,
                request=request,
                production_order=production_order,
                reason=reason or "Manual warehouse movement",
            )
            if _inventory_decimal(item.quantity) < 0:
                messages.warning(request, "Movement saved. This item now has negative stock.")
            else:
                messages.success(request, "Inventory movement saved.")
            return redirect("inventory_detail", pk=item.pk)

    if request.method == "POST" and "quick_reorder" in request.POST:
        qty_str = (request.POST.get("reorder_quantity") or "0").strip()
        note = (request.POST.get("reorder_note") or "").strip()

        try:
            qty = Decimal(qty_str)
        except Exception:
            qty = Decimal("0")

        if qty > 0:
            InventoryReorder.objects.create(
                inventory_item=item,
                quantity=qty,
                note=note,
                created_by=request.user if request.user.is_authenticated else None,
            )
            messages.success(request, "Reorder saved for this item.")
            return redirect("inventory_detail", pk=item.pk)
        else:
            messages.warning(request, "Please enter a reorder quantity bigger than zero.")

    reorders = item.reorders.all()[:20]
    production_lines = item.production_materials.select_related("order").order_by("-created_at")[:20]
    movements = _inventory_movement_rows(item=item, limit=30)
    production_options = ProductionOrder.objects.select_related("customer").order_by("-created_at")[:40]

    context = {
        "item": item,
        "total_value": total_value,
        "reorders": reorders,
        "production_lines": production_lines,
        "movements": movements,
        "production_options": production_options,
        "movement_choices": InventoryMovement.MOVEMENT_CHOICES,
        "can_view_inventory_financials": can_view_financials,
        "minimum_stock": _inventory_minimum_stock(item),
        "reorder_level": _inventory_reorder_level(item),
        "available_quantity": getattr(item, "available_quantity", Decimal("0")),
        "waste_percent": _inventory_waste_percent(item),
    }
    return render(request, "crm/inventory_detail.html", context)

def inventory_detail_pdf(request, pk):
    item = get_object_or_404(InventoryItem, pk=pk)
    can_view_financials = _inventory_can_view_financials(request.user)

    # try to use reportlab for real PDF
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
    except ImportError:
        # Safe fallback so system does not break
        return HttpResponse(
            "ReportLab is not installed yet. Ask your dev to install 'reportlab' to enable PDF.",
            content_type="text/plain",
        )

    response = HttpResponse(content_type="application/pdf")
    filename = f"inventory_{item.pk}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    p = canvas.Canvas(response, pagesize=letter)
    width, height = letter
    y = height - 50

    def ensure_space(y_pos, needed):
        if y_pos - needed < 50:
            p.showPage()
            y_pos = height - 50
        return y_pos

    p.setFont("Helvetica-Bold", 16)
    p.drawString(50, y, f"Inventory item: {item.name}")
    y -= 30

    p.setFont("Helvetica", 11)

    lines = [
        f"Category: {item.get_category_display()}",
        f"Material group: {_inventory_group_label(_inventory_group_key(item))}",
        f"Code: {item.code or 'Not set'}",
        f"SKU: {item.sku or 'Not set'}",
        f"Unit type: {item.unit_type or 'Not set'}",
        f"Quantity: {item.quantity}",
        f"Minimum stock: {_inventory_minimum_stock(item)}",
        f"Reorder level: {_inventory_reorder_level(item)}",
        f"Available quantity: {getattr(item, 'available_quantity', Decimal('0'))}",
        f"Reserved quantity: {item.reserved_quantity}",
    ]

    if can_view_financials:
        lines.append(f"Unit cost: {item.unit_cost or 'Not set'}")
        lines.append(f"Supplier: {item.supplier_name or 'Not set'}")
        lines.append(f"Waste quantity: {item.waste_quantity}")

    if can_view_financials and item.unit_cost and item.quantity is not None:
        total_value = item.unit_cost * item.quantity
        lines.append(f"Total value: {total_value}")

    lines.append(f"Location: {item.location or 'Not set'}")
    lines.append(f"Active: {'Yes' if item.is_active else 'No'}")

    for line in lines:
        p.drawString(50, y, line)
        y -= 18

    if item.notes:
        p.drawString(50, y, "Notes:")
        y -= 18
        text_obj = p.beginText(50, y)
        text_obj.setFont("Helvetica", 10)
        for note_line in str(item.notes).splitlines():
            text_obj.textLine(note_line)
        p.drawText(text_obj)

    p.showPage()
    p.save()
    return response

@require_POST
def inventory_delete(request, pk):
    item = get_object_or_404(InventoryItem, pk=pk)
    name = item.name
    item.delete()
    messages.success(request, f"Inventory item '{name}' deleted.")
    return redirect("inventory_list")


@require_POST
def inventory_quick_reorder(request, pk):
    """
    Quick reorder from list page:
    for now we just redirect to detail page reorder section.
    """
    item = get_object_or_404(InventoryItem, pk=pk)
    # later we can read quantity from the list with JS
    return redirect("inventory_detail", pk=item.pk)


@require_POST
def inventory_ai_overview(request):
    items = InventoryItem.objects.all()
    can_view_financials = _inventory_can_view_financials(request.user)

    total_items = items.count()
    total_quantity = items.aggregate(s=Sum("quantity"))["s"] or 0

    total_value = 0
    low_stock = 0
    by_category = {}

    for it in items:
        cat = it.get_category_display()
        by_category[cat] = by_category.get(cat, 0) + 1

        if can_view_financials and it.unit_cost and it.quantity:
            total_value += it.unit_cost * it.quantity

        if it.min_level is not None and it.quantity is not None:
            if it.quantity <= it.min_level:
                low_stock += 1

    cat_lines = ", ".join(f"{k}: {v}" for k, v in by_category.items()) or "No categories"

    prompt = (
        "You are the SmartBrain AI for a garment inventory system.\n"
        f"Total items: {total_items}\n"
        f"Total quantity: {total_quantity}\n"
        f"Total value: {total_value if can_view_financials else 'Restricted'}\n"
        f"Low stock: {low_stock}\n"
        f"Category summary: {cat_lines}\n\n"
        "Give short advice in simple English:\n"
        "- High risk items\n"
        "- What to reorder first\n"
        "- Anything that looks wasteful\n"
        "- Any slow items to check\n"
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You assist inventory decisions for a garment factory.",
                },
                {"role": "user", "content": prompt},
            ],
        )
        ai_text = response.choices[0].message.content
        return JsonResponse({"ok": True, "text": ai_text})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)})

# -----------------------------------------
# World Dashboard and Tools
# -----------------------------------------

def world_tools(request):
    return render(request, "crm/world_tools.html")


def world_dashboard(request):
    cities = [
        {"name": "Vancouver", "tz": "America/Vancouver"},
        {"name": "Toronto", "tz": "America/Toronto"},
        {"name": "New York", "tz": "America/New_York"},
        {"name": "London", "tz": "Europe/London"},
        {"name": "Dhaka", "tz": "Asia/Dhaka"},
    ]

    currencies = [
        {"pair": "CAD -> BDT"},
        {"pair": "USD -> BDT"},
        {"pair": "EUR -> BDT"},
        {"pair": "GBP -> BDT"},
    ]

    trend_cards = [
        {"label": "Demand pulse", "value": "62", "delta": "+4%", "note": "last 30 days", "tone": "up"},
        {"label": "Price pressure", "value": "47", "delta": "-2%", "note": "yarn index", "tone": "down"},
        {"label": "Lead time risk", "value": "31", "delta": "+1%", "note": "port congestion", "tone": "flat"},
        {"label": "Eco share", "value": "28%", "delta": "+6%", "note": "buyer asks", "tone": "up"},
    ]

    category_mix = [
        {"label": "Activewear", "value": 28},
        {"label": "Lounge sets", "value": 22},
        {"label": "Denim", "value": 16},
        {"label": "Outerwear", "value": 14},
        {"label": "Basics", "value": 20},
    ]

    region_signals = [
        {"region": "North America", "demand": 68, "lead_time": "42 days", "note": "Stable buys"},
        {"region": "Europe", "demand": 54, "lead_time": "45 days", "note": "Careful pricing"},
        {"region": "Middle East", "demand": 61, "lead_time": "35 days", "note": "Modest wear up"},
        {"region": "Asia", "demand": 49, "lead_time": "32 days", "note": "Competitive"},
    ]

    events = [
        {"date": "Feb 12", "name": "New York Fashion Week", "city": "New York", "region": "North America", "etype": "Runway", "impact": "Retail buys"},
        {"date": "Feb 20", "name": "London Fashion Week", "city": "London", "region": "Europe", "etype": "Runway", "impact": "Trend signal"},
        {"date": "Mar 03", "name": "Toronto Apparel Textile Show", "city": "Toronto", "region": "North America", "etype": "Trade", "impact": "Supplier leads"},
        {"date": "Mar 22", "name": "Dubai Modest Wear Expo", "city": "Dubai", "region": "Middle East", "etype": "Expo", "impact": "Modest demand"},
        {"date": "Apr 01", "name": "Shanghai Fashion Week", "city": "Shanghai", "region": "Asia", "etype": "Runway", "impact": "Fabric shifts"},
        {"date": "Apr 10", "name": "Dhaka Textile Summit", "city": "Dhaka", "region": "Asia", "etype": "Summit", "impact": "Sourcing"},
    ]

    experts = [
        {"name": "Lena Roy", "role": "Merch Lead, Atlas Sports", "topic": "Performance fabrics", "quote": "Buyers want smoother hand-feel and longer colorfast life."},
        {"name": "Arif Hasan", "role": "Sourcing Director, Meadow & Co.", "topic": "Lead-time planning", "quote": "Pre-booking trims is now a win for 45-day programs."},
        {"name": "Nina Park", "role": "Designer, Northline Studio", "topic": "Color direction", "quote": "Dusty pastels and deep navy are strong for FW."},
    ]

    trend_labels = ["Week 1", "Week 2", "Week 3", "Week 4", "Week 5", "Week 6"]
    trend_series = {
        "demand": [58, 60, 61, 63, 62, 64],
        "price": [52, 51, 50, 49, 48, 47],
        "lead": [30, 31, 33, 32, 31, 31],
    }

    ai_fashion_update = "AI Trend Update unavailable right now."
    if "_ai_client" in globals() and _ai_client:
        try:
            prompt = (
                "Give a short daily update about global fashion and apparel trends. "
                "Only key changes, risks, or chances for a clothing manufacturer in Bangladesh and Canada. "
                "Max 6 lines. Simple English."
            )
            response = _ai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are the trend analyst for a clothing manufacturer."},
                    {"role": "user", "content": prompt},
                ],
            )
            ai_fashion_update = response.choices[0].message.content
        except Exception:
            pass

    context = {
        "cities": cities,
        "currencies": currencies,
        "ai_fashion_update": ai_fashion_update,
        "trend_cards": trend_cards,
        "category_mix": category_mix,
        "region_signals": region_signals,
        "events": events,
        "experts": experts,
        "trend_labels_json": json.dumps(trend_labels),
        "trend_series_json": json.dumps(trend_series),
    }
    return render(request, "crm/world_dashboard.html", context)


def world_ai_fashion_news(request):
    ai_enabled = bool("_ai_client" in globals() and _ai_client)
    if request.method != "POST":
        context = {
            "ai_enabled": ai_enabled,
            "sample_notes": [
                "Green factories and safety audits remain strong selling points for Bangladesh.",
                "Activewear and lounge sets stay steady; buyers want softer hand-feel.",
                "Offer one standard fabric and one eco option when possible.",
                "For small orders, reduce color count to control costs.",
                "Repeat buyers expect faster sampling and clear QC checkpoints.",
            ],
            "signal_cards": [
                {"label": "Category heat", "value": "Activewear +6%"},
                {"label": "Price signals", "value": "Yarn softening"},
                {"label": "Lead time", "value": "Stable 40-45d"},
            ],
        }
        return render(request, "crm/world_ai_fashion_news.html", context)

    if not ai_enabled:
        return JsonResponse({"ok": False, "error": "AI is not configured on server."})

    try:
        payload = {}
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except Exception:
            payload = {}

        mode = (payload.get("mode") or "custom").strip().lower()
        region = (payload.get("region") or "Global").strip()
        category = (payload.get("category") or "All categories").strip()
        buyer = (payload.get("buyer") or "Mid-market").strip()
        price = (payload.get("price") or "Mid").strip()
        sustainability = (payload.get("sustainability") or "Balanced").strip()
        lead_time = (payload.get("lead_time") or "Standard").strip()
        intent = (payload.get("intent") or "sales").strip()

        if mode == "dashboard":
            prompt = (
                "Give a short daily update about global fashion and apparel trends. "
                "Only key changes, risks, or chances for a clothing manufacturer in Bangladesh and Canada. "
                "Max 5 bullets. Simple English."
            )
        else:
            prompt = (
                "Create a short fashion insight note for a garment manufacturer. "
                f"Region focus: {region}. Category focus: {category}. "
                f"Buyer type: {buyer}. Price tier: {price}. "
                f"Sustainability: {sustainability}. Lead time: {lead_time}. "
                f"Intent: {intent}. "
                "Return 5 bullet points plus 2 quick actions. Simple English."
            )

        response = _ai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are an assistant for Iconic Apparel House. Keep it short and useful."},
                {"role": "user", "content": prompt},
            ],
        )
        text = response.choices[0].message.content
        return JsonResponse({"ok": True, "text": text})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)})

## =====================================
# CALENDAR VIEWS
# =====================================
# Safe block for crm/views.py
# Paste this as its own section (top level, no indentation).
# Make sure crm/urls.py points to these function names.

import json
import calendar as py_calendar
from datetime import date, datetime, timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Event, EventReminderDismissal, Lead
from .forms import EventForm

# Optional OpenAI client (safe if package not installed)
_ai_client = client


def _calendar_user_name_values(user):
    names = []
    full_name = (user.get_full_name() or "").strip() if user else ""
    username = (getattr(user, "username", "") or "").strip() if user else ""
    for value in (full_name, username):
        if value and value not in names:
            names.append(value)
    return names


def _calendar_events_for_user(user):
    if not user or not getattr(user, "is_authenticated", False):
        return Event.objects.none()
    if user.is_superuser:
        return Event.objects.all()

    visibility = Q(created_by=user) | Q(attendees=user)
    email = (getattr(user, "email", "") or "").strip()
    if email:
        visibility |= Q(assigned_to_email__iexact=email)
    for name in _calendar_user_name_values(user):
        visibility |= Q(assigned_to_name__iexact=name)
    return Event.objects.filter(visibility).distinct()


def _get_calendar_event_for_user(request, pk):
    return get_object_or_404(_calendar_events_for_user(request.user), pk=pk)


def _queue_calendar_email_after_commit(event, action):
    if not event or not event.pk:
        return
    transaction.on_commit(lambda event_id=event.pk, action_name=action: queue_calendar_invite_email(event_id, action=action_name))


# ==============================
# EMAIL REMINDER HELPER
# ==============================
def send_due_event_reminders():
    """
    Send reminder emails for events that are close.
    Uses reminder_minutes_before and assigned_to_email.
    """
    now = timezone.now()

    qs = Event.objects.filter(
        reminder_minutes_before__isnull=False,
        reminder_sent=False,
    ).exclude(
        assigned_to_email__isnull=True
    ).exclude(
        assigned_to_email=""
    )

    for ev in qs:
        if not ev.start_datetime:
            continue

        delta = ev.start_datetime - now
        minutes_to_start = delta.total_seconds() / 60.0

        if 0 <= minutes_to_start <= (ev.reminder_minutes_before or 0):
            subject = f"Reminder: {ev.title}"
            msg_note = ev.note or ""
            message = f"Event starts at {timezone.localtime(ev.start_datetime)}.\n\nNote: {msg_note}"
            recipient = [ev.assigned_to_email]

            try:
                send_mail(
                    subject,
                    message,
                    getattr(settings, "DEFAULT_FROM_EMAIL", None),
                    recipient,
                    fail_silently=True,
                )
                ev.reminder_sent = True
                ev.save(update_fields=["reminder_sent"])
            except Exception:
                # keep silent so calendar page never breaks
                pass


# ==============================
# CALENDAR ADD
# ==============================
def calendar_add(request):
    """
    Create a new calendar event.
    If ?lead=ID is in the URL, prefill lead.
    Supports repeat from template field:
    none, every_day_5, every_week_4
    """
    initial_data = {}

    lead_id = request.GET.get("lead")
    if lead_id:
        try:
            initial_data["lead"] = Lead.objects.get(pk=lead_id)
        except Lead.DoesNotExist:
            pass
    production_id = request.GET.get("production")
    if production_id:
        try:
            initial_data["production"] = ProductionOrder.objects.get(pk=production_id)
        except ProductionOrder.DoesNotExist:
            pass

    if request.method == "POST":
        form = EventForm(request.POST)
        if form.is_valid():
            event = form.save(commit=False)
            event.created_by = request.user
            _hydrate_calendar_event_links(event)
            event.save()
            form.save_m2m()
            _queue_calendar_email_after_commit(event, "created")

            repeat = (request.POST.get("repeat") or "none").strip()
            extra_dates = []

            if event.start_datetime:
                if repeat == "every_day_5":
                    for i in range(1, 5):
                        extra_dates.append(event.start_datetime + timedelta(days=i))
                elif repeat == "every_week_4":
                    for i in range(1, 4):
                        extra_dates.append(event.start_datetime + timedelta(weeks=i))

            for dt_value in extra_dates:
                extra_event = Event.objects.create(
                    created_by=request.user,
                    title=event.title,
                    start_datetime=dt_value,
                    end_datetime=event.end_datetime,
                    event_type=event.event_type,
                    priority=event.priority,
                    status=event.status,
                    note=event.note,
                    location=event.location,
                    meeting_link=event.meeting_link,
                    lead=event.lead,
                    opportunity=event.opportunity,
                    customer=event.customer,
                    production=event.production,
                    assigned_to_name=event.assigned_to_name,
                    assigned_to_email=event.assigned_to_email,
                    external_attendees=event.external_attendees,
                    reminder_minutes_before=event.reminder_minutes_before,
                    production_stage=event.production_stage,
                )
                extra_event.attendees.set(event.attendees.all())
                _queue_calendar_email_after_commit(extra_event, "created")

            # AI note for first event only
            if _ai_client and not event.ai_note:
                try:
                    prompt = f"Create a short helpful follow up summary for this CRM event: {event.title}"
                    resp = _ai_client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=120,
                    )
                    event.ai_note = (resp.choices[0].message.content or "").strip()
                    event.save(update_fields=["ai_note"])
                except Exception:
                    pass

            return redirect("calendar_list")
    else:
        form = EventForm(initial=initial_data)

    return render(request, "crm/calendar_add.html", {"form": form, "calendar_link_payload": _calendar_link_payload()})


# ==============================
# CALENDAR EDIT
# ==============================
def calendar_edit(request, pk):
    event = _get_calendar_event_for_user(request, pk)
    old_signature = calendar_event_signature(event)
    old_start = event.start_datetime
    old_reminder = event.reminder_minutes_before

    if request.method == "POST":
        form = EventForm(request.POST, instance=event)
        if form.is_valid():
            event = form.save(commit=False)
            _hydrate_calendar_event_links(event)
            event.save()
            form.save_m2m()
            if old_start != event.start_datetime or old_reminder != event.reminder_minutes_before:
                event.reminder_sent = False
                event.save(update_fields=["reminder_sent"])
            if calendar_event_signature(event) != old_signature:
                _queue_calendar_email_after_commit(event, "updated")

            if _ai_client and event.status == "done" and not event.ai_note:
                try:
                    prompt = f"Summarize completed CRM event in one short note: {event.title}"
                    resp = _ai_client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=100,
                    )
                    event.ai_note = (resp.choices[0].message.content or "").strip()
                    event.save(update_fields=["ai_note"])
                except Exception:
                    pass

            return redirect("calendar_list")
    else:
        form = EventForm(instance=event)

    return render(request, "crm/calendar_edit.html", {"form": form, "event": event, "calendar_link_payload": _calendar_link_payload()})


# ==============================
# CALENDAR EVENT DETAIL
# ==============================
def calendar_event_detail(request, pk):
    event = _get_calendar_event_for_user(request, pk)

    upcoming_for_lead = []
    if getattr(event, "lead", None):
        upcoming_for_lead = (
            _calendar_events_for_user(request.user).filter(
                lead=event.lead,
                start_datetime__gte=timezone.now(),
            )
            .exclude(pk=event.pk)
            .order_by("start_datetime")[:5]
        )

    return render(
        request,
        "crm/calendar_event_detail.html",
        {
            "event": event,
            "upcoming_for_lead": upcoming_for_lead,
            "related_opportunities": _calendar_related_opportunities(event),
            "related_productions": _calendar_related_productions(event),
        },
    )


# ==============================
# CALENDAR EVENT AI
# ==============================
@require_POST
def calendar_event_ai(request, pk):
    if not _ai_client:
        return JsonResponse({"ok": False, "error": "AI is not configured."}, status=500)

    event = _get_calendar_event_for_user(request, pk)

    mode = (request.POST.get("mode") or "summary").strip()
    user_text = (request.POST.get("user_text") or "").strip()

    parts = []
    parts.append(f"Event title: {event.title}")
    parts.append(f"Type: {event.get_event_type_display() if hasattr(event, 'get_event_type_display') else event.event_type}")
    parts.append(f"Status: {event.get_status_display() if hasattr(event, 'get_status_display') else event.status}")
    parts.append(f"Priority: {event.get_priority_display() if hasattr(event, 'get_priority_display') else event.priority}")
    parts.append(f"Start time: {event.start_datetime}")
    if event.end_datetime:
        parts.append(f"End time: {event.end_datetime}")
    if event.note:
        parts.append(f"Note: {event.note}")
    if event.lead:
        parts.append(f"Lead: {event.lead.account_brand} ({event.lead.lead_id})")
    if event.customer:
        parts.append(f"Customer: {event.customer.account_brand}")

    context_text = "\n".join(parts)

    if mode == "summary":
        prompt = "Write one short summary for this calendar event. Keep it friendly and under 3 lines.\n\n" + context_text
    elif mode == "follow_up":
        prompt = "Write a short follow up message I can send to the client after this event. No greeting. 3 to 5 lines.\n\n" + context_text
    elif mode == "next_steps":
        prompt = "List 3 clear next steps for this event in bullet points.\n\n" + context_text
    elif mode == "reminder":
        prompt = "Write one short reminder note to myself about this event. Max 3 lines.\n\n" + context_text
    else:
        if not user_text:
            return JsonResponse({"ok": False, "error": "No user text provided."}, status=400)
        prompt = "You are a CRM assistant helping with one calendar event.\n\n" + context_text + "\n\nUser question: " + user_text

    try:
        resp = _ai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=220,
        )
        text = (resp.choices[0].message.content or "").strip()
        return JsonResponse({"ok": True, "text": text})
    except Exception:
        return JsonResponse({"ok": False, "error": "AI error"}, status=500)


# ==============================
# CALENDAR LIST
# ==============================
def calendar_list(request):
    """
    Main calendar page with month, week, day view and filters.
    """
    # send reminders without extra job
    send_due_event_reminders()

    today = timezone.localdate()
    current_view = request.GET.get("view", "month")

    nav = request.GET.get("nav")
    year = request.GET.get("year")
    month = request.GET.get("month")

    if nav == "today":
        current_month = today.replace(day=1)
        selected_day = today
    else:
        if year and month:
            current_month = date(int(year), int(month), 1)
        else:
            current_month = today.replace(day=1)

        selected_str = request.GET.get("day")
        if selected_str:
            try:
                selected_day = date.fromisoformat(selected_str)
            except ValueError:
                selected_day = today
        else:
            selected_day = today

        if nav == "prev":
            if current_month.month == 1:
                current_month = current_month.replace(year=current_month.year - 1, month=12)
            else:
                current_month = current_month.replace(month=current_month.month - 1)

        if nav == "next":
            if current_month.month == 12:
                current_month = current_month.replace(year=current_month.year + 1, month=1)
            else:
                current_month = current_month.replace(month=current_month.month + 1)

    first_weekday, days_in_month = py_calendar.monthrange(current_month.year, current_month.month)

    weeks = []
    week = []

    for i in range(first_weekday):
        d = date(current_month.year, current_month.month, 1) - timedelta(days=(first_weekday - i))
        week.append(d)

    for day_num in range(1, days_in_month + 1):
        d = date(current_month.year, current_month.month, day_num)
        week.append(d)
        if len(week) == 7:
            weeks.append(week)
            week = []

    if week:
        last_day = week[-1]
        while len(week) < 7:
            last_day = last_day + timedelta(days=1)
            week.append(last_day)
        weeks.append(week)

    display_week_dates = None
    for w in weeks:
        if selected_day in w:
            display_week_dates = w
            break
    if display_week_dates is None and weeks:
        display_week_dates = weeks[0]

    start_month = weeks[0][0]
    end_month = weeks[-1][-1] + timedelta(days=1)

    range_start = start_month
    range_end = end_month
    if current_view == "day":
        range_start = selected_day
        range_end = selected_day + timedelta(days=1)
    elif current_view == "week" and display_week_dates:
        range_start = display_week_dates[0]
        range_end = display_week_dates[-1] + timedelta(days=1)

    base_events_qs = _calendar_events_for_user(request.user)

    events_qs = base_events_qs.filter(
        start_datetime__date__gte=range_start,
        start_datetime__date__lt=range_end,
    ).order_by("start_datetime")

    status_filter = request.GET.get("status", "all")
    type_filter = request.GET.get("type", "all")
    priority_filter = request.GET.get("priority", "all")
    assigned_filter = request.GET.get("assigned", "all")
    lead_filter = request.GET.get("lead", "all")

    if status_filter != "all":
        events_qs = events_qs.filter(status=status_filter)
    if type_filter != "all":
        events_qs = events_qs.filter(event_type=type_filter)
    if priority_filter != "all":
        events_qs = events_qs.filter(priority=priority_filter)
    if assigned_filter != "all":
        events_qs = events_qs.filter(assigned_to_name=assigned_filter)
    if lead_filter != "all":
        events_qs = events_qs.filter(lead__id=lead_filter)

    events_by_day = {}
    for ev in events_qs.select_related("lead", "customer", "created_by").prefetch_related("attendees"):
        key = timezone.localtime(ev.start_datetime).date()
        events_by_day.setdefault(key, []).append(ev)

    month_weeks = []
    for w in weeks:
        week_days = []
        for d in w:
            day_events = events_by_day.get(d, [])
            has_overdue = any(getattr(e, "is_overdue", False) for e in day_events)
            week_days.append(
                {
                    "date": d,
                    "in_month": d.month == current_month.month,
                    "is_today": d == today,
                    "events": day_events,
                    "has_overdue": has_overdue,
                }
            )
        month_weeks.append(week_days)

    display_weeks = month_weeks
    if current_view in ["week", "day"]:
        for w in month_weeks:
            if any(cell["date"] == selected_day for cell in w):
                display_weeks = [w]
                break

    selected_day_events = events_by_day.get(selected_day, [])
    selected_day_events = sorted(
        selected_day_events,
        key=lambda e: timezone.localtime(e.start_datetime),
    )

    # time grid helpers for day/week views
    start_hour = 6
    end_hour = 22
    hour_slots = list(range(start_hour, end_hour + 1))

    day_hour_events = {h: [] for h in hour_slots}
    day_overflow_events = []
    for ev in selected_day_events:
        local_dt = timezone.localtime(ev.start_datetime)
        if local_dt.hour in day_hour_events:
            day_hour_events[local_dt.hour].append(ev)
        else:
            day_overflow_events.append(ev)

    day_hour_rows = [
        {"hour": h, "events": day_hour_events.get(h, [])} for h in hour_slots
    ]

    week_dates = display_week_dates or []

    week_hour_rows = []
    if current_view == "week" and week_dates:
        week_hour_map = {
            d: {h: [] for h in hour_slots}
            for d in week_dates
        }
        for d in week_dates:
            for ev in events_by_day.get(d, []):
                local_dt = timezone.localtime(ev.start_datetime)
                if local_dt.hour in hour_slots:
                    week_hour_map[d][local_dt.hour].append(ev)
        for h in hour_slots:
            cells = []
            for d in week_dates:
                cells.append(
                    {
                        "date": d,
                        "events": week_hour_map[d].get(h, []),
                    }
                )
            week_hour_rows.append({"hour": h, "cells": cells})

    now = timezone.now()
    today_events_count = base_events_qs.filter(start_datetime__date=today).count()
    week_events_count = base_events_qs.filter(
        start_datetime__date__gte=today,
        start_datetime__date__lte=today + timedelta(days=7),
    ).count()
    overdue_events_count = base_events_qs.filter(
        status__in=["planned", "in_work"],
        start_datetime__lt=now,
    ).count()

    assigned_choices = (
        base_events_qs.exclude(assigned_to_name__isnull=True)
        .exclude(assigned_to_name="")
        .values_list("assigned_to_name", flat=True)
        .distinct()
        .order_by("assigned_to_name")
    )

    lead_choices = (
        base_events_qs.filter(lead__isnull=False)
        .values_list("lead_id", "lead__account_brand")
        .distinct()
        .order_by("lead_id")
    )

    dismissed_event_ids = EventReminderDismissal.objects.filter(user=request.user).values_list("event_id", flat=True)
    upcoming_reminders = (
        base_events_qs.filter(
            start_datetime__gte=now,
            start_datetime__lte=now + timedelta(hours=24),
        )
        .exclude(status="done")
        .exclude(pk__in=dismissed_event_ids)
        .select_related("lead", "customer", "created_by")
        .prefetch_related("attendees")
        .order_by("start_datetime")[:5]
    )

    context = {
        "today": today,
        "current_month": current_month,
        "month_weeks": month_weeks,
        "display_weeks": display_weeks,
        "selected_day": selected_day,
        "selected_day_events": selected_day_events,
        "current_view": current_view,
        "status_filter": status_filter,
        "type_filter": type_filter,
        "priority_filter": priority_filter,
        "assigned_filter": assigned_filter,
        "lead_filter": lead_filter,
        "assigned_choices": assigned_choices,
        "lead_choices": lead_choices,
        "today_events_count": today_events_count,
        "week_events_count": week_events_count,
        "overdue_events_count": overdue_events_count,
        "hour_slots": hour_slots,
        "day_hour_rows": day_hour_rows,
        "day_overflow_events": day_overflow_events,
        "week_dates": week_dates,
        "week_hour_rows": week_hour_rows,
        "upcoming_reminders": upcoming_reminders,
    }

    return render(request, "crm/calendar_list.html", context)


# ==============================
# TOGGLE DONE
# ==============================
@require_POST
def calendar_toggle_done(request, pk):
    event = _get_calendar_event_for_user(request, pk)
    event.status = "done"
    event.save(update_fields=["status"])
    return JsonResponse({"ok": True})


@require_POST
def calendar_dismiss_reminder(request, pk):
    event = _get_calendar_event_for_user(request, pk)
    EventReminderDismissal.objects.get_or_create(user=request.user, event=event)
    return JsonResponse({"ok": True})


# ==============================
# DRAG UPDATE
# ==============================
@require_POST
def calendar_drag_update(request):
    try:
        data = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Bad JSON"}, status=400)

    event_id = data.get("event_id")
    new_date_str = data.get("new_date")
    new_time_str = data.get("new_time")

    if not event_id or not new_date_str:
        return JsonResponse({"ok": False, "error": "Missing data"}, status=400)

    event = _get_calendar_event_for_user(request, event_id)

    try:
        new_date = datetime.strptime(new_date_str, "%Y-%m-%d").date()
    except ValueError:
        return JsonResponse({"ok": False, "error": "Bad date"}, status=400)

    old_start = event.start_datetime
    if not old_start:
        return JsonResponse({"ok": False, "error": "Event has no start time"}, status=400)

    new_time = old_start.time()
    if new_time_str:
        try:
            new_time = datetime.strptime(new_time_str, "%H:%M").time()
        except ValueError:
            return JsonResponse({"ok": False, "error": "Bad time"}, status=400)

    naive_new_start = datetime.combine(new_date, new_time)

    if timezone.is_naive(naive_new_start):
        new_start = timezone.make_aware(naive_new_start, timezone.get_current_timezone())
    else:
        new_start = naive_new_start

    event.start_datetime = new_start

    if event.end_datetime:
        duration = event.end_datetime - old_start
        event.end_datetime = new_start + duration

    event.save(update_fields=["start_datetime", "end_datetime"])
    _queue_calendar_email_after_commit(event, "updated")
    return JsonResponse({"ok": True})

    # ---------- PRODUCTION VIEWS ----------

import logging

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.utils import timezone
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .models import (
        Lead,
        Opportunity,
        Product,
        Customer,
        Event,
        ProductionOrder,
        ProductionStage,
        # if you have these in models, keep them:
        # LeadActivity,
        # LeadComment,
    )

from .production_forms import ProductionOrderForm, ProductionStageForm

logger = logging.getLogger(__name__)

# fixed stage order for all screens and logic
STAGE_ORDER = {
    "development": 1,
    "sampling": 2,
    "cutting": 3,
    "sewing": 4,
    "ironing": 5,
    "qc": 6,
    "finishing": 7,
    "packing": 8,
    "shipping": 9,
}


def get_sorted_stages(order):
    """
    Return stages for this order in a fixed order.
    This keeps the stage bar stable on list and detail pages.
    """
    stages = list(order.stages.all())
    stages.sort(key=lambda s: STAGE_ORDER.get(s.stage_key, 99))
    return stages

    # ==============================
    # PRODUCTION HELPERS
    # ==============================

    def ensure_production_stages():
        """
        For the new design, each order has its own stages
        created by the signal in models.
        Right now this helper does nothing heavy.
        """
        return

    # ==============================
    # PRODUCTION LIST WITH DASHBOARD
    # ==============================

    def production_list(request):
        orders = (
            ProductionOrder.objects
            .select_related("customer", "product")
            .order_by("-created_at")
        )

        orders_data = []

        for order in orders:
            stages = get_sorted_stages(order)

            total_stages = len(stages)
            done_count = len([s for s in stages if s.status == "done"])
            percent_done = int((done_count / total_stages) * 100) if total_stages else 0

            has_delay = any(s.status == "delay" or s.is_late for s in stages)

            orders_data.append(
                {
                    "order": order,
                    "stages": stages,
                    "percent_done": percent_done,
                    "has_delay": has_delay,
                }
            )

        total_orders = orders.count()
        active_orders = orders.exclude(status="done").count()
        delayed_orders = len([row for row in orders_data if row["has_delay"]])
        total_pieces = sum(o.qty_total for o in orders)
        total_reject = sum(o.qty_reject for o in orders)
        reject_percent = int((total_reject / total_pieces) * 100) if total_pieces else 0

        return render(
            request,
            "crm/production_list.html",
            {
                "orders_data": orders_data,
                "total_orders": total_orders,
                "active_orders": active_orders,
                "delayed_orders": delayed_orders,
                "total_pieces": total_pieces,
                "total_reject": total_reject,
                "reject_percent": reject_percent,
            },
        )

    # ==============================
    # ADD AND EDIT PRODUCTION ORDER
    # ==============================

    def production_add(request):
        if request.method == "POST":
            form = ProductionOrderForm(request.POST, request.FILES)
            if form.is_valid():
                order = form.save()
                messages.success(request, "Production order created.")
                return redirect("production_detail", pk=order.pk)
        else:
            form = ProductionOrderForm()

        return render(
            request,
            "crm/production_add.html",
            {
                "form": form,
                "is_edit": False,
                "order": None,
            },
        )

    def production_edit(request, pk):
        order = get_object_or_404(ProductionOrder, pk=pk)

        if request.method == "POST":
            form = ProductionOrderForm(request.POST, request.FILES, instance=order)
            if form.is_valid():
                form.save()
                messages.success(request, "Production order updated.")
                return redirect("production_detail", pk=pk)
        else:
            form = ProductionOrderForm(instance=order)

        return render(
            request,
            "crm/production_add.html",  # same layout for add and edit
            {
                "form": form,
                "is_edit": True,
                "order": order,
            },
        )

    # ==============================
    # PRODUCTION DETAIL PAGE
    # ==============================
    # ==============================
    # PRODUCTION STAGE FLOW FIX
    # Put this section ONCE in crm/views.py
    # Remove all duplicate copies of these functions from your file
    # ==============================

    from decimal import Decimal

    from django.contrib import messages
    from django.db.models import Case, IntegerField, When
    from django.shortcuts import get_object_or_404, redirect, render
    from django.utils import timezone
    from django.views.decorators.http import require_POST

    from .models import Opportunity, ProductionOrder, ProductionStage
    from .production_forms import ProductionOrderForm, ProductionStageForm

    # One fixed stage order used everywhere
    STAGE_FLOW_ORDER = [
        "development",
        "sampling",
        "cutting",
        "sewing",
        "ironing",
        "qc",
        "finishing",
        "packing",
        "shipping",
    ]

    def _ordered_stages_qs(order_id):
        whens = [When(stage_key=key, then=idx) for idx, key in enumerate(STAGE_FLOW_ORDER)]
        return (
            ProductionStage.objects.filter(order_id=order_id)
            .annotate(_sort=Case(*whens, default=999, output_field=IntegerField()))
            .order_by("_sort", "id")
        )

    def _sync_order_status(order):
        stages = order.stages.all()

        if stages and all(s.status == "done" for s in stages):
            order.status = "done"
        elif any(s.status == "in_progress" for s in stages):
            order.status = "in_progress"
        else:
            order.status = "planning"

        order.save(update_fields=["status"])

    def production_detail(request, pk):
        order = get_object_or_404(ProductionOrder, pk=pk)

        # Always show stages in correct order
        stages = _ordered_stages_qs(order.pk)

        # You already have this helper somewhere else in views.py
        size_grid, size_total = build_size_grid(order)

        attachments = order.attachments.all().order_by("-created_at")
        shipments = order.shipments.all().order_by("-ship_date", "-created_at")

        shipping_cost_bdt_total = Decimal("0")
        shipping_cost_cad_total = Decimal("0")
        for s in shipments:
            shipping_cost_bdt_total += s.cost_bdt or Decimal("0")
            shipping_cost_cad_total += s.cost_cad or Decimal("0")

        context = {
            "order": order,
            "stages": stages,
            "percent_done": order.percent_done,
            "order_delayed": order.is_delayed,
            "size_grid": size_grid,
            "size_total": size_total,
            "attachments": attachments,
            "shipments": shipments,
            "shipping_cost_bdt_total": shipping_cost_bdt_total,
            "shipping_cost_cad_total": shipping_cost_cad_total,
        }
        return render(request, "crm/production_detail.html", context)

    @require_POST
    def production_stage_click(request, stage_id):
        """
        This is the button action:
        planned -> in_progress (sets actual_start)
        in_progress -> done (sets actual_end)
        done -> no change
        """
        stage = get_object_or_404(ProductionStage, pk=stage_id)
        today = timezone.localdate()

        if stage.status in [None, "", "planned"]:
            stage.status = "in_progress"
            if not stage.actual_start:
                stage.actual_start = today
            stage.save(update_fields=["status", "actual_start"])
            _sync_order_status(stage.order)
            messages.success(request, "Stage started and date saved.")
            return redirect("production_detail", pk=stage.order_id)

        if stage.status == "in_progress":
            stage.status = "done"
            if not stage.actual_start:
                stage.actual_start = today
            if not stage.actual_end:
                stage.actual_end = today
            stage.save(update_fields=["status", "actual_start", "actual_end"])
            _sync_order_status(stage.order)
            messages.success(request, "Stage completed and date saved.")
            return redirect("production_detail", pk=stage.order_id)

        messages.info(request, "Stage is already done.")
        return redirect("production_detail", pk=stage.order_id)

    def production_stage_edit(request, stage_id):
        """
        Manual edit page. Also auto sets dates if status changes.
        """
        stage = get_object_or_404(ProductionStage, pk=stage_id)
        today = timezone.localdate()

        if request.method == "POST":
            form = ProductionStageForm(request.POST, instance=stage)
            if form.is_valid():
                obj = form.save(commit=False)

                if obj.status in ["in_progress", "done"] and not obj.actual_start:
                    obj.actual_start = today

                if obj.status == "done" and not obj.actual_end:
                    obj.actual_end = today

                obj.save()
                _sync_order_status(obj.order)
                messages.success(request, "Stage updated.")
                return redirect("production_detail", pk=obj.order_id)
        else:
            form = ProductionStageForm(instance=stage)

        return render(
            request,
            "crm/production_stage_edit.html",
            {"stage": stage, "form": form},
        )

    def production_from_opportunity(request, pk):
        opportunity = get_object_or_404(Opportunity, pk=pk)

        po = ProductionOrder.objects.filter(opportunity=opportunity).first()
        if not po:
            account = (
                getattr(opportunity.lead, "account_brand", "")
                or getattr(opportunity.customer, "account_brand", "")
                or "Customer"
            )
            title = f"{account} order for {opportunity.opportunity_id}"
            qty_guess = opportunity.moq_units or 0
            po = ProductionOrder.objects.create(
                opportunity=opportunity,
                title=title,
                qty_total=qty_guess,
            )

        return redirect("production_detail", pk=po.pk)

# ==============================
# AI HELP FOR OPPORTUNITY
# ==============================

@require_POST
def opportunity_ai_detail(request, pk):
    """
    AI helper for a single opportunity.
    This is called from the opportunity page.
    """
    opportunity = get_object_or_404(Opportunity, pk=pk)
    lead = opportunity.lead

    customer = opportunity.customer or (lead.customer if lead and lead.customer_id else None)

    mode = request.POST.get("mode", "summary")
    user_text = request.POST.get("user_text", "")
    email_body = request.POST.get("email_body", "")

    # lead info
    if lead:
        lead_info = (
            f"Brand: {lead.account_brand}. "
            f"Contact: {lead.contact_name}. "
            f"Email: {lead.email}. "
            f"Phone: {lead.phone}. "
            f"Market: {getattr(lead, 'market', '')}. "
            f"Lead type: {getattr(lead, 'lead_type', '')}. "
            f"Budget: {getattr(lead, 'budget', '')}. "
            f"Order quantity: {getattr(lead, 'order_quantity', '')}. "
        )
    else:
        lead_info = "No lead linked. This opportunity was created directly from a customer. "

    # opportunity info
    opp_info = (
        f"Opportunity id: {opportunity.opportunity_id}. "
        f"Stage: {opportunity.stage}. "
        f"Product type: {opportunity.product_type}. "
        f"Product category: {opportunity.product_category}. "
        f"MOQ units: {opportunity.moq_units}. "
        f"Order value: {opportunity.order_value}. "
        f"Open: {opportunity.is_open}. "
        f"Notes: {opportunity.notes[:300] if opportunity.notes else 'None'}. "
    )

    # customer info
    if customer:
        cust_info = (
            f"Customer code: {customer.customer_code}. "
            f"Shipping city: {customer.shipping_city}. "
            f"Shipping country: {customer.shipping_country}. "
        )
    else:
        cust_info = "No customer record yet for this lead. "

    base_context = (
        "You are the sales brain of a clothing factory CRM. "
        "Be short, clear and practical. "
        "Focus on actions that help close the deal.\n\n"
        "Lead info: " + lead_info + "\n"
        "Opportunity info: " + opp_info + "\n"
        "Customer info: " + cust_info + "\n"
    )

    # choose mode
    if mode == "summary":
        user_prompt = base_context + "Give a short summary of this opportunity in 8 lines."
    elif mode == "next_steps":
        user_prompt = base_context + "Give clear next steps with timeline and owner."
    elif mode == "risk":
        user_prompt = base_context + "Rate the deal risk and list three warning signs."
    elif mode == "products":
        user_prompt = base_context + "Suggest 3 product ideas with GSM and fabric style."
    elif mode == "timeline":
        user_prompt = base_context + "Give a simple timeline from now to shipment in under 10 lines."
    elif mode == "email_followup":
        extra = f"\nUser notes:\n{email_body}\n" if email_body else ""
        user_prompt = (
            base_context
            + extra
            + "Write a simple friendly follow up email with a clear call to action."
        )
    elif mode == "chat":
        if not user_text:
            return JsonResponse({"ok": False, "error": "Type a question first."})
        user_prompt = base_context + f"Answer this question clearly:\n{user_text}"
    else:
        user_prompt = base_context + "Give a helpful summary of what to do next."

    # call OpenAI
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert CRM assistant for a clothing factory.",
                },
                {"role": "user", "content": user_prompt},
            ],
        )
        ai_text = resp.choices[0].message.content
    except Exception as e:
        logger.exception("AI opportunity helper failed")
        return JsonResponse({"ok": False, "error": str(e)})

    # try to save as a comment if model exists
    try:
        from .models import LeadComment  # safe local import
        if lead:
            LeadComment.objects.create(
                lead=lead,
                opportunity=opportunity,
                author="AI",
                content=ai_text,
                is_ai=True,
            )
    except Exception:
        # if model does not exist just skip
        pass

    return JsonResponse({"ok": True, "text": ai_text})
# ==============================
# EDIT OPPORTUNITY
# ==============================

def opportunity_edit(request, pk):
    """
    Simple edit page for the main fields of an opportunity.
    This is the view used by urls.py for 'opportunities/<pk>/edit/'.
    """
    opportunity = get_object_or_404(
        scope_sales_opportunities(Opportunity.objects.select_related("lead", "customer"), request.user),
        pk=pk,
    )

    product_type_choices = Opportunity.PRODUCT_TYPE_CHOICES
    product_category_choices = Opportunity.PRODUCT_CATEGORY_CHOICES
    product_type_keys = {k for k, _ in product_type_choices}
    product_category_keys = {k for k, _ in product_category_choices}
    order_currency_choices = Opportunity.ORDER_CURRENCY_CHOICES
    order_currency_keys = {k for k, _ in order_currency_choices}
    selected_order_currency = (getattr(opportunity, "order_currency", "") or "CAD").upper()
    if selected_order_currency not in order_currency_keys:
        selected_order_currency = "CAD"
    can_edit_historical_dates_flag = can_edit_historical_dates(request.user)
    account_label = (
        getattr(opportunity.lead, "account_brand", "")
        or (getattr(opportunity.customer, "account_brand", "") if opportunity.customer_id else "")
        or "Not linked"
    )

    if request.method == "POST":
        # very basic safe update
        product_type = request.POST.get("product_type") or opportunity.product_type
        product_category = request.POST.get("product_category") or opportunity.product_category
        order_currency = (request.POST.get("order_currency") or selected_order_currency).upper()

        if product_type in product_type_keys:
            opportunity.product_type = product_type
        if product_category in product_category_keys:
            opportunity.product_category = product_category
        if order_currency in order_currency_keys:
            opportunity.order_currency = order_currency
        else:
            opportunity.order_currency = "CAD"

        moq_raw = request.POST.get("moq_units")
        if moq_raw:
            try:
                opportunity.moq_units = int(moq_raw)
            except ValueError:
                pass

        order_value_raw = request.POST.get("order_value")
        order_value_usd_raw = request.POST.get("order_value_usd")
        fx_rate_raw = request.POST.get("fx_rate_bdt_per_usd")

        order_value = _safe_decimal_or_none(order_value_raw)
        order_value_usd = _safe_decimal_or_none(order_value_usd_raw)
        fx_rate = _safe_decimal_or_none(fx_rate_raw)

        if order_value_usd is not None:
            opportunity.order_value_usd = order_value_usd
        if fx_rate is not None:
            opportunity.fx_rate_bdt_per_usd = fx_rate

        if order_value_usd is not None:
            order_value = _calc_order_value_bdt(order_value_usd, fx_rate, opportunity.order_currency)

        if order_value is not None:
            opportunity.order_value = order_value

        if can_edit_historical_dates_flag:
            opportunity_date_raw = (request.POST.get("opportunity_date") or "").strip()
            if opportunity_date_raw:
                opportunity_date = parse_date(opportunity_date_raw)
                if opportunity_date is None:
                    messages.error(request, "Please enter a valid opportunity date.")
                    return redirect("opportunity_edit", pk=pk)
                opportunity.opportunity_date = opportunity_date
            else:
                opportunity.opportunity_date = None

        opportunity.notes = request.POST.get("notes") or opportunity.notes

        opportunity.save()
        messages.success(request, "Opportunity updated.")
        return redirect("opportunity_detail", pk=pk)

    return render(
        request,
        "crm/opportunity_edit.html",
        {
            "opportunity": opportunity,
            "product_type_choices": product_type_choices,
            "product_category_choices": product_category_choices,
            "order_currency_choices": order_currency_choices,
            "selected_order_currency": selected_order_currency,
            "account_label": account_label,
            "currency_summary": _opportunity_currency_summary(opportunity),
            "bdt_per_piece": _opportunity_currency_summary(opportunity)["bdt_per_piece"],
            "can_edit_historical_dates": can_edit_historical_dates_flag,
        },
    )


@require_POST
def opportunity_delete(request, pk):
    opportunity = get_object_or_404(
        scope_sales_opportunities(Opportunity.objects.select_related("lead"), request.user),
        pk=pk,
    )
    if not _can_archive_workflow_record(request.user):
        messages.error(request, "You do not have permission to archive or delete opportunities.")
        return redirect("opportunity_detail", pk=pk)

    requested_action = (request.POST.get("workflow_action") or "archive").strip().lower()
    linked_labels = _opportunity_linked_record_labels(opportunity)
    opp_id = opportunity.opportunity_id

    _archive_workflow_record(opportunity, request.user)
    _log_workflow_safety_action(
        request,
        action="archive",
        record=opportunity,
        message=f"Opportunity {opp_id} archived.",
        meta={"linked_records": linked_labels},
    )
    lead = opportunity.lead
    _log_lead_workflow_note(lead, request.user, f"Opportunity {opp_id} archived by {_user_display_name(request.user)}.")
    _record_customer_event(
        customer=opportunity.customer or getattr(lead, "customer", None),
        event_type="opportunity_created",
        title="Opportunity archived",
        details=f"Opportunity {opp_id} archived by {_user_display_name(request.user)}.",
        opportunity=opportunity,
    )
    if linked_labels:
        messages.warning(
            request,
            f"Opportunity {opp_id} archived. Linked records were preserved: {', '.join(linked_labels)}.",
        )
    else:
        messages.success(request, f"Opportunity {opp_id} archived. History is preserved.")
    return redirect(request.POST.get("next") or "opportunities_list")
# ==============================
# PRODUCTION VIEWS
# ==============================

import logging

from django.utils import timezone
from django.contrib import messages
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .models import (
    ProductionOrder,
    ProductionOrderAttachment,
    ProductionProgressPhoto,
    ProductionStage,
    Opportunity,
)
from .production_forms import ProductionOrderForm, ProductionStageForm

logger = logging.getLogger(__name__)

# fixed order for stages in all screens
STAGE_ORDER = {
    "development": 1,
    "sampling": 2,
    "cutting": 3,
    "sewing": 4,
    "ironing": 5,
    "qc": 6,
    "finishing": 7,
    "packing": 8,
    "shipping": 9,
}


def get_sorted_stages(order):
    """
    Return stages for this order in a fixed order.
    """
    try:
        stages = list(order.stages.all())
        stages.sort(key=lambda s: STAGE_ORDER.get(s.stage_key, 99))
        return stages
    except (OperationalError, ProgrammingError, AttributeError):
        return []


# production status change helper
def _apply_production_status_change(order, old_status):
    if order.status == old_status:
        return

    sync_operational_status(order)

    customer = order.customer or (order.opportunity.customer if order.opportunity else None)
    _record_customer_event(
        customer=customer,
        event_type="production_status",
        title="Production status updated",
        details=f"Production {order.purchase_order_number or order.pk} is now {order.get_status_display()}.",
        opportunity=order.opportunity,
        production=order,
    )

    if order.status in {"done", "completed"} and order.opportunity:
        order.opportunity.stage = "Closed Won"
        order.opportunity.save(update_fields=["stage"])
        _record_customer_event(
            customer=customer,
            event_type="production_completed",
            title="Production completed",
            details=f"Production {order.purchase_order_number or order.pk} marked completed.",
            opportunity=order.opportunity,
            production=order,
        )
    elif order.status == "closed_won" and order.opportunity:
        order.opportunity.stage = "Closed Won"
        order.opportunity.save(update_fields=["stage"])
        _record_customer_event(
            customer=customer,
            event_type="production_closed_won",
            title="Production closed won",
            details=f"Production {order.purchase_order_number or order.pk} closed won.",
            opportunity=order.opportunity,
            production=order,
        )
    elif order.status == "closed_lost" and order.opportunity:
        order.opportunity.stage = "Closed Lost"
        order.opportunity.save(update_fields=["stage"])
        _record_customer_event(
            customer=customer,
            event_type="production_closed_lost",
            title="Production closed lost",
            details=f"Production {order.purchase_order_number or order.pk} closed lost.",
            opportunity=order.opportunity,
            production=order,
        )

# size grid helpers

SIZE_LABELS = [
    "YXS",
    "YS",
    "YM",
    "YL",
    "YXL",
    "XS",
    "S",
    "M",
    "L",
    "XL",
    "2XL",
    "3XL",
    "4XL",
    "5XL",
]

SIZE_GROUP_CHOICES = ProductionOrder.SIZE_GROUP_CHOICES
DEFAULT_SIZE_GROUP = "unisex"
SIZE_GROUP_LABELS = dict(SIZE_GROUP_CHOICES)
VALID_SIZE_GROUPS = {key for key, _ in SIZE_GROUP_CHOICES}


def normalize_size_group(value):
    value = (value or DEFAULT_SIZE_GROUP).strip().lower()
    return value if value in VALID_SIZE_GROUPS else DEFAULT_SIZE_GROUP


def _size_pattern(label):
    import re

    return re.compile(
        r"(?<![A-Z0-9])"
        + re.escape(label)
        + r"(?![A-Z0-9])\s*[:=]?\s*(\d+)",
        re.IGNORECASE,
    )


def extract_size_notes_from_text(text):
    """
    Preserve any non-size instructions stored beside the size quantities.
    """
    raw_text = text or ""
    if not raw_text.strip():
        return ""

    cleaned = raw_text
    for label in SIZE_LABELS:
        cleaned = _size_pattern(label).sub("", cleaned)

    notes = []
    for chunk in cleaned.replace(",", "\n").replace(";", "\n").splitlines():
        note = chunk.strip(" \t:-=,;")
        if note:
            notes.append(note)
    return "\n".join(notes)


def build_size_grid_from_text(text):
    """
    Build a size grid from a size ratio string.
    Example text: 'XS 10, S 20, M 40, L 30'
    """
    text = (text or "").upper()
    result = []
    total = 0

    for label in SIZE_LABELS:
        qty = 0
        if text:
            m = _size_pattern(label).search(text)
            if m:
                qty = int(m.group(1))
        result.append({"label": label, "qty": qty if qty else None})
        total += qty

    if total == 0:
        total = None

    return result, total


def build_size_grid(order):
    """
    Read size_ratio_note and build a small size grid.
    """
    return build_size_grid_from_text(order.size_ratio_note)


def _production_line_quantity_from_text(size_ratio_note):
    _, size_total = build_size_grid_from_text(size_ratio_note)
    return size_total


def _clean_production_line_quantity(value, size_ratio_note=""):
    size_total = _production_line_quantity_from_text(size_ratio_note)
    if size_total is not None:
        return int(size_total)
    if value in ("", None):
        return None
    try:
        quantity = int(value)
    except (TypeError, ValueError):
        return None
    return quantity if quantity >= 0 else None


def _build_order_line_dict(
    style_name="",
    color_info="",
    quantity=None,
    size_group=DEFAULT_SIZE_GROUP,
    size_ratio_note="",
    accessories_note="",
    packaging_note="",
    extra_order_note="",
):
    size_group = normalize_size_group(size_group)
    size_grid, size_total = build_size_grid_from_text(size_ratio_note)
    size_items = [item for item in size_grid if item.get("qty")]
    normalized_quantity = quantity if quantity is not None else size_total
    return {
        "style_name": style_name,
        "color_info": color_info,
        "quantity": normalized_quantity,
        "size_group": size_group,
        "size_group_display": SIZE_GROUP_LABELS.get(size_group, "Unisex"),
        "size_ratio_note": size_ratio_note,
        "size_grid": size_grid,
        "size_items": size_items,
        "size_total": size_total,
        "size_notes": extract_size_notes_from_text(size_ratio_note),
        "accessories_note": accessories_note,
        "packaging_note": packaging_note,
        "extra_order_note": extra_order_note,
    }


def _production_order_lines(order):
    if ProductionOrderLine is None or not hasattr(order, "lines"):
        lines = []
    else:
        try:
            lines = list(order.lines.all())
            lines.sort(key=lambda line: (line.line_no, line.id))
        except (OperationalError, ProgrammingError, AttributeError):
            lines = []
    if lines:
        return [
            _build_order_line_dict(
                style_name=line.style_name,
                color_info=line.color_info,
                quantity=line.quantity,
                size_group=line.size_group,
                size_ratio_note=line.size_ratio_note,
                accessories_note=line.accessories_note,
                packaging_note=line.packaging_note,
                extra_order_note=line.extra_order_note,
            )
            for line in lines
        ]

    return [
        _build_order_line_dict(
            style_name=order.style_name,
            color_info=order.color_info,
            quantity=order.qty_total or None,
            size_group=order.size_group,
            size_ratio_note=order.size_ratio_note,
            accessories_note=order.accessories_note,
            packaging_note=order.packaging_note,
            extra_order_note=order.extra_order_note,
        )
    ]


def _production_order_lines_for_form(order=None):
    if not order:
        return [_build_order_line_dict()]
    return _production_order_lines(order)


def _production_order_lines_from_payload(raw):
    lines = _parse_production_lines_payload(raw)
    if not lines:
        return None
    return [
        _build_order_line_dict(
            style_name=line.get("style_name", ""),
            color_info=line.get("color_info", ""),
            quantity=line.get("quantity"),
            size_group=line.get("size_group", DEFAULT_SIZE_GROUP),
            size_ratio_note=line.get("size_ratio_note", ""),
            accessories_note=line.get("accessories_note", ""),
            packaging_note=line.get("packaging_note", ""),
            extra_order_note=line.get("extra_order_note", ""),
        )
        for line in lines
    ]


# library helpers for production edit/add
def _parse_id_list(raw):
    if raw is None:
        return None
    raw = (raw or "").strip()
    if raw == "":
        return []
    ids = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if part.isdigit():
            ids.append(int(part))
    return ids


def _apply_production_library_links(order, request):
    """
    Update production order m2m links based on hidden id lists in the form.
    """
    mapping = [
        ("fabrics", Fabric, "fabric_ids"),
        ("accessories", Accessory, "accessory_ids"),
        ("trims", Trim, "trim_ids"),
        ("threads", ThreadOption, "thread_ids"),
    ]

    for field_name, model_cls, param in mapping:
        raw_ids = _parse_id_list(request.POST.get(param))
        if raw_ids is None:
            continue
        try:
            qs = model_cls.objects.filter(pk__in=raw_ids)
            getattr(order, field_name).set(qs)
        except (AttributeError, OperationalError, ProgrammingError):
            continue


def _parse_production_lines_payload(raw):
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    if not isinstance(payload, list):
        return None
    cleaned = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        # These fields are posted outside the ModelForm, so enforce the DB max length here.
        style_name = (item.get("style_name") or "").strip()[:200]
        color_info = (item.get("color_info") or "").strip()[:200]
        size_group = normalize_size_group(item.get("size_group"))
        size_ratio_note = (item.get("size_ratio_note") or "").strip()
        quantity = _clean_production_line_quantity(item.get("quantity"), size_ratio_note)
        accessories_note = (item.get("accessories_note") or "").strip()
        packaging_note = (item.get("packaging_note") or "").strip()
        extra_order_note = (item.get("extra_order_note") or "").strip()
        if not any([style_name, color_info, quantity, size_ratio_note, accessories_note, packaging_note, extra_order_note]):
            continue
        cleaned.append(
            {
                "style_name": style_name,
                "color_info": color_info,
                "quantity": quantity,
                "size_group": size_group,
                "size_ratio_note": size_ratio_note,
                "accessories_note": accessories_note,
                "packaging_note": packaging_note,
                "extra_order_note": extra_order_note,
            }
        )
    return cleaned


def _save_production_lines(order, request):
    if ProductionOrderLine is None or not hasattr(order, "lines"):
        return
    raw = request.POST.get("line_payload")
    lines = _parse_production_lines_payload(raw)
    if lines is None:
        return
    try:
        order.lines.all().delete()
        for idx, data in enumerate(lines, start=1):
            ProductionOrderLine.objects.create(order=order, line_no=idx, **data)
    except (AttributeError, DataError, IntegrityError, OperationalError, ProgrammingError):
        return

    if lines:
        first = lines[0]
        updates = {}
        for field in [
            "style_name",
            "color_info",
            "size_group",
            "size_ratio_note",
            "accessories_note",
            "packaging_note",
            "extra_order_note",
        ]:
            if getattr(order, field) != first.get(field, ""):
                updates[field] = first.get(field, "")
        if updates:
            try:
                for key, val in updates.items():
                    setattr(order, key, val)
                order.save(update_fields=list(updates.keys()))
            except (DataError, IntegrityError, OperationalError, ProgrammingError):
                return


def _production_library_context(order=None):
    def _safe_related_list(manager_name):
        if not order:
            return []
        try:
            return list(getattr(order, manager_name).all())
        except (AttributeError, OperationalError, ProgrammingError):
            return []

    def _safe_library_queryset(model, order_by_field, limit=None):
        try:
            qs = model.objects.filter(is_active=True).order_by(order_by_field)
            qs = qs[:limit] if limit else qs
            return list(qs)
        except (OperationalError, ProgrammingError):
            return []

    selected_fabrics = _safe_related_list("fabrics")
    selected_accessories = _safe_related_list("accessories")
    selected_trims = _safe_related_list("trims")
    selected_threads = _safe_related_list("threads")

    return {
        "library_products": _safe_library_queryset(Product, "name"),
        "library_fabrics": _safe_library_queryset(Fabric, "name", limit=200),
        "library_accessories": _safe_library_queryset(Accessory, "name", limit=200),
        "library_trims": _safe_library_queryset(Trim, "name", limit=200),
        "library_threads": _safe_library_queryset(ThreadOption, "name", limit=200),
        "selected_fabrics": selected_fabrics,
        "selected_accessories": selected_accessories,
        "selected_trims": selected_trims,
        "selected_threads": selected_threads,
        "selected_fabric_ids": [str(f.pk) for f in selected_fabrics],
        "selected_accessory_ids": [str(a.pk) for a in selected_accessories],
        "selected_trim_ids": [str(t.pk) for t in selected_trims],
        "selected_thread_ids": [str(t.pk) for t in selected_threads],
    }


def _production_inventory_context(order):
    try:
        materials = list(order.materials.select_related("inventory_item"))
    except (AttributeError, OperationalError, ProgrammingError):
        materials = []

    recommended_items = []
    recommended_ids = []
    try:
        if order.fabrics.exists() or order.accessories.exists() or order.trims.exists() or order.threads.exists():
            recommended_items = list(
                InventoryItem.objects.filter(
                    Q(fabric__in=order.fabrics.all())
                    | Q(accessory__in=order.accessories.all())
                    | Q(trim__in=order.trims.all())
                    | Q(thread_option__in=order.threads.all())
                ).distinct()
            )
        recommended_ids = [str(item.pk) for item in recommended_items]
    except (AttributeError, OperationalError, ProgrammingError):
        recommended_items = []
        recommended_ids = []

    try:
        inventory_items = list(
            InventoryItem.objects.filter(is_active=True).order_by("material_group", "category", "name")
        )
    except (OperationalError, ProgrammingError):
        inventory_items = []
    try:
        material_movements = list(
            InventoryMovement.objects.filter(production_order=order)
            .select_related("inventory_item", "created_by")
            .order_by("-created_at", "-id")[:20]
        )
    except (OperationalError, ProgrammingError):
        material_movements = []

    return {
        "materials": materials,
        "recommended_items": recommended_items,
        "recommended_ids": recommended_ids,
        "inventory_items": inventory_items,
        "material_movements": material_movements,
    }


def production_list(request):
    """
    List of all production orders with small dashboard numbers.
    """
    today = timezone.localdate()
    if request.method == "POST":
        order_id = request.POST.get("order_id")
        new_operational_status = request.POST.get("operational_status")
        new_status = request.POST.get("status")
        valid_operational_statuses = {key for key, _ in ProductionOrder.OPERATIONAL_STATUS_CHOICES}
        valid_statuses = {key for key, _ in ProductionOrder.STATUS_CHOICES}

        if order_id and new_operational_status in valid_operational_statuses:
            order = get_object_or_404(
                scope_production_orders(ProductionOrder.objects.all(), request.user),
                pk=order_id,
            )
            if new_operational_status != order.operational_status:
                sync_operational_status(order, explicit_status=new_operational_status)
                messages.success(
                    request,
                    f"Workflow status updated to {order.get_operational_status_display()}.",
                )
        elif order_id and new_status in valid_statuses:
            order = get_object_or_404(
                scope_production_orders(ProductionOrder.objects.all(), request.user),
                pk=order_id,
            )
            old_status = order.status
            if new_status != old_status:
                order.status = new_status
                order.save(update_fields=["status"])
                _apply_production_status_change(order, old_status)
                messages.success(request, f"Status updated to {order.get_status_display()}.")

        return redirect(request.POST.get("next") or "production_list")

    archive_filter = (request.GET.get("archive") or "active").strip().lower()
    status_filter = (
        request.GET.get("status")
        or ("all" if archive_filter == "archived" else "active")
    ).strip().lower()
    if status_filter == "archived":
        archive_filter = "archived"
    elif status_filter == "all" and "archive" not in request.GET:
        archive_filter = "all"
    search_query = (request.GET.get("q") or "").strip()
    priority_filter = (request.GET.get("priority") or "all").strip().lower()
    delayed_filter = (request.GET.get("delayed") or "all").strip().lower()
    shipment_filter = (request.GET.get("shipment") or "all").strip().lower()

    orders = (
        ProductionOrder.objects
        .select_related(
            "customer",
            "product",
            "opportunity",
            "opportunity__lead",
            "opportunity__lead__assigned_to",
            "lead",
            "lead__assigned_to",
            "assigned_production_manager",
            "created_by",
        )
        .annotate(
            list_has_inventory_allocations=Exists(
                ProductionOrderMaterial.objects.filter(order_id=OuterRef("pk"))
            ),
            list_has_invoices=Exists(
                Invoice.objects.filter(order_id=OuterRef("pk"))
            ),
            list_has_accounting_records=Exists(
                AccountingEntry.objects.filter(production_order_id=OuterRef("pk"))
            ),
        )
        .prefetch_related("stages", "shipments", "order_lifecycles")
        .order_by("-created_at")
    )
    orders = scope_production_orders(orders, request.user)

    if archive_filter == "archived":
        orders = orders.filter(is_archived=True)
    elif archive_filter != "all":
        orders = orders.filter(is_archived=False)

    if search_query:
        orders = orders.filter(
            Q(title__icontains=search_query)
            | ProductionOrder.identifier_search_query(search_query)
            | Q(customer__account_brand__icontains=search_query)
            | Q(customer__contact_name__icontains=search_query)
            | Q(product__name__icontains=search_query)
            | Q(lead__account_brand__icontains=search_query)
            | Q(lead__primary_product_type__icontains=search_query)
            | Q(opportunity__product_type__icontains=search_query)
            | Q(opportunity__product_category__icontains=search_query)
        )

    orders_for_kpis = orders
    production_kpi_rows = [
        {
            "order": order,
            "operational_status": get_production_operational_status(order),
        }
        for order in orders_for_kpis
    ]

    orders_data_all = []

    for order in orders:
        operational_status = get_production_operational_status(order)
        stages = get_sorted_stages(order)
        shipments = list(order.shipments.all())

        total_stages = len(stages)
        done_count = len([s for s in stages if s.status == "done"])
        percent_done = int((done_count / total_stages) * 100) if total_stages else 0

        bulk_overdue = bool(
            order.bulk_deadline
            and today > order.bulk_deadline
            and operational_status not in OPERATIONAL_FINISHED_STATUSES
        )
        stage_delay = any(s.status == "delay" or s.is_late for s in stages)
        late_shipment = any(
            s.ship_date
            and s.ship_date < today
            and s.status not in {"delivered", "cancelled"}
            for s in shipments
        )
        has_delay = bulk_overdue or stage_delay
        shipment_pending = (
            not shipments
            and operational_status in OPERATIONAL_ACTIVE_STATUSES
            and (percent_done >= 80 or _production_any_stage_started(_production_stage_lookup(stages), ["shipping"]))
        )
        priority = _production_priority(
            order,
            percent_done,
            has_delay,
            late_shipment,
            shipment_pending,
            today,
            operational_status,
        )
        lifecycle = None
        try:
            lifecycles = list(order.order_lifecycles.all())
            lifecycle = lifecycles[0] if lifecycles else None
        except (AttributeError, IndexError):
            lifecycle = None

        orders_data_all.append(
            {
                "order": order,
                "stages": stages,
                "shipments": shipments,
                "percent_done": percent_done,
                "has_delay": has_delay,
                "bulk_overdue": bulk_overdue,
                "late_shipment": late_shipment,
                "shipment_pending": shipment_pending,
                "priority": priority,
                "operational_status": operational_status,
                "operational_status_label": OPERATIONAL_STATUS_LABELS.get(
                    operational_status,
                    OPERATIONAL_STATUS_LABELS[OPERATIONAL_STATUS_PLANNING],
                ),
                "latest_shipment": shipments[0] if shipments else None,
                "lifecycle": lifecycle,
                "lifecycle_currency": lifecycle_currency(lifecycle) if lifecycle else "",
                "can_hard_delete": not (
                    shipments
                    or order.list_has_inventory_allocations
                    or order.list_has_invoices
                    or order.list_has_accounting_records
                ),
            }
        )

    orders_data = []
    for row in orders_data_all:
        operational_status = row["operational_status"]
        if status_filter == "active" and operational_status not in OPERATIONAL_ACTIVE_STATUSES:
            continue
        if status_filter == "sample_development" and operational_status != OPERATIONAL_STATUS_SAMPLE_DEVELOPMENT:
            continue
        if status_filter == "bulk_production" and (
            row["order"].production_order_type != "bulk"
            or operational_status not in OPERATIONAL_ACTIVE_STATUSES
        ):
            continue
        if status_filter == "delayed" and not row["has_delay"]:
            continue
        if status_filter == "ready_to_ship" and operational_status != OPERATIONAL_STATUS_READY_TO_SHIP:
            continue
        if status_filter == "shipped" and (
            operational_status != OPERATIONAL_STATUS_SHIPPED
            or _production_row_is_completed(row)
        ):
            continue
        if status_filter == "completed" and not _production_row_is_completed(row):
            continue
        if status_filter == "cancelled" and operational_status != OPERATIONAL_STATUS_CANCELLED:
            continue
        if status_filter == "archived" and not row["order"].is_archived:
            continue
        if status_filter not in {
            "active",
            "sample_development",
            "bulk_production",
            "delayed",
            "ready_to_ship",
            "shipped",
            "completed",
            "cancelled",
            "archived",
            "all",
        } and operational_status not in OPERATIONAL_ACTIVE_STATUSES:
            continue
        if priority_filter != "all" and row["priority"]["key"] != priority_filter:
            continue
        if delayed_filter == "delayed" and not row["has_delay"]:
            continue
        if delayed_filter == "on_track" and row["has_delay"]:
            continue
        if shipment_filter == "pending" and not row["shipment_pending"]:
            continue
        if shipment_filter == "linked" and not row["shipments"]:
            continue
        if shipment_filter == "late" and not row["late_shipment"]:
            continue
        orders_data.append(row)
    production_operational_counts = Counter(
        row["operational_status"] for row in production_kpi_rows
    )
    production_kpi_active_rows = [
        row for row in production_kpi_rows
        if row["operational_status"] in OPERATIONAL_ACTIVE_STATUSES
    ]
    sample_development_rows = [
        row for row in production_kpi_active_rows
        if row["order"].production_order_type == "sampling"
    ]
    bulk_production_rows = [
        row for row in production_kpi_active_rows
        if row["order"].production_order_type == "bulk"
    ]
    total_active_orders_count = len(production_kpi_active_rows)
    total_active_units_count = sum((row["order"].qty_total or 0) for row in production_kpi_active_rows)
    total_orders = total_active_orders_count
    active_orders = total_active_orders_count
    total_pieces = total_active_units_count
    total_reject = sum((row["order"].qty_reject or 0) for row in production_kpi_active_rows)
    reject_percent = int((total_reject / total_pieces) * 100) if total_pieces else 0
    sample_development_orders_count = len(sample_development_rows)
    sample_development_units_count = sum((row["order"].qty_total or 0) for row in sample_development_rows)
    bulk_production_orders_count = len(bulk_production_rows)
    bulk_production_units_count = sum((row["order"].qty_total or 0) for row in bulk_production_rows)
    production_completed_count = len([
        row for row in production_kpi_rows
        if row["operational_status"] == OPERATIONAL_STATUS_SHIPPED
    ])
    completed_orders = production_completed_count
    production_completion_denominator = total_active_orders_count + production_completed_count
    production_completion_percent = (
        round((production_completed_count / production_completion_denominator) * 100)
        if production_completion_denominator
        else 0
    )
    ready_to_ship_operations_count = len([
        row for row in production_kpi_rows
        if row["operational_status"] == OPERATIONAL_STATUS_READY_TO_SHIP
    ])
    delayed_operations_count = len([
        row for row in production_kpi_rows
        if row["order"].bulk_deadline
        and row["order"].bulk_deadline < today
        and row["operational_status"] not in OPERATIONAL_FINISHED_STATUSES
    ])
    delayed_orders = delayed_operations_count
    awaiting_approval_samples_count = len([
        row for row in production_kpi_rows
        if row["operational_status"] == OPERATIONAL_STATUS_SAMPLE_SENT
    ])

    pipeline_statuses = [
        OPERATIONAL_STATUS_PLANNING,
        OPERATIONAL_STATUS_SAMPLE_DEVELOPMENT,
        OPERATIONAL_STATUS_APPROVED,
        OPERATIONAL_STATUS_SEWING,
        OPERATIONAL_STATUS_QC,
        OPERATIONAL_STATUS_PACKING,
        OPERATIONAL_STATUS_READY_TO_SHIP,
        OPERATIONAL_STATUS_SHIPPED,
    ]
    pipeline_counts = [
        {
            "key": status,
            "label": OPERATIONAL_STATUS_LABELS[status],
            "count": production_operational_counts.get(status, 0),
        }
        for status in pipeline_statuses
    ]
    pipeline_total_count = sum(item["count"] for item in pipeline_counts)
    delayed_operation_rows = sorted(
        [
            {
                "order": row["order"],
                "operational_status": row["operational_status"],
                "priority": SimpleNamespace(
                    key="urgent",
                    label="Urgent",
                    reason="Delayed",
                    tone="risk",
                ),
            }
            for row in production_kpi_rows
            if row["order"].bulk_deadline
            and row["order"].bulk_deadline < today
            and row["operational_status"] not in OPERATIONAL_FINISHED_STATUSES
        ],
        key=lambda row: (row["order"].bulk_deadline or date.max, row["order"].order_code or ""),
    )
    delayed_operation_ids = {row["order"].pk for row in delayed_operation_rows}
    ready_to_ship_operation_rows = sorted(
        [
            {
                "order": row["order"],
                "operational_status": row["operational_status"],
                "priority": SimpleNamespace(
                    key="high",
                    label="High",
                    reason="Ready to ship",
                    tone="warning",
                ),
            }
            for row in production_kpi_rows
            if row["operational_status"] == OPERATIONAL_STATUS_READY_TO_SHIP
            and row["order"].pk not in delayed_operation_ids
        ],
        key=lambda row: (row["order"].bulk_deadline or date.max, row["order"].order_code or ""),
    )
    urgent_orders = sorted(
        delayed_operation_rows + ready_to_ship_operation_rows,
        key=lambda row: (
            0 if row["priority"].key == "urgent" else 1,
            row["order"].bulk_deadline or date.max,
            row["order"].order_code or "",
        ),
    )[:10]
    factory_summary = {
        "active_orders": total_active_orders_count,
        "units_in_production": total_active_units_count,
        "completed_orders": production_completed_count,
        "ready_to_ship": ready_to_ship_operations_count,
    }
    can_view_profit = can_view_lifecycle_profit(request.user)
    local_sewing_summary = summarize_local_sewing_orders(
        scope_production_orders(ProductionOrder.objects.all(), request.user)
    )
    low_margin_orders = []
    high_profit_orders = []
    if can_view_profit:
        profit_rows = [row for row in orders_data if row["lifecycle"]]
        low_margin_orders = sorted(
            [
                row for row in profit_rows
                if row["lifecycle"].estimated_revenue
                and row["lifecycle"].estimated_cost > 0
                and row["lifecycle"].estimated_margin < Decimal("15")
            ],
            key=lambda row: row["lifecycle"].estimated_margin,
        )[:4]
        high_profit_orders = sorted(
            [
                row for row in profit_rows
                if row["lifecycle"].estimated_cost > 0
                and row["lifecycle"].estimated_profit
                and row["lifecycle"].estimated_profit > 0
            ],
            key=lambda row: row["lifecycle"].estimated_profit,
            reverse=True,
        )[:4]

    paginator = Paginator(orders_data, 25)
    page_obj = paginator.get_page(request.GET.get("page"))
    paginated_orders_data = list(page_obj.object_list)
    attach_primary_reference_images_to_production_orders(
        [row["order"] for row in paginated_orders_data]
    )
    pagination_query = request.GET.copy()
    pagination_query.pop("page", None)

    return render(
        request,
        "crm/production_list.html",
        {
            "orders_data": paginated_orders_data,
            "page_obj": page_obj,
            "paginator": paginator,
            "pagination_query": pagination_query.urlencode(),
            "total_orders": total_orders,
            "active_orders": active_orders,
            "total_active_orders_count": total_active_orders_count,
            "total_active_units_count": total_active_units_count,
            "completed_orders": completed_orders,
            "delayed_orders": delayed_orders,
            "sample_development_orders_count": sample_development_orders_count,
            "sample_development_units_count": sample_development_units_count,
            "bulk_production_orders_count": bulk_production_orders_count,
            "bulk_production_units_count": bulk_production_units_count,
            "production_completion_percent": production_completion_percent,
            "ready_to_ship_operations_count": ready_to_ship_operations_count,
            "delayed_operations_count": delayed_operations_count,
            "awaiting_approval_samples_count": awaiting_approval_samples_count,
            "total_pieces": total_pieces,
            "total_reject": total_reject,
            "reject_percent": reject_percent,
            "status_filter": status_filter,
            "archive_filter": archive_filter,
            "search_query": search_query,
            "priority_filter": priority_filter,
            "delayed_filter": delayed_filter,
            "shipment_filter": shipment_filter,
            "status_choices": ProductionOrder.STATUS_CHOICES,
            "operational_status_choices": ProductionOrder.OPERATIONAL_STATUS_CHOICES,
            "pipeline_counts": pipeline_counts,
            "pipeline_total_count": pipeline_total_count,
            "urgent_orders": urgent_orders,
            "factory_summary": factory_summary,
            "can_view_lifecycle_profit": can_view_profit,
            "local_sewing_summary": local_sewing_summary,
            "can_view_local_sewing_financials": can_view_local_sewing_financials(request.user),
            "low_margin_orders": low_margin_orders,
            "high_profit_orders": high_profit_orders,
            "can_archive_records": _can_archive_workflow_record(request.user),
        },
    )


def production_add(request):
    """
    Create new production order.
    """
    order_lines = None
    can_edit_internal_costing = can_view_lifecycle_profit(request.user)
    can_edit_local_financials = can_view_local_sewing_financials(request.user)
    if request.method == "POST":
        form = ProductionOrderForm(
            request.POST,
            request.FILES,
            can_edit_internal_costing=can_edit_internal_costing,
            can_edit_local_sewing_financials=can_edit_local_financials,
        )
        if form.is_valid():
            form.instance.created_by = request.user if request.user.is_authenticated else None
            order = form.save()
            _save_production_lines(order, request)
            _apply_production_library_links(order, request)
            create_lifecycle_from_production(order, user=request.user)
            messages.success(request, "Production order created.")
            return redirect("production_detail", pk=order.pk)
        order_lines = _production_order_lines_from_payload(request.POST.get("line_payload"))
    else:
        form = ProductionOrderForm(
            can_edit_internal_costing=can_edit_internal_costing,
            can_edit_local_sewing_financials=can_edit_local_financials,
        )

    return render(
        request,
        "crm/production_add.html",
        {
            "form": form,
            "is_edit": False,
            "order": None,
            "order_lines": order_lines or _production_order_lines_for_form(),
            "production_size_labels": SIZE_LABELS,
            "production_size_group_choices": SIZE_GROUP_CHOICES,
            "can_view_lifecycle_profit": can_edit_internal_costing,
            "can_edit_local_sewing_financials": can_edit_local_financials,
            **_production_library_context(),
        },
    )


def production_edit(request, pk):
    """
    Edit existing production order.
    """
    order = get_object_or_404(
        scope_production_orders(ProductionOrder.objects.all(), request.user),
        pk=pk,
    )
    old_status = order.status
    order_lines = None
    can_edit_internal_costing = can_view_lifecycle_profit(request.user)
    can_edit_local_financials = can_view_local_sewing_financials(request.user)

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        if action == "add_material":
            item_id = (request.POST.get("inventory_item") or "").strip()
            qty_raw = (request.POST.get("quantity") or "").strip()
            note = (request.POST.get("note") or "").strip()

            item = InventoryItem.objects.filter(pk=item_id).first() if item_id else None
            if item:
                if _production_reserve_inventory(order, item, qty_raw, note, request):
                    messages.success(request, "Material reserved for this production order.")
            else:
                messages.error(request, "Please select a material.")

            return redirect("production_edit", pk=pk)

        if action == "remove_material":
            line_id = (request.POST.get("line_id") or "").strip()
            if line_id:
                line = ProductionOrderMaterial.objects.filter(pk=line_id, order=order).select_related("inventory_item").first()
                if line:
                    _production_remove_inventory_reservation(line, request)
            return redirect("production_edit", pk=pk)

        try:
            form = ProductionOrderForm(
                request.POST,
                request.FILES,
                instance=order,
                can_edit_internal_costing=can_edit_internal_costing,
                can_edit_local_sewing_financials=can_edit_local_financials,
            )
            if form.is_valid():
                obj = form.save()
                _apply_production_library_links(obj, request)
                _save_production_lines(obj, request)

                if not obj.customer_id and obj.opportunity and obj.opportunity.customer_id:
                    obj.customer = obj.opportunity.customer
                    obj.save(update_fields=["customer"])

                _apply_production_status_change(obj, old_status)
                create_lifecycle_from_production(obj, user=request.user)

                messages.success(request, "Production order updated.")
                return redirect("production_detail", pk=pk)
        except Exception:
            logging.getLogger(__name__).exception(
                "Production order POST failed for order %s",
                order.pk,
            )
            messages.error(
                request,
                "Production order could not be updated. Please try shorter text or reload and try again.",
            )
            form = ProductionOrderForm(
                instance=order,
                can_edit_internal_costing=can_edit_internal_costing,
                can_edit_local_sewing_financials=can_edit_local_financials,
            )
        order_lines = _production_order_lines_from_payload(request.POST.get("line_payload"))
    else:
        form = ProductionOrderForm(
            instance=order,
            can_edit_internal_costing=can_edit_internal_costing,
            can_edit_local_sewing_financials=can_edit_local_financials,
        )

    try:
        materials = list(order.materials.select_related("inventory_item"))
    except (OperationalError, ProgrammingError):
        materials = []

    recommended_items = []
    recommended_ids = []
    try:
        if order.fabrics.exists() or order.accessories.exists() or order.trims.exists() or order.threads.exists():
            recommended_items = list(
                InventoryItem.objects.filter(
                    Q(fabric__in=order.fabrics.all())
                    | Q(accessory__in=order.accessories.all())
                    | Q(trim__in=order.trims.all())
                    | Q(thread_option__in=order.threads.all())
                ).distinct()
            )
        recommended_ids = [str(i.pk) for i in recommended_items]
    except (OperationalError, ProgrammingError):
        recommended_items = []
        recommended_ids = []

    try:
        inventory_items = list(
            InventoryItem.objects.filter(is_active=True).order_by("material_group", "category", "name")
        )
    except (OperationalError, ProgrammingError):
        inventory_items = []

    inventory_context = {
        "materials": materials,
        "recommended_items": recommended_items,
        "recommended_ids": recommended_ids,
        "inventory_items": inventory_items,
    }

    try:
        context = {
            "form": form,
            "is_edit": True,
            "order": order,
            "order_lines": order_lines or _production_order_lines_for_form(order),
            "production_size_labels": SIZE_LABELS,
            "production_size_group_choices": SIZE_GROUP_CHOICES,
            "can_view_lifecycle_profit": can_edit_internal_costing,
            "can_edit_local_sewing_financials": can_edit_local_financials,
            **inventory_context,
            **_production_library_context(order),
        }
    except Exception:
        logging.getLogger(__name__).exception(
            "Production edit context build failed for order %s",
            order.pk,
        )
        context = {
            "form": form,
            "order": order,
            "production_size_labels": SIZE_LABELS,
            "production_size_group_choices": SIZE_GROUP_CHOICES,
            "can_view_lifecycle_profit": can_edit_internal_costing,
        }
        return render(request, "crm/production_edit.html", context)
    try:
        return render(request, "crm/production_add.html", context)
    except Exception:
        logging.getLogger(__name__).exception(
            "Production edit rich template failed for order %s; falling back to simple edit template",
            order.pk,
        )
        fallback_context = {
            "form": form,
            "order": order,
            "production_size_labels": SIZE_LABELS,
            "production_size_group_choices": SIZE_GROUP_CHOICES,
            "can_view_lifecycle_profit": can_edit_internal_costing,
        }
        return render(request, "crm/production_edit.html", fallback_context)


def production_detail(request, pk):
    detail_prefetches = [
        "fabrics",
        "accessories",
        "trims",
        "threads",
        Prefetch("stages", queryset=ProductionStage.objects.all()),
        Prefetch(
            "shipments",
            queryset=Shipment.objects.order_by("-ship_date", "-created_at"),
        ),
        Prefetch(
            "attachments",
            queryset=ProductionOrderAttachment.objects.select_related("line").order_by("-created_at"),
        ),
        Prefetch(
            "progress_photos",
            queryset=ProductionProgressPhoto.objects.select_related("uploaded_by").order_by(
                "stage", "-uploaded_at", "-id"
            ),
        ),
        Prefetch(
            "invoices",
            queryset=Invoice.objects.select_related("costing_header", "customer").order_by(
                "-created_at", "-id"
            ),
        ),
        Prefetch(
            "order_lifecycles",
            queryset=OrderLifecycle.objects.select_related(
                "customer",
                "lead",
                "opportunity",
                "costing",
                "quotation",
                "invoice",
                "production_order",
                "shipping_record",
            ).order_by("-updated_at", "-id"),
        ),
    ]
    if ProductionOrderLine is not None:
        detail_prefetches.append(
            Prefetch(
                "lines",
                queryset=ProductionOrderLine.objects.order_by("line_no", "id"),
            )
        )

    order = get_object_or_404(
        scope_production_orders(ProductionOrder.objects.select_related(
            "customer",
            "product",
            "opportunity",
            "lead",
            "source_quotation",
            "source_quick_costing",
            "source_quick_costing__previous_revision",
            "source_quick_costing__superseded_by",
            "source_quick_costing__revision_root",
            "assigned_production_manager",
            "created_by",
        ), request.user),
        pk=pk,
    )
    if request.method == "POST" and not (
        request.user.is_superuser
        or can_access_operations_module(request.user, "production")
    ):
        return HttpResponseForbidden("Production updates are not permitted for this role.")
    prefetch_related_objects([order], *detail_prefetches)
    can_add_lines = ProductionOrderLine is not None and hasattr(order, "lines")
    opportunity = _safe_related_attr(order, "opportunity")
    lead = _safe_related_attr(order, "lead")
    customer = _safe_related_attr(order, "customer")
    product = _safe_related_attr(order, "product")
    today = timezone.localdate()

    # sorted stages
    stages = get_sorted_stages(order)
    local_sewing = calculate_local_sewing(order, stages=stages) if is_bangladesh_local_sewing(order) else None
    operational_status = get_production_operational_status(order)
    operational_status_label = OPERATIONAL_STATUS_LABELS.get(
        operational_status,
        OPERATIONAL_STATUS_LABELS[OPERATIONAL_STATUS_PLANNING],
    )
    if local_sewing:
        local_statuses = {
            "planning": ("planning", "Planning"),
            "in_progress": ("sewing", "In progress"),
            "hold": ("hold", "On hold"),
            "done": ("shipped", "Done"),
            "closed_won": ("shipped", "Completed"),
            "closed_lost": ("cancelled", "Cancelled"),
        }
        operational_status, operational_status_label = local_statuses.get(
            order.status,
            (operational_status, operational_status_label),
        )
        if order.status == "planning" and local_sewing["completed_quantity"] > 0:
            operational_status, operational_status_label = "sewing", "In progress"
    production_workflow_steps = _production_workflow_steps(operational_status)
    next_required_action = next(
        (step.label for step in production_workflow_steps if step.state == "current"),
        "Review production status",
    )

    # order lines for sheet details
    order_lines = _production_order_lines(order)

    # files
    try:
        attachments = list(order.attachments.all())
    except (OperationalError, ProgrammingError):
        attachments = []
    try:
        progress_photos = list(order.progress_photos.all())
    except (OperationalError, ProgrammingError):
        progress_photos = []

    # shipments for this order
    try:
        shipments = list(order.shipments.all())
    except (OperationalError, ProgrammingError):
        shipments = []

    # progress and delay
    total_stages = len(stages)
    done_count = sum(1 for stage in stages if stage.status == "done")
    percent_done = int((done_count / total_stages) * 100) if total_stages else 0
    if local_sewing and local_sewing["quantity"]:
        percent_done = min(
            int((local_sewing["completed_quantity"] / local_sewing["quantity"]) * 100),
            100,
        )
    stage_delay = any(s.status == "delay" or s.is_late for s in stages)
    order_delayed = bool(
        operational_status not in OPERATIONAL_FINISHED_STATUSES
        and (
            stage_delay
            or (order.bulk_deadline and order.bulk_deadline < today)
        )
    )
    late_shipment = any(
        s.ship_date
        and s.ship_date < today
        and s.status not in {"delivered", "cancelled"}
        for s in shipments
    )
    shipment_pending = (
        not shipments
        and operational_status in OPERATIONAL_ACTIVE_STATUSES
        and (percent_done >= 80 or _production_any_stage_started(_production_stage_lookup(stages), ["shipping"]))
    )
    priority_badge = _production_priority(
        order,
        percent_done,
        order_delayed,
        late_shipment,
        shipment_pending,
        today,
        operational_status,
    )
    production_visual_stages = _production_visual_stages(order, stages, shipments)
    latest_shipment = shipments[0] if shipments else None
    reject_percent = int((order.qty_reject / order.qty_total) * 100) if order.qty_total else 0
    approved_summary = order.approved_costing_summary or {}
    approved_currency = order.approved_currency or "BDT"
    source_quick_costing = getattr(order, "source_quick_costing", None)
    quick_latest_approved_revision = (
        source_quick_costing.latest_approved_revision()
        if source_quick_costing
        else None
    )
    approved_costing_rows = [
        {
            "label": "Total cost per piece",
            "value": format_finance_money(approved_summary.get("total_cost_per_piece"), approved_currency),
        },
        {
            "label": "Total approved cost",
            "value": format_finance_money(approved_summary.get("total_cost_order"), approved_currency),
        },
    ]

    try:
        inventory_context = _production_inventory_context(order)
    except Exception:
        logger.exception("production_detail: failed to load inventory context for order %s", order.pk)
        inventory_context = {
            "materials": [],
            "recommended_items": [],
            "recommended_ids": [],
            "inventory_items": [],
        }
    try:
        comments = _chatter_for_production(order, request.user)
    except Exception:
        logger.exception("production_detail: failed to load chatter for order %s", order.pk)
        comments = []
    can_view_profit = can_view_lifecycle_profit(request.user)
    can_view_local_financials = can_view_local_sewing_financials(request.user)
    if can_view_profit:
        try:
            actual_entries = list(order.actual_cost_entries.all().order_by("section", "id"))
        except (AttributeError, OperationalError, ProgrammingError):
            actual_entries = []
    else:
        actual_entries = []
    variance_report = None
    raw_cost_sheet_active = _safe_related_attr(order, "cost_sheet_active")
    cost_sheet_active = raw_cost_sheet_active if can_view_profit else None
    if can_view_profit and cost_sheet_active:
        try:
            variance_report = build_variance_report(cost_sheet_active, order)
        except Exception:
            logger.exception("production_detail: failed to build variance report for order %s", order.pk)
            variance_report = None
    invoices = list(order.invoices.all())
    latest_invoice = invoices[0] if invoices else None
    lifecycle = None
    lifecycle_profit = None
    try:
        lifecycles = list(order.order_lifecycles.all())
        lifecycle = lifecycles[0] if lifecycles else None
        if lifecycle is None:
            invoice = latest_invoice
            lifecycle = OrderLifecycle(
                customer=customer or getattr(invoice, "customer", None),
                lead=lead,
                opportunity=opportunity,
                costing=getattr(order, "costing_header", None) or getattr(invoice, "costing_header", None),
                quotation=getattr(order, "costing_header", None) or getattr(invoice, "costing_header", None),
                invoice=invoice,
                production_order=order,
            )
        if can_view_profit:
            lifecycle_profit = build_lifecycle_profit_breakdown(lifecycle)
    except Exception:
        logger.exception("production_detail: failed to build lifecycle profit for order %s", order.pk)
        lifecycle_profit = None
    actual_entry_form = ActualCostEntryForm() if can_view_profit else None
    production_activity = _production_activity_timeline(
        order,
        stages,
        shipments,
        lifecycle if getattr(lifecycle, "pk", None) else None,
        comments,
    )
    reference_images = list(reference_images_for_production(order))
    primary_reference_image = reference_images[0] if reference_images else None
    progress_photos_by_stage = defaultdict(list)
    for photo in progress_photos:
        progress_photos_by_stage[photo.stage].append(photo)
    progress_photo_sections = [
        {
            "key": key,
            "label": label,
            "photos": progress_photos_by_stage.get(key, []),
        }
        for key, label in ProductionProgressPhoto.STAGE_CHOICES
    ]
    workflow_visibility = build_workflow_visibility_context(
        "production",
        user=request.user,
        lead=lead,
        opportunity=opportunity,
        costing=getattr(order, "costing_header", None),
        invoice=latest_invoice,
        production_order=order,
        shipment=latest_shipment,
        lifecycle=lifecycle if getattr(lifecycle, "pk", None) else None,
    )

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()

        if action in {"add_actual", "update_actual", "delete_actual"} and not can_view_profit:
            return HttpResponseForbidden("No access")

        if action == "add_progress_photo":
            stage = (request.POST.get("progress_stage") or "").strip()
            caption = (request.POST.get("progress_caption") or "").strip()
            image = request.FILES.get("progress_photo")
            valid_stages = {key for key, _label in ProductionProgressPhoto.STAGE_CHOICES}
            if stage not in valid_stages:
                messages.error(request, "Choose a valid production stage.")
                return redirect("production_detail", pk=pk)
            if not image:
                messages.error(request, "Please choose a progress photo.")
                return redirect("production_detail", pk=pk)
            try:
                ProductionProgressPhoto.objects.create(
                    order=order,
                    stage=stage,
                    image=image,
                    caption=caption,
                    uploaded_by=request.user if request.user.is_authenticated else None,
                )
                messages.success(request, "Production progress photo added.")
            except ValidationError as exc:
                message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
                messages.error(request, message)
            return redirect("production_detail", pk=pk)

        if action == "add_line":
            if not can_add_lines:
                messages.error(request, "Line items are not enabled for this order.")
                return redirect("production_detail", pk=pk)

            style_name = (request.POST.get("line_style_name") or "").strip()
            color_info = (request.POST.get("line_color_info") or "").strip()
            size_ratio_note = (request.POST.get("line_size_ratio_note") or "").strip()
            quantity = _clean_production_line_quantity(request.POST.get("line_quantity"), size_ratio_note)
            accessories_note = (request.POST.get("line_accessories_note") or "").strip()
            packaging_note = (request.POST.get("line_packaging_note") or "").strip()
            extra_order_note = (request.POST.get("line_extra_order_note") or "").strip()

            if not any([style_name, color_info, quantity, size_ratio_note, accessories_note, packaging_note, extra_order_note]):
                messages.error(request, "Please add at least one detail before saving a new line.")
                return redirect("production_detail", pk=pk)

            try:
                max_no = order.lines.aggregate(m=Max("line_no")).get("m") or 0
            except Exception:
                max_no = 0

            ProductionOrderLine.objects.create(
                order=order,
                line_no=max_no + 1,
                style_name=style_name,
                color_info=color_info,
                quantity=quantity,
                size_ratio_note=size_ratio_note,
                accessories_note=accessories_note,
                packaging_note=packaging_note,
                extra_order_note=extra_order_note,
            )
            messages.success(request, "Product line added to this order.")
            return redirect("production_detail", pk=pk)

        if action == "add_material":
            item_id = (request.POST.get("inventory_item") or "").strip()
            qty_raw = (request.POST.get("quantity") or "").strip()
            note = (request.POST.get("note") or "").strip()

            item = InventoryItem.objects.filter(pk=item_id).first() if item_id else None
            if item:
                if _production_reserve_inventory(order, item, qty_raw, note, request):
                    messages.success(request, "Material reserved for this production order.")
            else:
                messages.error(request, "Please select a material.")

            return redirect("production_detail", pk=pk)

        if action in {"consume_material", "damage_material"}:
            line_id = (request.POST.get("line_id") or "").strip()
            qty_raw = (request.POST.get("movement_quantity") or "").strip()
            line = ProductionOrderMaterial.objects.filter(pk=line_id, order=order).select_related("inventory_item", "order").first()
            if line:
                _production_consume_inventory(
                    line,
                    qty_raw,
                    request,
                    movement_type="damaged" if action == "damage_material" else "consumed",
                )
            else:
                messages.error(request, "Material allocation was not found.")
            return redirect("production_detail", pk=pk)

        if action == "remove_material":
            line_id = (request.POST.get("line_id") or "").strip()
            if line_id:
                line = ProductionOrderMaterial.objects.filter(pk=line_id, order=order).select_related("inventory_item").first()
                if line:
                    _production_remove_inventory_reservation(line, request)
            return redirect("production_detail", pk=pk)

        if action == "add_comment":
            if not can_access_chatter_record(request.user, "production", order):
                return HttpResponseForbidden("You do not have access to this production order's chatter.")
            comment_text = (request.POST.get("comment_text") or "").strip()
            attachment = request.FILES.get("attachment")
            if not comment_text and not attachment:
                messages.error(request, "Please write a note or attach a file first.")
            else:
                author_name = employee_display_name(request.user)
                content = comment_text or f"Attachment: {attachment.name}"
                comment = LeadComment.objects.create(
                    lead=lead,
                    opportunity=opportunity,
                    production=order,
                    author=author_name,
                    author_user=request.user,
                    content=content,
                    attachment=attachment,
                )
                _send_chatter_mentions(request, comment)
                messages.success(request, "Chatter note added.")
            return redirect("production_detail", pk=pk)

        if action == "add_actual":
            if not opportunity:
                messages.error(request, "Link an opportunity to record actual costs.")
                return redirect("production_detail", pk=pk)
            form = ActualCostEntryForm(request.POST)
            if form.is_valid():
                entry = form.save(commit=False)
                qty_val = form.cleaned_data.get("actual_qty_total") or Decimal("0")
                rate_val = form.cleaned_data.get("actual_rate") or Decimal("0")
                try:
                    entry.actual_total_cost = (qty_val * rate_val).quantize(Decimal("0.0001"))
                except Exception:
                    entry.actual_total_cost = Decimal("0")
                entry.production_order = order
                entry.opportunity = opportunity
                entry.cost_sheet = cost_sheet_active
                entry.save()
                if cost_sheet_active:
                    CostSheetAudit.objects.create(
                        cost_sheet=cost_sheet_active,
                        action="edited_actual",
                        changed_by=request.user if request.user.is_authenticated else None,
                        note=f"Actual cost added: {entry.item_name}",
                    )
                messages.success(request, "Actual cost entry added.")
            else:
                messages.error(request, "Please fill the actual cost form.")
            return redirect("production_detail", pk=pk)

        if action == "update_actual":
            entry_id = (request.POST.get("entry_id") or "").strip()
            entry = ActualCostEntry.objects.filter(id=entry_id, production_order=order).first()
            if entry:
                entry.section = request.POST.get("section", entry.section)
                entry.item_name = (request.POST.get("item_name") or "").strip()
                entry.uom = (request.POST.get("uom") or "").strip()
                entry.actual_qty_total = _parse_decimal(request.POST.get("actual_qty_total"))
                entry.actual_rate = _parse_decimal(request.POST.get("actual_rate"))
                try:
                    entry.actual_total_cost = (entry.actual_qty_total * entry.actual_rate).quantize(Decimal("0.0001"))
                except Exception:
                    entry.actual_total_cost = Decimal("0")
                entry.notes = (request.POST.get("notes") or "").strip()
                entry.save()
                if cost_sheet_active:
                    CostSheetAudit.objects.create(
                        cost_sheet=cost_sheet_active,
                        action="edited_actual",
                        changed_by=request.user if request.user.is_authenticated else None,
                        note=f"Actual cost updated: {entry.item_name}",
                    )
                messages.success(request, "Actual cost entry updated.")
            return redirect("production_detail", pk=pk)

        if action == "delete_actual":
            entry_id = (request.POST.get("entry_id") or "").strip()
            entry = ActualCostEntry.objects.filter(id=entry_id, production_order=order).first()
            if entry:
                entry_name = entry.item_name
                entry.delete()
                if cost_sheet_active:
                    CostSheetAudit.objects.create(
                        cost_sheet=cost_sheet_active,
                        action="edited_actual",
                        changed_by=request.user if request.user.is_authenticated else None,
                        note=f"Actual cost deleted: {entry_name}",
                    )
                messages.success(request, "Actual cost entry deleted.")
            return redirect("production_detail", pk=pk)

        if action == "toggle_pin_comment":
            comment_id = (request.POST.get("comment_id") or "").strip()
            if comment_id:
                filters = Q(id=comment_id, production=order)
                if opportunity:
                    filters |= Q(id=comment_id, opportunity=opportunity)
                if lead:
                    filters |= Q(id=comment_id, lead=lead, opportunity__isnull=True, production__isnull=True)
                comment = LeadComment.objects.filter(filters).first()
                if comment:
                    comment.pinned = not comment.pinned
                    comment.save(update_fields=["pinned"])
            return redirect("production_detail", pk=pk)

    context = {
        "order": order,
        "stages": stages,
        "percent_done": percent_done,
        "order_delayed": order_delayed,
        "priority_badge": priority_badge,
        "operational_status": operational_status,
        "operational_status_label": operational_status_label,
        "production_workflow_steps": production_workflow_steps,
        "next_required_action": next_required_action,
        "production_visual_stages": production_visual_stages,
        "late_shipment": late_shipment,
        "shipment_pending": shipment_pending,
        "latest_shipment": latest_shipment,
        "production_activity": production_activity,
        "order_lines": order_lines,
        "attachments": attachments,
        "invoices": invoices,
        "progress_photo_stage_choices": ProductionProgressPhoto.STAGE_CHOICES,
        "progress_photo_sections": progress_photo_sections,
        "shipments": shipments,
        "reject_percent": reject_percent,
        "approved_selling_price_display": format_finance_money(
            order.approved_selling_price,
            approved_currency,
        ),
        "approved_total_value_display": format_finance_money(
            order.approved_total_value,
            approved_currency,
        ),
        "approved_costing_rows": approved_costing_rows,
        "source_quick_costing": source_quick_costing,
        "quick_latest_approved_revision": quick_latest_approved_revision,
        "opportunity": opportunity,
        "lead": lead,
        "customer": customer,
        "product": product,
        "reference_images": reference_images,
        "primary_reference_image": primary_reference_image,
        "product_snapshot": product_snapshot_for_production(order, primary_reference_image),
        "comments": comments,
        "record_audit_history": list(
            CRMAuditLog.objects.filter(module="production", record_id=str(order.pk))
            .select_related("actor")
            .order_by("-created_at", "-id")[:12]
        ),
        **inventory_context,
        "cost_sheet_active": cost_sheet_active,
        "actual_entries": actual_entries,
        "actual_entry_form": actual_entry_form,
        "variance_report": variance_report,
        "lifecycle": lifecycle if getattr(lifecycle, "pk", None) else None,
        "lifecycle_profit": lifecycle_profit,
        "can_view_lifecycle_profit": can_view_profit,
        "can_view_local_sewing_financials": can_view_local_financials,
        "can_edit_production": can_access_operations_module(request.user, "production"),
        "is_bangladesh_local_sewing": bool(local_sewing),
        "local_sewing": local_sewing,
        "can_add_lines": can_add_lines,
        "can_archive_records": _can_archive_workflow_record(request.user),
        "production_can_hard_delete": not _production_linked_record_labels(order),
        "production_lifecycle_banner": _production_lifecycle_banner(shipments),
        **workflow_visibility,
    }

    return render(request, "crm/production_detail.html", context)


@require_POST
def production_archive(request, pk):
    order = get_object_or_404(ProductionOrder, pk=pk)
    if not _can_archive_workflow_record(request.user):
        messages.error(request, "You do not have permission to archive production orders.")
        return redirect("production_detail", pk=order.pk)

    operational_status = get_production_operational_status(order)
    if operational_status in {OPERATIONAL_STATUS_READY_TO_SHIP, OPERATIONAL_STATUS_SHIPPED}:
        confirmation = (request.POST.get("confirm_archive") or "").strip().lower()
        if confirmation not in {"archive", "yes", "1"}:
            messages.error(
                request,
                "Ready to ship or shipped production orders require confirmation before archiving.",
            )
            return redirect("production_detail", pk=order.pk)

    _archive_workflow_record(order, request.user)
    linked_labels = _production_linked_record_labels(order)
    label = _workflow_object_label(order)
    _log_workflow_safety_action(
        request,
        action="archive",
        record=order,
        message=f"Production order {label} archived.",
        meta={"linked_records": linked_labels, "operational_status": operational_status},
    )
    _log_lead_workflow_note(order.lead, request.user, f"Production order {label} archived by {_user_display_name(request.user)}.")
    _record_customer_event(
        customer=order.customer or getattr(order.opportunity, "customer", None) or getattr(order.lead, "customer", None),
        event_type="production_status",
        title="Production archived",
        details=f"Production order {label} archived by {_user_display_name(request.user)}.",
        opportunity=order.opportunity,
        production=order,
    )
    if linked_labels:
        messages.warning(request, f"Production order {label} archived. Linked records were preserved: {', '.join(linked_labels)}.")
    else:
        messages.success(request, f"Production order {label} archived. History is preserved.")
    return redirect(request.POST.get("next") or "production_list")


@require_POST
def production_delete(request, pk):
    order = get_object_or_404(ProductionOrder, pk=pk)
    if not _can_archive_workflow_record(request.user):
        messages.error(request, "You do not have permission to delete production orders.")
        return redirect("production_detail", pk=order.pk)

    operational_status = get_production_operational_status(order)
    if operational_status in {OPERATIONAL_STATUS_READY_TO_SHIP, OPERATIONAL_STATUS_SHIPPED}:
        confirmation = (request.POST.get("confirm_delete") or "").strip().lower()
        if confirmation not in {"delete", "archive", "yes", "1"}:
            messages.error(request, "Ready to ship or shipped production orders require confirmation before archiving.")
            return redirect("production_detail", pk=order.pk)
    _archive_workflow_record(order, request.user)
    label = _workflow_object_label(order)
    _log_workflow_safety_action(
        request,
        action="archive",
        record=order,
        message=f"Production order {label} archived from the legacy delete action.",
        meta={"linked_records": _production_linked_record_labels(order), "operational_status": operational_status},
    )
    messages.success(request, f"Production order {label} archived. History is preserved.")
    return redirect(request.POST.get("next") or "production_list")


@require_POST
def production_attachment_add(request, pk):
    """
    Add one attachment to a production order.
    """
    order = get_object_or_404(ProductionOrder, pk=pk)
    file = request.FILES.get("file")
    name = request.POST.get("name", "")

    if file:
        ProductionOrderAttachment.objects.create(
            order=order,
            file=file,
            name=name or file.name,
        )
        messages.success(request, "Attachment added.")
    else:
        messages.error(request, "No file selected.")

    return redirect("production_detail", pk=order.pk)


@require_POST
def production_attachment_delete(request, pk, att_pk):
    """
    Remove one attachment from a production order.
    """
    order = get_object_or_404(ProductionOrder, pk=pk)
    att = get_object_or_404(ProductionOrderAttachment, pk=att_pk, order=order)
    att.delete()
    messages.success(request, "Attachment deleted.")
    return redirect("production_detail", pk=order.pk)


def production_from_opportunity(request, pk):
    """
    Open or create production order from an opportunity.
    New orders require a CEO/Admin-approved quotation and a CEO/Admin user.
    """
    opportunity = get_object_or_404(Opportunity, pk=pk)
    try:
        customer = _ensure_customer_for_opportunity(opportunity)
    except Exception:
        logger.exception("Failed to ensure customer for opportunity %s", opportunity.pk)
        customer = None

    try:
        approved_costing = CostingHeader.objects.filter(
            opportunity=opportunity,
            status="approved",
            quotation_status=CostingHeader.QUOTATION_STATUS_APPROVED,
            quotation_approved_at__isnull=False,
        ).order_by("-updated_at", "-id").first()
    except (OperationalError, ProgrammingError):
        approved_costing = None

    quick_costing = None
    quick_invoice = None
    if not approved_costing:
        try:
            quick_costing, quick_invoice = paid_full_package_quick_costing_source_for_opportunity(opportunity)
        except (OperationalError, ProgrammingError):
            quick_costing = None
            quick_invoice = None

    po = ProductionOrder.objects.filter(opportunity=opportunity).first()
    created = False

    if not po:
        if not can_approve_costing(request.user):
            messages.error(request, "CEO/Admin approval is required before moving an order to Production.")
            return redirect("opportunity_detail", pk=opportunity.pk)
        if not approved_costing and not quick_costing:
            messages.error(request, "A CEO-approved quotation is required before moving this opportunity to Production.")
            return redirect("opportunity_detail", pk=opportunity.pk)
        try:
            if approved_costing:
                po, created = create_production_order_from_approved_quotation(approved_costing, user=request.user)
            else:
                po, created = create_production_order_from_paid_full_package_quick_costing(
                    quick_costing,
                    invoice=quick_invoice,
                    user=request.user,
                )
        except ProductionOrderCreationError as exc:
            messages.error(request, str(exc))
            return redirect("opportunity_detail", pk=opportunity.pk)
        except Exception as exc:
            logger.exception("Failed to create production order for opportunity %s", opportunity.pk)
            messages.error(request, "Unable to create production order right now.")
            return redirect("opportunity_detail", pk=opportunity.pk)

    elif customer and not po.customer_id:
        po.customer = customer
        po.save(update_fields=["customer"])
    elif approved_costing and not po.costing_header_id:
        po.costing_header = approved_costing
        po.save(update_fields=["costing_header"])

    link_reference_images_to_production(opportunity=opportunity, production_order=po)

    stage_changed = False
    if opportunity.stage != "Production":
        opportunity.stage = "Production"
        opportunity.save(update_fields=["stage"])
        stage_changed = True

    if created or stage_changed:
        _record_customer_event(
            customer=customer,
            event_type="moved_to_production",
            title="Moved to production",
            details=f"Opportunity {opportunity.opportunity_id} moved to production.",
            opportunity=opportunity,
            production=po,
        )

    return redirect("production_detail", pk=po.pk)


def production_next_stage(request, pk):
    """
    Move order to next production stage.
    """
    order = get_object_or_404(ProductionOrder, pk=pk)
    stages = get_sorted_stages(order)

    if not stages:
        messages.error(request, "No production stages found for this order.")
        return redirect("production_detail", pk=pk)

    current_index = -1
    current_stage = getattr(order, "current_stage", None)

    if current_stage:
        for i, s in enumerate(stages):
            if s.id == current_stage.id:
                current_index = i
                break

    if current_index == -1:
        next_stage = stages[0]
    elif current_index + 1 < len(stages):
        next_stage = stages[current_index + 1]
    else:
        next_stage = stages[-1]

    today = timezone.now().date()

    for s in stages:
        if s.id == next_stage.id:
            if s.actual_start is None:
                s.actual_start = today
            s.status = "in_progress"
        else:
            if s.status == "in_progress":
                s.status = "done"
                if s.actual_end is None:
                    s.actual_end = today
        s.save()

    order.current_stage = next_stage
    order.status = "done" if next_stage.stage_key == "shipping" else "in_progress"
    order.save()
    sync_operational_status(order)

    messages.success(request, f"Moved to stage: {next_stage.get_stage_key_display()}")
    return redirect("production_detail", pk=pk)


def production_stage_edit(request, stage_id):
    """
    Edit one stage record.
    """
    stage = get_object_or_404(ProductionStage, pk=stage_id)

    if request.method == "POST":
        form = ProductionStageForm(request.POST, instance=stage)
        if form.is_valid():
            form.save()
            sync_operational_status(stage.order)
            messages.success(request, "Stage updated.")
            return redirect("production_detail", pk=stage.order_id)
    else:
        form = ProductionStageForm(instance=stage)

    return render(
        request,
        "crm/production_stage_edit.html",
        {
            "stage": stage,
            "form": form,
        },
    )


def production_ai_help(request, pk):
    """
    Ask AI to give advice about this production order.
    Saves the answer in order.ai_note and returns text for ajax call.
    """
    order = get_object_or_404(
        ProductionOrder.objects.prefetch_related("stages"),
        pk=pk,
    )

    stages = get_sorted_stages(order)

    stage_lines = []
    for s in stages:
        name = s.display_name or s.get_stage_key_display()
        stage_lines.append(
            f"- {name}: status {s.get_status_display()}, "
            f"planned {s.planned_start or 'none'} to {s.planned_end or 'none'}, "
            f"actual {s.actual_start or 'none'} to {s.actual_end or 'none'}"
        )
    stages_text = "\n".join(stage_lines) or "No stages have been created yet."

    current_stage_name = (
        order.current_stage.display_name
        if getattr(order, "current_stage", None)
        else "Not set"
    )

    mode = request.POST.get("mode", "summary")
    user_text = (request.POST.get("user_text") or "").strip()

    base_prompt = f"""
You are a clothing factory production planner.
Use short simple English.
Write clear bullet points.

Order title: {order.title}
Purchase order number: {order.purchase_order_number}
Order type: {order.get_order_type_display()}
Total quantity: {order.qty_total}
Reject quantity: {order.qty_reject}
Status: {order.get_status_display()}
Current stage: {current_stage_name}
Sample deadline: {order.sample_deadline}
Bulk deadline: {order.bulk_deadline}

Stages:
{stages_text}
"""

    if mode == "summary":
        task = """
Give a short summary of this order.
Use at most four bullet points:
1) Overall status
2) What is going well
3) Main risk
4) What we should do this week
"""
    elif mode == "stage":
        task = """
Focus on the current stage only.
Give three bullet points:
1) Main goal of this stage
2) Risk or bottleneck
3) Simple action plan for the team today
"""
    elif mode == "timeline":
        task = """
Look at the deadlines and stages.
Suggest how to keep the timeline safe.
Give three to five bullet points only.
Mark any stage that must start earlier or faster.
"""
    elif mode == "delay":
        task = """
Check for delays or risk of delay.
Explain in three bullet points:
1) If there is a delay or risk
2) The main reason
3) A simple recovery plan that we can follow
"""
    elif mode == "dpr":
        task = """
Write a daily production report for this order.
Use short lines like a report we can send to management.
Include:
- Today status per stage if possible
- Any issues
- Plan for tomorrow
Keep it under ten lines.
"""
    elif mode == "client":
        task = """
Write a short update email for the customer about this order.
Use friendly very simple tone.
Keep it under twelve lines.
Include status, next steps, and if there is any risk or delay.
Do not invent dates that are not in the data.
"""
    elif mode == "tasks":
        task = """
List action items.
Give two sections:
1) Factory team actions
2) Office or sales team actions
Each section can have three to five bullet points.
Use very short sentences.
"""
    elif mode == "chat" and user_text:
        task = f"""
The user asked this question about the order:
\"\"\"{user_text}\"\"\"

Answer in short clear English.
Keep it under ten bullet points or lines.
"""
    else:
        task = """
Give a short summary and key actions for this order.
Use four bullet points only.
"""

    full_prompt = base_prompt + "\n\n" + task

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": full_prompt}],
            max_tokens=400,
        )
        text = response.choices[0].message.content.strip()

        if order.ai_note:
            order.ai_note += "\n\n---\n\n" + text
        else:
            order.ai_note = text
        order.save()

        if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.POST.get("ajax") == "1":
            return JsonResponse({"ok": True, "text": text})

        messages.success(request, "AI advice updated for this order.")
    except Exception:
        logger.exception("AI production help failed")
        if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.POST.get("ajax") == "1":
            return JsonResponse(
                {"ok": False, "error": "Could not get AI advice right now."},
                status=500,
            )
        messages.error(request, "Could not get AI advice right now.")

    return redirect("production_detail", pk=pk)


def production_dpr(request, pk):
    """
    Simple daily production report.
    Appends a line in order.notes.
    """
    order = get_object_or_404(ProductionOrder, pk=pk)

    if request.method == "POST":
        qty_raw = request.POST.get("dpr_qty")
        note = request.POST.get("dpr_note", "").strip()

        if not qty_raw:
            messages.error(request, "Please enter quantity for the daily report.")
            return redirect("production_detail", pk=pk)

        try:
            qty_val = int(qty_raw)
        except ValueError:
            messages.error(request, "Quantity must be a number.")
            return redirect("production_detail", pk=pk)

        today = timezone.now().date().isoformat()
        line = f"[{today}] DPR {qty_val} pieces"
        if note:
            line += f" - {note}"

        if order.notes:
            order.notes = order.notes + "\n" + line
        else:
            order.notes = line

        order.save()
        messages.success(request, "Daily production report added.")
        return redirect("production_detail", pk=pk)

    return redirect("production_detail", pk=pk)


def production_order_sheet_pdf(request, pk):
    order = get_object_or_404(
        ProductionOrder.objects.select_related("customer", "lead", "opportunity")
        .prefetch_related(
            "materials__inventory_item",
            "lines",
            "fabrics",
            "accessories",
            "trims",
            "threads",
        ),
        pk=pk,
    )

    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        from reportlab.pdfbase import pdfmetrics
    except ImportError:
        return HttpResponse(
            "ReportLab is not installed yet. Ask your dev to install 'reportlab' to enable PDF.",
            content_type="text/plain",
        )

    response = HttpResponse(content_type="application/pdf")
    filename = f"production_order_sheet_{order.purchase_order_number or order.pk}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    p = canvas.Canvas(response, pagesize=letter)
    width, height = letter
    y = height - 50

    def ensure_space(y_pos, needed, font_name="Helvetica", font_size=10):
        if y_pos - needed < 50:
            p.showPage()
            y_pos = height - 50
            p.setFont(font_name, font_size)
        return y_pos

    def draw_wrapped(text, x_pos, y_pos, max_width, font_name="Helvetica", font_size=10, line_height=12):
        p.setFont(font_name, font_size)
        words = str(text).split()
        if not words:
            return y_pos
        line = ""
        for word in words:
            test = (line + " " + word).strip()
            if pdfmetrics.stringWidth(test, font_name, font_size) <= max_width:
                line = test
            else:
                y_pos = ensure_space(y_pos, line_height, font_name, font_size)
                p.drawString(x_pos, y_pos, line)
                y_pos -= line_height
                line = word
        if line:
            y_pos = ensure_space(y_pos, line_height, font_name, font_size)
            p.drawString(x_pos, y_pos, line)
            y_pos -= line_height
        return y_pos

    def draw_label_value(label, value, x_pos, y_pos):
        p.setFont("Helvetica-Bold", 10)
        p.drawString(x_pos, y_pos, f"{label}:")
        p.setFont("Helvetica", 10)
        p.drawString(x_pos + 70, y_pos, str(value))
        return y_pos - 12

    def draw_size_table(size_grid, x_pos, y_pos):
        if not size_grid:
            return y_pos
        col_w = 55
        row_h = 12
        table_width = col_w * len(size_grid)
        p.setFont("Helvetica-Bold", 9)
        y_pos = ensure_space(y_pos, row_h, "Helvetica-Bold", 9)
        for idx, item in enumerate(size_grid):
            p.drawString(x_pos + (idx * col_w) + 2, y_pos, str(item.get("label", "")))
        y_pos -= row_h
        p.setFont("Helvetica", 9)
        y_pos = ensure_space(y_pos, row_h, "Helvetica", 9)
        for idx, item in enumerate(size_grid):
            qty = item.get("qty") or 0
            p.drawString(x_pos + (idx * col_w) + 2, y_pos, str(qty))
        y_pos -= row_h
        p.line(x_pos, y_pos + (row_h * 2) + 2, x_pos + table_width, y_pos + (row_h * 2) + 2)
        p.line(x_pos, y_pos + 2, x_pos + table_width, y_pos + 2)
        return y_pos

    def draw_materials_header(y_pos):
        p.setFont("Helvetica", 10)
        p.drawString(50, y_pos, "Material")
        p.drawString(230, y_pos, "Category")
        p.drawString(320, y_pos, "Qty")
        p.drawString(370, y_pos, "Unit")
        p.drawString(420, y_pos, "In stock")
        p.drawString(490, y_pos, "Code/SKU")
        y_pos -= 12
        p.line(50, y_pos, width - 50, y_pos)
        y_pos -= 12
        return y_pos

    p.setFont("Helvetica-Bold", 16)
    p.drawString(50, y, "Order Sheet")
    y -= 24

    # product image (if any)
    image_path = None
    if order.product and order.product.image:
        image_path = order.product.image.path
    elif order.style_image:
        image_path = order.style_image.path

    if image_path:
        try:
            p.drawImage(image_path, width - 170, height - 140, width=110, height=110, preserveAspectRatio=True, mask="auto")
        except Exception:
            pass

    order_lines = _production_order_lines(order)

    p.setFont("Helvetica", 11)
    header_lines = [
        f"Purchase Order Number: {order.purchase_order_number or order.pk}",
        f"Title: {order.title}",
        f"Customer: {(order.customer.account_brand if order.customer else '') or 'Not set'}",
        f"Product ID: {(order.product.product_code if order.product else '-')}",
        f"Total pieces: {order.qty_total}  |  Reject: {order.qty_reject}",
        f"Sample date: {order.sample_deadline or '-'}  |  Bulk date: {order.bulk_deadline or '-'}",
        f"Factory: {order.get_factory_location_display()}  |  Order type: {order.get_order_type_display()}",
        f"Status: {order.get_status_display()}",
        f"Product lines: {len(order_lines)}",
    ]
    for line in header_lines:
        p.drawString(50, y, line)
        y -= 14

    y -= 6
    y = ensure_space(y, 20, "Helvetica-Bold", 12)
    p.setFont("Helvetica-Bold", 12)
    p.drawString(50, y, "Product lines")
    y -= 16

    for idx, line in enumerate(order_lines, start=1):
        y = ensure_space(y, 16, "Helvetica-Bold", 11)
        p.setFont("Helvetica-Bold", 11)
        p.drawString(50, y, f"Line {idx}")
        y -= 14

        p.setFont("Helvetica", 10)
        y = draw_wrapped(f"Style name: {line.get('style_name') or '-'}", 50, y, 500)
        y = draw_wrapped(f"Color info: {line.get('color_info') or '-'}", 50, y, 500)

        if line.get("size_total"):
            y = ensure_space(y, 12, "Helvetica-Bold", 10)
            p.setFont("Helvetica-Bold", 10)
            p.drawString(50, y, "Size ratio")
            y -= 12
            y = draw_size_table(line.get("size_grid"), 50, y)
            y = ensure_space(y, 12, "Helvetica", 10)
            p.setFont("Helvetica", 10)
            p.drawString(50, y, f"Line total: {line.get('size_total')}")
            y -= 12
        else:
            y = ensure_space(y, 12, "Helvetica", 10)
            p.setFont("Helvetica", 10)
            p.drawString(50, y, "No size ratio set.")
            y -= 12

        y = draw_wrapped(f"Accessories & trims: {line.get('accessories_note') or '-'}", 50, y, 500)
        y = draw_wrapped(f"Packaging: {line.get('packaging_note') or '-'}", 50, y, 500)
        y = draw_wrapped(f"Extra notes: {line.get('extra_order_note') or '-'}", 50, y, 500)
        y -= 6

    # library selections
    y = ensure_space(y, 20, "Helvetica-Bold", 12)
    p.setFont("Helvetica-Bold", 12)
    p.drawString(50, y, "Library selections")
    y -= 16
    p.setFont("Helvetica", 10)
    fabrics_line = ", ".join([f"{f.fabric_code} {f.name}" for f in order.fabrics.all()]) or "None"
    accessories_line = ", ".join([f"{a.accessory_code} {a.name}" for a in order.accessories.all()]) or "None"
    trims_line = ", ".join([f"{t.trim_code} {t.name}" for t in order.trims.all()]) or "None"
    threads_line = ", ".join([f"{t.thread_code} {t.name}" for t in order.threads.all()]) or "None"
    y = draw_wrapped(f"Fabrics: {fabrics_line}", 50, y, 500)
    y = draw_wrapped(f"Accessories: {accessories_line}", 50, y, 500)
    y = draw_wrapped(f"Trims: {trims_line}", 50, y, 500)
    y = draw_wrapped(f"Threads: {threads_line}", 50, y, 500)
    y -= 6

    # materials + inventory
    y = ensure_space(y, 24, "Helvetica-Bold", 12)
    p.setFont("Helvetica-Bold", 12)
    p.drawString(50, y, "Materials and inventory")
    y -= 18

    y = draw_materials_header(y)

    materials = order.materials.select_related("inventory_item")
    if not materials:
        p.drawString(50, y, "No materials selected yet.")
        y -= 16
    else:
        for line in materials:
            item = line.inventory_item
            name = item.name
            category = item.get_category_display()
            qty = line.quantity if line.quantity is not None else "-"
            unit = line.unit_type or item.unit_type or "-"
            code = item.code or item.sku or "-"
            stock = item.quantity if item.quantity is not None else "-"

            img_x = 50
            text_x = 70
            if item.image:
                try:
                    p.drawImage(item.image.path, img_x, y - 8, width=14, height=14, preserveAspectRatio=True, mask="auto")
                except Exception:
                    text_x = 50

            p.drawString(text_x, y, str(name)[:24])
            p.drawString(230, y, str(category)[:14])
            p.drawString(320, y, str(qty))
            p.drawString(370, y, str(unit))
            p.drawString(420, y, str(stock))
            p.drawString(490, y, str(code)[:18])
            y -= 14

            if y < 80:
                p.showPage()
                y = height - 50
                y = draw_materials_header(y)

    p.showPage()
    p.save()
    return response


def production_packing_list_pdf(request, pk):
    order = get_object_or_404(
        ProductionOrder.objects.select_related("customer", "lead", "opportunity"),
        pk=pk,
    )

    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        from reportlab.lib import colors
        from reportlab.platypus import Table, TableStyle
        from reportlab.lib.utils import ImageReader
    except ImportError:
        return HttpResponse(
            "ReportLab is not installed yet. Ask your dev to install 'reportlab' to enable PDF.",
            content_type="text/plain",
        )

    response = HttpResponse(content_type="application/pdf")
    filename = f"packing_list_{order.purchase_order_number or order.pk}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    p = canvas.Canvas(response, pagesize=letter)
    width, height = letter

    def draw_header():
        y = height - 48
        p.setFont("Helvetica-Bold", 16)
        p.drawString(40, y, "FINAL PACKING LIST")
        y -= 18

        brand = (order.customer.account_brand if order.customer else "") or (order.lead.account_brand if order.lead else "") or (order.title or "")
        color = order.color_info or "Not set"
        p.setFont("Helvetica", 10)
        p.drawString(40, y, f"Brand: {brand or 'Not set'}")
        y -= 14
        p.drawString(40, y, f"Color: {color}")
        y -= 10

        # image on the right
        img_path = None
        if getattr(order, "style_image", None) and getattr(order.style_image, "path", ""):
            img_path = order.style_image.path
        elif getattr(order, "product", None) and getattr(order.product, "image", None) and getattr(order.product.image, "path", ""):
            img_path = order.product.image.path

        if img_path and os.path.exists(img_path):
            try:
                img = ImageReader(img_path)
                img_w, img_h = 110, 110
                p.drawImage(img, width - 40 - img_w, height - 58 - img_h, width=img_w, height=img_h, preserveAspectRatio=True, mask="auto")
            except Exception:
                pass
        return y

    y = draw_header()

    # summary line
    p.setFont("Helvetica", 10)
    p.drawString(40, y, f"Purchase Order Number: {order.purchase_order_number or order.pk}")
    p.drawString(220, y, f"Total PCS: {order.qty_total or 0}")
    p.drawString(360, y, f"Reject: {order.qty_reject or 0}")
    y -= 18

    # table
    size_labels = ["XS", "S", "M", "L", "XL", "XXL", "XXXL"]
    label_map = {"2XL": "XXL", "3XL": "XXXL"}

    table_data = [["Box / Style"] + size_labels + ["Total PCS"]]
    order_lines = _production_order_lines(order)
    for idx, line in enumerate(order_lines, start=1):
        name = line.get("style_name") or f"Style {idx}"
        row = [f"Box {idx} - {name}"]

        grid = line.get("size_grid") or []
        grid_map = {}
        for item in grid:
            lbl = item.get("label")
            qty = item.get("qty") or 0
            if lbl in label_map:
                lbl = label_map[lbl]
            if lbl:
                grid_map[lbl] = qty

        total = 0
        for lbl in size_labels:
            qty = grid_map.get(lbl, 0)
            total += qty
            row.append(str(qty or ""))
        row.append(str(total or line.get("size_total") or ""))
        table_data.append(row)

    col_widths = [150, 35, 35, 35, 35, 35, 40, 45, 55]
    tbl = Table(table_data, colWidths=col_widths, hAlign="LEFT")
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.black),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (1, 1), (-1, -1), "CENTER"),
                ("ALIGN", (0, 0), (0, -1), "LEFT"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.lightgrey]),
            ]
        )
    )

    table_y = y - 10
    _, table_height = tbl.wrapOn(p, width - 80, height - 200)
    if table_y - table_height < 80:
        p.showPage()
        table_y = draw_header() - 40
    tbl.drawOn(p, 40, table_y - table_height)

    p.showPage()
    p.save()
    return response


def chatter_feed(request):
    """
    Consolidated chatter feed for leads, opportunities, and production.
    """
    source = (request.GET.get("source") or "all").strip().lower()
    source_modules = {"lead": "leads", "opportunity": "opportunities", "production": "production"}
    if source in source_modules and not can_access_operations_module(request.user, source_modules[source]):
        return HttpResponseForbidden("You do not have access to this chatter module.")
    comments_qs = visible_chatter_comments(request.user).order_by("-created_at")

    if source == "lead":
        comments_qs = comments_qs.filter(opportunity__isnull=True, production__isnull=True)
    elif source == "opportunity":
        comments_qs = comments_qs.filter(opportunity__isnull=False)
    elif source == "production":
        comments_qs = comments_qs.filter(production__isnull=False)

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        if action == "add_chatter":
            note_text = (request.POST.get("comment_text") or "").strip()
            attachment = request.FILES.get("attachment")
            link_type = (request.POST.get("link_type") or "").strip().lower()
            link_id = (request.POST.get("link_id") or "").strip()

            if not note_text and not attachment:
                messages.error(request, "Please write a note or attach a file first.")
                return redirect("chatter_feed")

            target = resolve_chatter_target(request.user, link_type, link_id)
            if target is None:
                return HttpResponseForbidden("You do not have access to the selected chatter record.")
            author_name = employee_display_name(request.user)
            lead = target["lead"]
            opportunity = target["opportunity"]
            production = target["production"]

            if note_text:
                content = note_text
            else:
                content = f"Attachment: {attachment.name}"

            comment = LeadComment.objects.create(
                lead=lead,
                opportunity=opportunity,
                production=production,
                author=author_name,
                author_user=request.user,
                content=content,
                attachment=attachment,
            )
            _send_chatter_mentions(request, comment)

            messages.success(request, "Chatter note saved.")
            return redirect("chatter_feed")

    User = get_user_model()
    team_members = User.objects.filter(
        is_active=True,
        employee_profile__status__in=EmployeeProfile.MENTIONABLE_STATUSES,
    ).select_related("employee_profile").order_by(
        "employee_profile__display_name", "first_name", "username"
    )

    return render(
        request,
        "crm/chatter_feed.html",
        {
            "comments": comments_qs,
            "source": source,
            "team_members": team_members,
        },
    )

# ==============================
# SHIPPING VIEWS
# ==============================

from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.core.mail import send_mail
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .models import Shipment, ProductionOrder, Opportunity
from .forms import ShipmentForm
from .services.shipment_notifications import SHIPMENT_NOTIFY_STATUSES, shipment_email_target, validate_shipment_email
from .tasks import send_shipment_notification_async


def form_fields(form_class):
    return set(getattr(form_class, "base_fields", {}).keys())


def _order_field_name():
    """
    Shipment model may use:
    - production_order (new)
    - order (old)
    """
    field_names = {f.name for f in Shipment._meta.fields}
    if "production_order" in field_names:
        return "production_order"
    if "order" in field_names:
        return "order"
    return None


ORDER_FIELD = _order_field_name()


def _select_related_fields():
    fields = []
    if ORDER_FIELD:
        fields.append(ORDER_FIELD)

    model_fields = {f.name for f in Shipment._meta.fields}
    if "opportunity" in model_fields:
        fields.append("opportunity")
    if "customer" in model_fields:
        fields.append("customer")
    return fields


def _shipment_notify_requested(request):
    return (request.POST.get("notify_customer") or "").strip().lower() in {"1", "on", "true", "yes"}


def _queue_shipment_status_notification(shipment, status_key, *, force=False):
    email_to, _name = shipment_email_target(shipment)
    if not email_to:
        logger.warning("Shipment notification queue skipped: no recipient", extra={"shipment_id": shipment.pk})
        return False, "missing_recipient"
    if not validate_shipment_email(email_to):
        logger.warning(
            "Shipment notification queue skipped: invalid recipient",
            extra={"shipment_id": shipment.pk, "email": email_to},
        )
        return False, "invalid_recipient"

    def enqueue():
        try:
            options = {"retry": False}
            queue_name = getattr(settings, "SHIPMENT_NOTIFICATION_QUEUE", "") or ""
            if queue_name:
                options["queue"] = queue_name
            send_shipment_notification_async.apply_async(
                args=[shipment.pk, status_key],
                kwargs={"force": force},
                **options,
            )
        except Exception:
            logger.exception(
                "Shipment notification queue failed",
                extra={"shipment_id": shipment.pk, "status": status_key},
            )
            return False
        return True

    if connection.in_atomic_block:
        transaction.on_commit(enqueue)
        return True, "queued"

    if enqueue():
        return True, "queued"
    return False, "queue_failed"


def _handle_shipment_status_change(request, shipment, old_status, *, notify_customer=False):
    new_status = shipment.status
    if new_status == old_status:
        return False
    if new_status not in SHIPMENT_NOTIFY_STATUSES:
        return False
    if shipment.last_notified_status == new_status:
        return False
    if not notify_customer:
        return False

    queued, reason = _queue_shipment_status_notification(shipment, new_status)
    if queued:
        messages.info(request, "Customer shipment notification queued.")
        return True
    if reason == "missing_recipient":
        messages.warning(request, "Shipment saved, but no customer email address was found for notification.")
    elif reason == "invalid_recipient":
        messages.warning(request, "Shipment saved, but the customer email address is invalid.")
    else:
        messages.warning(request, "Shipment saved, but notification could not be queued.")
    return False


def shipment_list(request):
    can_view_shipping_costs = can_view_lifecycle_profit(request.user)
    if request.method == "POST":
        ship_id = (request.POST.get("shipment_id") or "").strip()
        new_status = (request.POST.get("status") or "").strip()
        if ship_id and new_status:
            shipment = Shipment.objects.filter(pk=ship_id).first()
            if shipment:
                old_status = shipment.status
                shipment.status = new_status
                if new_status in {"shipped", "out_for_delivery", "delivered"} and not shipment.ship_date:
                    shipment.ship_date = timezone.localdate()
                if new_status == "delivered" and not shipment.delivered_at:
                    shipment.delivered_at = timezone.now()
                shipment.save()
                _sync_production_after_delivered_shipment(shipment)
                _handle_shipment_status_change(
                    request,
                    shipment,
                    old_status,
                    notify_customer=_shipment_notify_requested(request),
                )
                create_lifecycle_from_shipping(shipment, user=request.user)
                messages.success(request, f"Shipment status updated to {shipment.get_status_display()}.")
        return redirect("shipment_list")

    qs = Shipment.objects.all()
    sr = _select_related_fields()
    if sr:
        qs = qs.select_related(*sr)
    if ORDER_FIELD:
        qs = qs.prefetch_related(
            Prefetch(
                f"{ORDER_FIELD}__invoices",
                queryset=Invoice.objects.order_by("-created_at", "-id"),
            )
        )

    status_filter = (request.GET.get("status") or "all").strip().lower()
    carrier_filter = (request.GET.get("carrier") or "all").strip().lower()
    search_query = (request.GET.get("q") or "").strip()

    if status_filter != "all":
        qs = qs.filter(status=status_filter)

    carrier_keys = {key for key, _label in Shipment.CARRIER_CHOICES}
    if carrier_filter != "all" and carrier_filter in carrier_keys:
        qs = qs.filter(carrier=carrier_filter)

    if search_query:
        qs = qs.filter(
            Q(tracking_number__icontains=search_query)
            | Q(customer__account_brand__icontains=search_query)
            | Q(customer__contact_name__icontains=search_query)
            | Q(customer__shipping_city__icontains=search_query)
            | Q(customer__shipping_country__icontains=search_query)
            | Q(customer__city__icontains=search_query)
            | Q(customer__country__icontains=search_query)
            | Q(opportunity__opportunity_id__icontains=search_query)
            | ProductionOrder.identifier_search_query(search_query, "order__order_code")
            | Q(order__title__icontains=search_query)
        )

    shipments = qs.order_by("-ship_date", "-created_at")
    today = timezone.localdate()
    delayed_cutoff = today - timedelta(days=14)

    total_shipments = shipments.count()
    in_transit_shipments = shipments.filter(status__in=["shipped", "out_for_delivery"]).count()
    delivered_shipments = shipments.filter(status="delivered").count()
    delayed_shipments = shipments.filter(
        status__in=["shipped", "out_for_delivery"],
        ship_date__lt=delayed_cutoff,
    ).count()
    pending_tracking_shipments = shipments.filter(
        Q(tracking_number__isnull=True) | Q(tracking_number="")
    ).count()
    total_boxes = sum((s.box_count or 0) for s in shipments)
    total_weight = sum((s.total_weight_kg or 0) for s in shipments)
    total_cost_bdt = sum((s.cost_bdt or Decimal("0")) for s in shipments) if can_view_shipping_costs else None
    total_cost_cad = sum((s.cost_cad or Decimal("0")) for s in shipments) if can_view_shipping_costs else None

    shipment_rows = []
    for shipment in shipments:
        customer = getattr(shipment, "customer", None)
        city = ""
        country = ""
        client_name = "Client not linked"
        if customer:
            city = (
                getattr(customer, "shipping_city", "")
                or getattr(customer, "city", "")
                or ""
            )
            country = (
                getattr(customer, "shipping_country", "")
                or getattr(customer, "country", "")
                or ""
            )
            client_name = (
                getattr(customer, "account_brand", "")
                or getattr(customer, "contact_name", "")
                or str(customer)
            )

        shipment_rows.append(
            {
                "shipment": shipment,
                "shipment_number": f"SHP-{shipment.pk:05d}",
                "client_name": client_name,
                "destination_city": city or "-",
                "destination_country": country or "-",
                "is_delayed": (
                    shipment.status in {"shipped", "out_for_delivery"}
                    and shipment.ship_date
                    and shipment.ship_date < delayed_cutoff
                ),
            }
        )

    return render(
        request,
        "crm/shipment_list.html",
        {
            "shipments": shipments,
            "shipment_rows": shipment_rows,
            "total_shipments": total_shipments,
            "in_transit_shipments": in_transit_shipments,
            "delivered_shipments": delivered_shipments,
            "delayed_shipments": delayed_shipments,
            "pending_tracking_shipments": pending_tracking_shipments,
            "total_boxes": total_boxes,
            "total_weight": total_weight,
            "total_cost_bdt": total_cost_bdt,
            "total_cost_cad": total_cost_cad,
            "can_view_shipping_costs": can_view_shipping_costs,
            "status_filter": status_filter,
            "carrier_filter": carrier_filter,
            "search_query": search_query,
            "status_choices": Shipment.STATUS_CHOICES,
            "carrier_choices": Shipment.CARRIER_CHOICES,
        },
    )


def shipment_add(request):
    """
    Create a new shipment from menu.
    If your ShipmentForm includes order or production_order, it will show.
    """
    can_edit_internal_costing = can_view_lifecycle_profit(request.user)
    if request.method == "POST":
        form = ShipmentForm(request.POST, can_edit_internal_costing=can_edit_internal_costing)
        if form.is_valid():
            shipment = form.save(commit=False)
            rate = form.cleaned_data.get("rate_bdt_per_cad")
            if rate not in [None, ""]:
                shipment.rate_bdt_per_cad = rate
            blocked_response = _block_inactive_quick_revision_shipment(request, shipment)
            if blocked_response:
                return blocked_response
            shipment.save()
            _handle_shipment_status_change(
                request,
                shipment,
                None,
                notify_customer=_shipment_notify_requested(request),
            )
            create_lifecycle_from_shipping(shipment, user=request.user)
            messages.success(request, "Shipment created.")
            return redirect("shipment_detail", pk=shipment.pk)

        messages.error(request, "Could not save. Please fix the form errors.")
        return render(
            request,
            "crm/shipment_form.html",
            {
                "form": form,
                "is_edit": False,
                "order": None,
                "order_field": ORDER_FIELD,
                "can_view_shipping_costs": can_edit_internal_costing,
            },
        )

    form = ShipmentForm(
        initial={"ship_date": timezone.localdate()},
        can_edit_internal_costing=can_edit_internal_costing,
    )
    return render(
        request,
        "crm/shipment_form.html",
        {
            "form": form,
            "is_edit": False,
            "order": None,
            "order_field": ORDER_FIELD,
            "can_view_shipping_costs": can_edit_internal_costing,
        },
    )


def _shipment_source_quick_costing(shipment, order=None):
    source_order = order
    if source_order is None:
        source_order = getattr(shipment, "order", None) or getattr(shipment, "production_order", None)
    if not source_order:
        return None
    return getattr(source_order, "source_quick_costing", None)


def _block_inactive_quick_revision_shipment(request, shipment, order=None):
    source_quick_costing = _shipment_source_quick_costing(shipment, order=order)
    if not source_quick_costing:
        return None
    latest_approved_revision = source_quick_costing.latest_approved_revision()
    if (
        source_quick_costing.status not in QuickCosting.ACTIVE_APPROVED_STATUSES
        or (
            latest_approved_revision
            and latest_approved_revision.pk != source_quick_costing.pk
        )
    ):
        messages.error(
            request,
            "Shipment creation is blocked because this production order is not linked to the latest approved Quick Costing revision.",
        )
        return redirect(
            "quick_costing_detail",
            pk=latest_approved_revision.pk if latest_approved_revision else source_quick_costing.pk,
        )
    return None


def shipment_detail(request, pk):
    qs = Shipment.objects.all()
    sr = _select_related_fields()
    if sr:
        qs = qs.select_related(*sr)
    detail_prefetches = [
        Prefetch(
            "order_lifecycles",
            queryset=OrderLifecycle.objects.order_by("-updated_at", "-id"),
        )
    ]
    if ORDER_FIELD:
        detail_prefetches.append(
            Prefetch(
                f"{ORDER_FIELD}__invoices",
                queryset=Invoice.objects.order_by("-created_at", "-id"),
            )
        )
    qs = qs.prefetch_related(*detail_prefetches)

    shipment = get_object_or_404(qs, pk=pk)
    lifecycles = list(shipment.order_lifecycles.all())
    lifecycle = lifecycles[0] if lifecycles else None
    product_reference_images = []
    product_snapshot = None
    if getattr(shipment, "order", None):
        product_reference_images = list(reference_images_for_production(shipment.order))
        product_snapshot = product_snapshot_for_production(
            shipment.order,
            product_reference_images[0] if product_reference_images else None,
        )
    elif getattr(shipment, "opportunity", None):
        product_reference_images = list(reference_images_for_opportunity(shipment.opportunity))
        product_snapshot = product_snapshot_for_opportunity(
            shipment.opportunity,
            product_reference_images[0] if product_reference_images else None,
        )
    elif getattr(shipment, "lead", None):
        product_reference_images = list(reference_images_for_lead(shipment.lead))
        product_snapshot = product_snapshot_for_lead(
            shipment.lead,
            product_reference_images[0] if product_reference_images else None,
        )
    can_view_shipping_costs = can_view_lifecycle_profit(request.user)
    workflow_visibility = build_workflow_visibility_context(
        "shipping",
        user=request.user,
        shipment=shipment,
        production_order=getattr(shipment, "order", None),
        opportunity=getattr(shipment, "opportunity", None),
        lifecycle=lifecycle,
    )
    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        if action == "update_status":
            new_status = (request.POST.get("status") or "").strip()
            if new_status:
                old_status = shipment.status
                shipment.status = new_status
                if new_status in {"shipped", "out_for_delivery", "delivered"} and not shipment.ship_date:
                    shipment.ship_date = timezone.localdate()
                if new_status == "delivered" and not shipment.delivered_at:
                    shipment.delivered_at = timezone.now()
                shipment.save()
                _sync_production_after_delivered_shipment(shipment)
                _handle_shipment_status_change(
                    request,
                    shipment,
                    old_status,
                    notify_customer=_shipment_notify_requested(request),
                )
                create_lifecycle_from_shipping(shipment, user=request.user)
                messages.success(request, f"Shipment status updated to {shipment.get_status_display()}.")
            return redirect("shipment_detail", pk=pk)
    return render(
        request,
        "crm/shipment_detail.html",
        {
            "shipment": shipment,
            "lifecycle": lifecycle,
            "product_snapshot": product_snapshot,
            "product_reference_images": product_reference_images,
            "can_view_shipping_costs": can_view_shipping_costs,
            **workflow_visibility,
        },
    )


def shipment_edit(request, pk):
    shipment = get_object_or_404(Shipment, pk=pk)
    can_edit_internal_costing = can_view_lifecycle_profit(request.user)

    if request.method == "POST":
        form = ShipmentForm(
            request.POST,
            instance=shipment,
            can_edit_internal_costing=can_edit_internal_costing,
        )
        if form.is_valid():
            old_status = shipment.status
            shipment = form.save(commit=False)
            rate = form.cleaned_data.get("rate_bdt_per_cad")
            if rate not in [None, ""]:
                shipment.rate_bdt_per_cad = rate
            shipment.save()
            _sync_production_after_delivered_shipment(shipment)
            _handle_shipment_status_change(
                request,
                shipment,
                old_status,
                notify_customer=_shipment_notify_requested(request),
            )
            create_lifecycle_from_shipping(shipment, user=request.user)
            messages.success(request, "Shipment updated.")
            return redirect("shipment_detail", pk=pk)

        messages.error(request, "Could not save. Please fix the form errors.")
        return render(
            request,
            "crm/shipment_form.html",
            {
                "form": form,
                "is_edit": True,
                "shipment": shipment,
                "order": getattr(shipment, ORDER_FIELD, None) if ORDER_FIELD else None,
                "order_field": ORDER_FIELD,
                "can_view_shipping_costs": can_edit_internal_costing,
            },
        )

    form = ShipmentForm(instance=shipment, can_edit_internal_costing=can_edit_internal_costing)
    return render(
        request,
        "crm/shipment_form.html",
        {
            "form": form,
            "is_edit": True,
            "shipment": shipment,
            "order": getattr(shipment, ORDER_FIELD, None) if ORDER_FIELD else None,
            "order_field": ORDER_FIELD,
            "can_view_shipping_costs": can_edit_internal_costing,
        },
    )


def shipment_delete(request, pk):
    shipment = get_object_or_404(Shipment, pk=pk)

    if request.method != "POST":
        messages.error(request, "Delete must be submitted from the shipment list.")
        return redirect("shipment_list")

    shipment.delete()
    messages.success(request, "Shipment deleted.")
    return redirect("shipment_list")


def shipping_add_for_opportunity(request, pk):
    """
    Create a shipment from an opportunity.
    """
    opportunity = get_object_or_404(Opportunity, pk=pk)
    customer = getattr(opportunity, "customer", None)
    can_edit_internal_costing = can_view_lifecycle_profit(request.user)

    if request.method == "POST":
        form = ShipmentForm(request.POST, can_edit_internal_costing=can_edit_internal_costing)
        if form.is_valid():
            shipment = form.save(commit=False)
            rate = form.cleaned_data.get("rate_bdt_per_cad")
            if rate not in [None, ""]:
                shipment.rate_bdt_per_cad = rate

            if hasattr(shipment, "opportunity"):
                shipment.opportunity = opportunity
            if hasattr(shipment, "customer"):
                shipment.customer = customer

            if not shipment.ship_date:
                shipment.ship_date = timezone.localdate()

            shipment.save()
            _sync_production_after_delivered_shipment(shipment)
            _handle_shipment_status_change(
                request,
                shipment,
                None,
                notify_customer=_shipment_notify_requested(request),
            )
            create_lifecycle_from_shipping(shipment, user=request.user)
            messages.success(request, "Shipment created for this opportunity.")
            return redirect("opportunity_detail", pk=opportunity.pk)

        messages.error(request, "Could not save. Please fix the form errors.")
        return render(
            request,
            "crm/shipment_form.html",
            {
                "form": form,
                "opportunity": opportunity,
                "is_edit": False,
                "order": None,
                "order_field": ORDER_FIELD,
                "can_view_shipping_costs": can_edit_internal_costing,
            },
        )

    initial = {"ship_date": timezone.localdate()}
    if customer and "customer" in form_fields(ShipmentForm):
        initial["customer"] = customer

    form = ShipmentForm(initial=initial, can_edit_internal_costing=can_edit_internal_costing)
    return render(
        request,
        "crm/shipment_form.html",
        {
            "form": form,
            "opportunity": opportunity,
            "is_edit": False,
            "order": None,
            "order_field": ORDER_FIELD,
            "can_view_shipping_costs": can_edit_internal_costing,
        },
    )


def shipping_add_for_order(request, pk):
    """
    Create a shipment from a production order.
    This sets the correct FK field name every time.
    """
    order = get_object_or_404(ProductionOrder, pk=pk)
    can_edit_internal_costing = can_view_lifecycle_profit(request.user)

    if ORDER_FIELD is None:
        messages.error(request, "Shipment model has no order link field.")
        return redirect("production_detail", pk=order.pk)

    if request.method == "POST":
        form = ShipmentForm(request.POST, can_edit_internal_costing=can_edit_internal_costing)
        if form.is_valid():
            shipment = form.save(commit=False)

            # set correct FK name: production_order OR order
            setattr(shipment, ORDER_FIELD, order)

            # set optional links
            if hasattr(shipment, "customer"):
                shipment.customer = getattr(order, "customer", None)
            if hasattr(shipment, "opportunity"):
                shipment.opportunity = getattr(order, "opportunity", None)

            if not shipment.ship_date:
                shipment.ship_date = timezone.localdate()

            blocked_response = _block_inactive_quick_revision_shipment(request, shipment, order=order)
            if blocked_response:
                return blocked_response

            # safe numbers
            if hasattr(shipment, "cost_bdt") and shipment.cost_bdt is None:
                shipment.cost_bdt = Decimal("0")
            if hasattr(shipment, "cost_cad") and shipment.cost_cad is None:
                shipment.cost_cad = Decimal("0")

            shipment.save()
            _sync_production_after_delivered_shipment(shipment)
            _handle_shipment_status_change(
                request,
                shipment,
                None,
                notify_customer=_shipment_notify_requested(request),
            )
            create_lifecycle_from_shipping(shipment, user=request.user)
            messages.success(request, "Shipment created for this order.")
            return redirect("production_detail", pk=order.pk)

        messages.error(request, "Could not save. Please fix the form errors.")
        return render(
            request,
            "crm/shipment_form.html",
            {
                "form": form,
                "order": order,
                "is_edit": False,
                "order_field": ORDER_FIELD,
                "can_view_shipping_costs": can_edit_internal_costing,
            },
        )

    # initial values
    initial = {"ship_date": timezone.localdate()}
    if "customer" in form_fields(ShipmentForm):
        initial["customer"] = getattr(order, "customer", None)
    if "opportunity" in form_fields(ShipmentForm):
        initial["opportunity"] = getattr(order, "opportunity", None)

    form = ShipmentForm(initial=initial, can_edit_internal_costing=can_edit_internal_costing)
    return render(
        request,
        "crm/shipment_form.html",
        {
            "form": form,
            "order": order,
            "is_edit": False,
            "order_field": ORDER_FIELD,
            "can_view_shipping_costs": can_edit_internal_costing,
        },
    )


def shipment_refresh_tracking(request, pk):
    shipment = get_object_or_404(Shipment, pk=pk)

    if not getattr(shipment, "tracking_number", None):
        messages.error(request, "No tracking number set for this shipment.")
        return redirect("shipment_detail", pk=pk)

    if hasattr(shipment, "last_tracking_check"):
        shipment.last_tracking_check = timezone.now()

    if hasattr(shipment, "last_tracking_status") and not shipment.last_tracking_status:
        shipment.last_tracking_status = "Tracking checked and saved."

    shipment.save()
    messages.success(request, "Tracking updated for this shipment.")
    return redirect("shipment_detail", pk=pk)


@require_POST
def shipment_notify_customer(request, pk):
    qs = Shipment.objects.all()
    sr = _select_related_fields()
    if sr:
        qs = qs.select_related(*sr)

    shipment = get_object_or_404(qs, pk=pk)

    queued, reason = _queue_shipment_status_notification(shipment, shipment.status, force=True)
    if queued:
        messages.success(request, "Customer shipment notification queued.")
    elif reason == "missing_recipient":
        messages.error(request, "No email address found for this shipment.")
    elif reason == "invalid_recipient":
        messages.error(request, "Customer email address is invalid.")
    else:
        messages.error(request, "Could not queue email right now. Shipment was not changed.")

    return redirect("shipment_detail", pk=pk)

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum, Q
from django.shortcuts import render
from django.utils import timezone

from .models import (
    Lead,
    Opportunity,
    AccountingEntry,
    BDStaffMonth,
    LEAD_STATUS_CHOICES,
    SOURCE_CHOICES,
    PRIORITY_CHOICES,
)

# Optional models. If they do not exist, dashboard will still work.
try:
    from .models import ProductionOrder
except Exception:
    ProductionOrder = None

try:
    from .models import Shipment
except Exception:
    Shipment = None

try:
    from .models import Invoice
except Exception:
    Invoice = None

try:
    from .models import CustomerEvent
except Exception:
    CustomerEvent = None


def _to_float(x):
    if x is None:
        return 0.0
    if isinstance(x, Decimal):
        return float(x)
    try:
        return float(x)
    except Exception:
        return 0.0


def _top_buckets(qs, key_name: str, limit: int = 6):
    rows = list(qs)
    labels = []
    values = []
    other_total = 0
    for i, row in enumerate(rows):
        label = (row.get(key_name) or "Unknown").strip() or "Unknown"
        count = int(row.get("c") or 0)
        if i < limit:
            labels.append(label)
            values.append(count)
        else:
            other_total += count
    if other_total:
        labels.append("Other")
        values.append(other_total)
    return labels, values


def _shift_month_start(d, offset):
    month_index = (d.year * 12) + (d.month - 1) + offset
    return date(month_index // 12, month_index % 12 + 1, 1)


def _format_count(value):
    try:
        return f"{int(value):,}"
    except Exception:
        return "0"


def _format_money(value, currency="CAD"):
    return format_finance_money(value, currency)


def _format_currency_summary(rows):
    return " / ".join(
        format_compact_finance_money(row.get("amount"), row.get("currency"))
        for row in (rows or [])
    ) or "-"


def _format_percent(value, places=1):
    return f"{_to_float(value):,.{places}f}%"


def _delta_pct(current, previous):
    current_f = _to_float(current)
    previous_f = _to_float(previous)
    if previous_f == 0:
        if current_f == 0:
            return 0.0
        return 100.0
    return ((current_f - previous_f) / abs(previous_f)) * 100.0


def _delta_tone(delta, inverse=False):
    if abs(delta) < 0.05:
        return "flat"
    good = delta > 0
    if inverse:
        good = not good
    return "up" if good else "down"


def _ceo_optional_model(app_label, model_name):
    try:
        return apps.get_model(app_label, model_name)
    except Exception:
        return None


def _ceo_decimal(value):
    try:
        return Decimal(str(value)) if value is not None else Decimal("0")
    except Exception:
        return Decimal("0")


def _ceo_amount_cad(entry, cad_to_bdt=None):
    try:
        return convert_currency(
            getattr(entry, "amount_original", None),
            getattr(entry, "currency", ""),
            "CAD",
            bdt_per_cad=cad_to_bdt,
            stored_rate_to_cad=getattr(entry, "rate_to_cad", None),
            stored_rate_to_bdt=entry.__dict__.get("rate_to_bdt"),
        )
    except CurrencyConversionError:
        return Decimal("0")


def _ceo_currency_amount_cad(amount, currency, cad_to_bdt=None):
    try:
        return convert_currency(amount, currency, "CAD", bdt_per_cad=cad_to_bdt)
    except CurrencyConversionError:
        return Decimal("0")


def _ceo_invoice_balance_cad(invoice, cad_to_bdt=None):
    return _ceo_currency_amount_cad(
        getattr(invoice, "balance", Decimal("0")),
        getattr(invoice, "currency", ""),
        cad_to_bdt,
    )


def _ceo_month_keys(today, months=6):
    current_month = today.replace(day=1)
    return [_shift_month_start(current_month, offset) for offset in range(-(months - 1), 1)]


def _ceo_bar_rows(rows, keys):
    max_value = max(
        [max([abs(_to_float(row.get(key))) for key in keys] or [0]) for row in rows] or [0]
    )
    for row in rows:
        for key in keys:
            row[f"{key}_bar"] = int((abs(_to_float(row.get(key))) / max_value) * 100) if max_value else 0
    return rows


def _ceo_aggregate(model, qs, **fields):
    if model is None:
        return {name: 0 for name in fields}
    try:
        return qs.aggregate(**fields)
    except (OperationalError, ProgrammingError):
        return {name: 0 for name in fields}


def _ceo_month_key(value):
    if not value:
        return None
    if hasattr(value, "date"):
        value = value.date()
    return value.replace(day=1)


def _dashboard_period_from_request(request, today):
    try:
        period_days = int((request.GET.get("days") or "30").strip())
    except Exception:
        period_days = 30
    if period_days not in (7, 30, 60, 90, 180):
        period_days = 30

    date_from_raw = (request.GET.get("date_from") or "").strip()
    date_to_raw = (request.GET.get("date_to") or "").strip()
    custom_from = parse_date(date_from_raw) if date_from_raw else None
    custom_to = parse_date(date_to_raw) if date_to_raw else None

    if custom_from or custom_to:
        period_end = custom_to or today
        start_period = custom_from or (period_end - timedelta(days=period_days - 1))
        if start_period > period_end:
            start_period, period_end = period_end, start_period
        period_days = max((period_end - start_period).days + 1, 1)
        period_label = (
            start_period.strftime("%b %d, %Y")
            if start_period == period_end
            else f"{start_period.strftime('%b %d, %Y')} - {period_end.strftime('%b %d, %Y')}"
        )
        date_from_value = start_period.isoformat()
        date_to_value = period_end.isoformat()
    else:
        period_end = today
        start_period = period_end - timedelta(days=period_days - 1)
        period_label = f"Last {period_days} days"
        date_from_value = ""
        date_to_value = ""

    previous_end = start_period - timedelta(days=1)
    previous_start = previous_end - timedelta(days=period_days - 1)
    filter_values = {
        "days": str(period_days if period_days in (7, 30, 60, 90, 180) else 30),
        "date_from": date_from_value,
        "date_to": date_to_value,
    }
    return period_days, start_period, period_end, previous_start, previous_end, period_label, filter_values


def _ceo_percent(numerator, denominator):
    numerator = _ceo_decimal(numerator)
    denominator = _ceo_decimal(denominator)
    if denominator <= 0:
        return Decimal("0")
    return (numerator / denominator * Decimal("100")).quantize(Decimal("0.01"))


QUICK_COSTING_KPI_VALUE_STATUSES = list(QuickCosting.ACTIVE_APPROVED_STATUSES)


def _active_lead_queryset():
    return (
        Lead.objects.filter(is_archived=False)
        .annotate(
            visibility_has_opportunity=Exists(
                Opportunity.objects.filter(lead_id=OuterRef("pk"))
            )
        )
        .exclude(
            Q(lead_status__in=["Converted", "Lost", "Unqualified"])
            | Q(outbound_status__in=_LEAD_LOST_OUTBOUND_STATUSES | _LEAD_CONVERTED_OUTBOUND_STATUSES)
            | Q(visibility_has_opportunity=True)
        )
    )


def _active_opportunity_queryset():
    return _active_opportunity_list_queryset(Opportunity.objects.all())


def _active_production_queryset():
    return ProductionOrder.objects.filter(is_archived=False)


def _opportunity_has_production_subquery():
    return ProductionOrder.objects.filter(opportunity_id=OuterRef("pk"), is_archived=False)


def _with_opportunity_production_flag(qs, annotation_name="list_has_production"):
    return qs.annotate(**{annotation_name: Exists(_opportunity_has_production_subquery())})


def _active_opportunity_list_queryset(qs, production_flag="list_has_production"):
    if production_flag not in getattr(qs, "query", SimpleNamespace(annotations={})).annotations:
        qs = _with_opportunity_production_flag(qs, production_flag)
    return (
        open_pipeline_queryset(qs)
        .filter(**{production_flag: False})
        .exclude(stage="Production")
    )


def _production_row_has_delivered_shipment(row):
    return any(getattr(shipment, "status", "") == "delivered" for shipment in row.get("shipments", []))


def _production_row_is_completed(row):
    return row["operational_status"] == OPERATIONAL_STATUS_SHIPPED and _production_row_has_delivered_shipment(row)


def _sync_production_after_delivered_shipment(shipment):
    if getattr(shipment, "status", "") != "delivered":
        return None
    order = getattr(shipment, ORDER_FIELD, None) if ORDER_FIELD else getattr(shipment, "order", None)
    if not order:
        return None
    update_fields = []
    if getattr(order, "status", "") != "done":
        order.status = "done"
        update_fields.append("status")
    if update_fields:
        if hasattr(order, "updated_at"):
            update_fields.append("updated_at")
        order.save(update_fields=update_fields)
    sync_operational_status(order, explicit_status=OPERATIONAL_STATUS_SHIPPED)
    return order


def _quick_costing_revenue_expression():
    return models.ExpressionWrapper(
        F("selling_price_per_piece") * F("quantity"),
        output_field=models.DecimalField(max_digits=16, decimal_places=2),
    )


def _with_opportunity_kpi_value(qs, annotation_name="kpi_order_value"):
    return with_pipeline_value(qs, annotation_name=annotation_name).annotate(
        kpi_currency=F("pipeline_currency")
    )


def _sum_opportunity_kpi_values_by_currency(qs):
    try:
        return summarize_pipeline(qs, apply_open_definition=False)["rows"]
    except (OperationalError, ProgrammingError):
        totals = defaultdict(lambda: {"amount": Decimal("0")})
        for opportunity in qs.only("order_value", "order_value_usd", "order_currency"):
            if opportunity.order_value_usd is not None:
                currency = "USD"
                amount = opportunity.order_value_usd
            else:
                currency = (opportunity.order_currency or "CAD").upper()
                amount = opportunity.order_value
            totals[currency]["amount"] += _ceo_decimal(amount)
        return currency_summary_rows(totals)


def _ceo_inventory_label(key):
    labels = dict(INVENTORY_GROUP_LABELS)
    return labels.get(key or "other", "Other")


def _ceo_safe_inventory_snapshot(can_view_financials):
    snapshot = {
        "low_stock_count": 0,
        "low_fabric": 0,
        "low_trims": 0,
        "low_packaging": 0,
        "negative_stock": 0,
        "active_materials": 0,
        "incoming_stock": Decimal("0"),
        "reserved_stock": Decimal("0"),
        "allocated_qty": Decimal("0"),
        "consumed_qty": Decimal("0"),
        "pending_allocation": Decimal("0"),
        "dead_stock_count": 0,
        "waste_material_count": 0,
        "total_value": None,
        "dead_stock_value": None,
        "waste_estimate": None,
        "highest_usage_materials": [],
        "category_usage_labels": [],
        "category_usage_values": [],
    }
    try:
        low_stock_filter = Q(quantity__lte=F("reorder_level")) | Q(
            reorder_level=0,
            quantity__lte=F("min_level"),
        )
        inventory_qs = InventoryItem.objects.filter(is_active=True)
        stock_totals = inventory_qs.aggregate(
            active_materials=Count("id"),
            incoming_stock=Sum("incoming_quantity"),
            reserved_stock=Sum("reserved_quantity"),
        )
        snapshot.update(
            {
                "low_stock_count": inventory_qs.filter(low_stock_filter).count(),
                "low_fabric": inventory_qs.filter(low_stock_filter, material_group="fabric").count(),
                "low_trims": inventory_qs.filter(low_stock_filter, material_group="trim").count(),
                "low_packaging": inventory_qs.filter(low_stock_filter, material_group="packaging").count(),
                "negative_stock": inventory_qs.filter(quantity__lt=0).count(),
                "waste_material_count": inventory_qs.filter(Q(waste_quantity__gt=0) | Q(damaged_quantity__gt=0)).count(),
                "active_materials": stock_totals.get("active_materials") or 0,
                "incoming_stock": _ceo_decimal(stock_totals.get("incoming_stock")),
                "reserved_stock": _ceo_decimal(stock_totals.get("reserved_stock")),
            }
        )

        material_totals = ProductionOrderMaterial.objects.aggregate(
            allocated=Sum("allocated_quantity"),
            consumed=Sum("consumed_quantity"),
        )
        snapshot["allocated_qty"] = _ceo_decimal(material_totals.get("allocated"))
        snapshot["consumed_qty"] = _ceo_decimal(material_totals.get("consumed"))
        snapshot["pending_allocation"] = max(snapshot["allocated_qty"] - snapshot["consumed_qty"], Decimal("0"))

        usage_rows = list(
            ProductionOrderMaterial.objects.select_related("inventory_item")
            .values("inventory_item__name", "inventory_item__material_group")
            .annotate(
                consumed=Sum("consumed_quantity"),
                allocated=Sum("allocated_quantity"),
            )
            .order_by("-consumed", "-allocated")[:6]
        )
        highest_usage = []
        for row in usage_rows:
            used = _ceo_decimal(row.get("consumed")) or _ceo_decimal(row.get("allocated"))
            highest_usage.append(
                {
                    "label": row.get("inventory_item__name") or "Unassigned material",
                    "group": _ceo_inventory_label(row.get("inventory_item__material_group")),
                    "used": used,
                }
            )
        snapshot["highest_usage_materials"] = highest_usage

        category_rows = list(
            ProductionOrderMaterial.objects.values("inventory_item__material_group")
            .annotate(
                consumed=Sum("consumed_quantity"),
                allocated=Sum("allocated_quantity"),
            )
            .order_by("-consumed", "-allocated")[:7]
        )
        for row in category_rows:
            value = _ceo_decimal(row.get("consumed")) or _ceo_decimal(row.get("allocated"))
            snapshot["category_usage_labels"].append(_ceo_inventory_label(row.get("inventory_item__material_group")))
            snapshot["category_usage_values"].append(_to_float(value))

        snapshot["dead_stock_count"] = (
            inventory_qs.filter(quantity__gt=0, production_materials__isnull=True).distinct().count()
        )

        if can_view_financials:
            stock_value_expr = models.ExpressionWrapper(
                F("unit_cost") * F("quantity"),
                output_field=models.DecimalField(max_digits=16, decimal_places=2),
            )
            waste_value_expr = models.ExpressionWrapper(
                F("unit_cost") * (F("waste_quantity") + F("damaged_quantity")),
                output_field=models.DecimalField(max_digits=16, decimal_places=2),
            )
            value_totals = inventory_qs.filter(unit_cost__isnull=False).aggregate(
                total_value=Sum(stock_value_expr),
                waste_estimate=Sum(waste_value_expr),
            )
            dead_stock_totals = inventory_qs.filter(
                unit_cost__isnull=False,
                quantity__gt=0,
                production_materials__isnull=True,
            ).distinct().aggregate(dead_stock_value=Sum(stock_value_expr))
            snapshot["total_value"] = _ceo_decimal(value_totals.get("total_value"))
            snapshot["waste_estimate"] = _ceo_decimal(value_totals.get("waste_estimate"))
            snapshot["dead_stock_value"] = _ceo_decimal(dead_stock_totals.get("dead_stock_value"))
    except (OperationalError, ProgrammingError) as exc:
        logger.warning("ceo_dashboard: inventory intelligence metrics unavailable: %s", exc)
    return snapshot


def _quick_revision_metrics():
    try:
        return {
            "total_active_revisions": QuickCosting.objects.exclude(
                status__in=QuickCosting.INACTIVE_REPORTING_STATUSES
            ).count(),
            "superseded_revisions": QuickCosting.objects.filter(
                status=QuickCosting.STATUS_SUPERSEDED
            ).count(),
            "recalled_revisions": QuickCosting.objects.filter(
                status=QuickCosting.STATUS_RECALLED
            ).count(),
        }
    except (OperationalError, ProgrammingError):
        logger.exception("quick costing revision metrics unavailable")
        return {
            "total_active_revisions": 0,
            "superseded_revisions": 0,
            "recalled_revisions": 0,
        }


@login_required
def ceo_dashboard(request):
    from time import perf_counter

    from crm.services.ceo_executive import build_ceo_executive_context

    started = perf_counter()
    context = build_ceo_executive_context()
    production_business = summarize_production_business_models()
    context.update(production_business)
    context["local_sewing_summary"] = production_business["local_sewing"]
    context["quick_revision_metrics"] = _quick_revision_metrics()
    context["executive_money_cards"] = [
        ("Today's Sales Value", context["today_sales"]),
        ("Monthly Sales Value", context["monthly_sales"]),
        ("Outstanding AR", context["outstanding_ar"]),
        ("Outstanding AP", context["outstanding_ap"]),
        ("Current Cash", context["current_cash"]),
    ]
    response = render(request, "crm/ceo_executive_dashboard.html", context)
    elapsed_ms = (perf_counter() - started) * 1000
    response["Server-Timing"] = f"ceo-dashboard;dur={elapsed_ms:.1f}"
    return response


@login_required
def ceo_operations_dashboard(request):
    today = timezone.localdate()
    period_days, start_period, period_end, previous_start, previous_end, period_label, filter_values = (
        _dashboard_period_from_request(request, today)
    )

    side = (request.GET.get("side") or "").strip().upper()
    if side not in {"", "CA", "BD"}:
        side = ""

    filter_values["side"] = side
    can_view_executive_financials = can_view_lifecycle_profit(request.user)
    can_view_local_financials = can_view_local_sewing_financials(request.user)
    lead_kpi_qs = _active_lead_queryset()
    opportunity_kpi_qs = _active_opportunity_queryset()
    opportunity_reporting_qs = with_opportunity_reporting_date(opportunity_kpi_qs)
    production_kpi_qs = _active_production_queryset()
    opp_period_filter = {f"{OPPORTUNITY_REPORTING_DATE_ALIAS}__range": (start_period, period_end)}
    prev_opp_period_filter = {f"{OPPORTUNITY_REPORTING_DATE_ALIAS}__range": (previous_start, previous_end)}

    leads_period = lead_kpi_qs.filter(created_date__range=(start_period, period_end)).count()
    prev_leads_period = lead_kpi_qs.filter(created_date__range=(previous_start, previous_end)).count()
    overdue_followups = lead_kpi_qs.filter(next_followup__lt=today).exclude(
        lead_status__in=["Converted", "Closed", "Disqualified", "Lost"]
    ).count()
    due_soon_followups = lead_kpi_qs.filter(
        next_followup__range=(today, today + timedelta(days=7))
    ).exclude(lead_status__in=["Converted", "Closed", "Disqualified", "Lost"]).count()
    lead_source_rows = list(
        lead_kpi_qs.filter(created_date__range=(start_period, period_end))
        .values("source")
        .annotate(count=Count("id"))
        .order_by("-count")[:6]
    )
    assignee_rows = lead_kpi_qs.filter(created_date__range=(start_period, period_end)).values(
        "assigned_to_id", "owner"
    ).annotate(count=Count("id"))
    assignee_counts = defaultdict(int)
    identity_index = get_employee_identity_index()
    for row in assignee_rows:
        identity = resolve_employee_identity(
            user_id=row["assigned_to_id"],
            owner_text=row["owner"],
            index=identity_index,
        )
        assignee_counts[identity["canonical_name"]] += int(row["count"] or 0)
    top_assignees = [
        {"label": name, "count": count}
        for name, count in sorted(assignee_counts.items(), key=lambda item: (-item[1], item[0]))[:8]
    ]

    opp_base = opportunity_reporting_qs.filter(**opp_period_filter)
    opp_period = opp_base.count()
    prev_opp_period = opportunity_reporting_qs.filter(**prev_opp_period_filter).count()
    open_pipeline_qs = open_pipeline_queryset(opportunity_kpi_qs)
    open_opps = open_pipeline_qs.count()
    open_pipeline_values = _sum_opportunity_kpi_values_by_currency(open_pipeline_qs)
    won_period = opportunity_reporting_qs.filter(stage="Closed Won", **opp_period_filter).count()
    lost_period = opportunity_reporting_qs.filter(stage="Closed Lost", **opp_period_filter).count()
    conversion_rate = round((opp_period / leads_period) * 100, 1) if leads_period else 0
    prev_conversion_rate = round((prev_opp_period / prev_leads_period) * 100, 1) if prev_leads_period else 0
    stage_rows = list(
        opportunity_kpi_qs.values("stage").annotate(count=Count("id")).order_by("-count")[:8]
    )
    top_customer_rows = list(
        _with_opportunity_kpi_value(opportunity_kpi_qs.select_related("customer", "lead"))
        .filter(pk__in=opp_base.values("pk"))
        .values("customer__account_brand", "customer__contact_name", "lead__account_brand", "kpi_currency")
        .annotate(total=Sum("kpi_order_value"), count=Count("id"))
        .order_by(*(["-total", "-count"] if can_view_executive_financials else ["-count"]))[:8]
    )
    top_customers = []
    for row in top_customer_rows:
        top_customers.append(
            {
                "label": row.get("customer__account_brand")
                or row.get("customer__contact_name")
                or row.get("lead__account_brand")
                or "Unassigned customer",
                "value": _ceo_decimal(row.get("total")),
                "currency": row.get("kpi_currency") or "CAD",
                "count": int(row.get("count") or 0),
            }
        )

    production_qs = production_kpi_qs
    if side == "CA":
        production_qs = production_qs.filter(factory_location="ca")
    elif side == "BD":
        production_qs = production_qs.filter(factory_location="bd")
    production_orders_for_ceo = list(production_qs.prefetch_related("stages", "shipments"))
    production_operational_rows_ceo = [
        {
            "order": order,
            "operational_status": get_production_operational_status(order),
        }
        for order in production_orders_for_ceo
    ]
    production_operational_counts_ceo = Counter(
        row["operational_status"] for row in production_operational_rows_ceo
    )
    production_active_rows_ceo = [
        row for row in production_operational_rows_ceo
        if row["operational_status"] in OPERATIONAL_ACTIVE_STATUSES
    ]
    production_running_statuses = {
        "sample_development",
        "fabric_sourcing",
        "cutting",
        "printing",
        "sewing",
        "qc",
        "packing",
    }
    production_running = len([
        row for row in production_operational_rows_ceo
        if row["operational_status"] in production_running_statuses
    ])
    production_hold = 0
    production_done_period = len([
        row for row in production_operational_rows_ceo
        if row["operational_status"] == OPERATIONAL_STATUS_SHIPPED
        and row["order"].updated_at.date() >= start_period
        and row["order"].updated_at.date() <= period_end
    ])
    production_delayed = len([
        row for row in production_operational_rows_ceo
        if row["order"].bulk_deadline
        and row["order"].bulk_deadline < today
        and row["operational_status"] not in OPERATIONAL_FINISHED_STATUSES
    ])
    production_quantity = sum((row["order"].qty_total or 0) for row in production_active_rows_ceo)
    production_rows = [
        {"status": label, "count": production_operational_counts_ceo[status]}
        for status, label in OPERATIONAL_STATUS_LABELS.items()
        if production_operational_counts_ceo.get(status)
    ]

    shipping_qs = Shipment.objects.all()
    if side == "CA":
        shipping_qs = shipping_qs.filter(order__factory_location="ca")
    elif side == "BD":
        shipping_qs = shipping_qs.filter(order__factory_location="bd")
    shipments_period = shipping_qs.filter(created_at__date__range=(start_period, period_end)).count()
    shipments_in_transit = shipping_qs.filter(status__in=["booked", "shipped", "out_for_delivery"]).count()
    shipments_delayed = shipping_qs.filter(
        ship_date__lt=today,
    ).exclude(status__in=["delivered", "cancelled"]).count()
    shipment_status_rows = list(
        shipping_qs.values("status").annotate(count=Count("id")).order_by("-count")
    )

    cad_to_bdt = _get_latest_cad_to_bdt_rate()
    accounting_qs = AccountingEntry.objects.exclude(main_type="TRANSFER").exclude(status__iexact="CANCELLED")
    if side:
        accounting_qs = accounting_qs.filter(side=side)
    period_entries = list(
        accounting_qs.filter(date__range=(start_period, period_end)).only(
            "date", "direction", "side", "currency", "main_type", "amount_original", "amount_cad", "rate_to_cad"
        )
    )
    prev_entries = list(
        accounting_qs.filter(date__range=(previous_start, previous_end)).only(
            "date", "direction", "side", "currency", "main_type", "amount_original", "amount_cad", "rate_to_cad"
        )
    )

    def _finance_totals(entries):
        revenue = Decimal("0")
        expenses = Decimal("0")
        for entry in entries:
            direction = (entry.direction or "").upper().strip()
            amount = _ceo_amount_cad(entry, cad_to_bdt)
            if direction == "IN":
                revenue += amount
            elif direction == "OUT":
                expenses += amount
        return {"revenue": revenue, "expenses": expenses, "net": revenue - expenses}

    finance_totals = _finance_totals(period_entries)
    prev_finance_totals = _finance_totals(prev_entries)

    invoice_qs = Invoice.objects.filter(is_archived=False).exclude(status="cancelled") if Invoice is not None else None
    if invoice_qs is not None and side:
        invoice_qs = invoice_qs.filter(Q(invoice_region=side) | Q(invoice_region="", currency="BDT" if side == "BD" else "CAD"))
    if invoice_qs is not None:
        invoice_qs = with_invoice_reporting_date(invoice_qs)
    invoice_open_total = Decimal("0")
    invoice_open_values = []
    overdue_invoice_count = 0
    if invoice_qs is not None:
        try:
            open_invoices = invoice_qs.exclude(status="paid")
            open_totals = defaultdict(lambda: {"amount": Decimal("0")})
            for invoice in open_invoices.only("total_amount", "paid_amount", "currency"):
                code = (invoice.currency or "CAD").upper().strip()
                open_totals[code]["amount"] += _ceo_decimal(invoice.balance)
            invoice_open_values = currency_summary_rows(open_totals)
            if len(invoice_open_values) == 1:
                invoice_open_total = invoice_open_values[0]["amount"]
            overdue_invoice_count = open_invoices.filter(due_date__lt=today).count()
        except (OperationalError, ProgrammingError):
            invoice_open_total = Decimal("0")
            invoice_open_values = []
            overdue_invoice_count = 0

    ceo_month_keys = _ceo_month_keys(period_end)
    revenue_overview = None
    invoice_month_labels = []
    invoice_month_values = []
    invoice_month_series = []
    if can_view_executive_financials and invoice_qs is not None:
        try:
            invoice_period = invoice_qs.filter(**{f"{INVOICE_REPORTING_DATE_ALIAS}__range": (start_period, period_end)})
            period_totals = defaultdict(
                lambda: {"invoiced": Decimal("0"), "paid": Decimal("0"), "outstanding": Decimal("0")}
            )
            for invoice in invoice_period.only("total_amount", "paid_amount", "currency"):
                code = (invoice.currency or "CAD").upper().strip()
                period_totals[code]["invoiced"] += _ceo_decimal(invoice.total_amount)
                period_totals[code]["paid"] += _ceo_decimal(invoice.paid_amount)
                period_totals[code]["outstanding"] += _ceo_decimal(invoice.balance)
            invoice_currency_rows = currency_summary_rows(
                period_totals, ("invoiced", "paid", "outstanding")
            )
            invoice_month_map = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
            for invoice in invoice_qs.filter(**{f"{INVOICE_REPORTING_DATE_ALIAS}__gte": ceo_month_keys[0]}).only(
                "invoice_date", "created_at", "issue_date", "total_amount", "currency"
            ):
                month_key = _ceo_month_key(getattr(invoice, INVOICE_REPORTING_DATE_ALIAS, None))
                if month_key:
                    code = (invoice.currency or "CAD").upper().strip()
                    invoice_month_map[code][month_key] += _ceo_decimal(invoice.total_amount)
            for month_key in ceo_month_keys:
                invoice_month_labels.append(month_key.strftime("%b"))
            for row in invoice_currency_rows:
                invoice_month_series.append(
                    {
                        "currency": row["currency"],
                        "values": [
                            _to_float(invoice_month_map[row["currency"]][month_key])
                            for month_key in ceo_month_keys
                        ],
                    }
                )
            if len(invoice_month_series) == 1:
                invoice_month_values = invoice_month_series[0]["values"]
            revenue_overview = {
                "currency_rows": invoice_currency_rows,
                "overdue_invoice_count": overdue_invoice_count,
            }
        except (OperationalError, ProgrammingError):
            logger.exception("ceo_dashboard: invoice revenue overview unavailable")
            revenue_overview = None

    WebsiteTrafficDaily = _ceo_optional_model("marketing", "WebsiteTrafficDaily")
    WebsitePageDaily = _ceo_optional_model("marketing", "WebsitePageDaily")
    SeoQueryDaily = _ceo_optional_model("marketing", "SeoQueryDaily")
    AccountMetricDaily = _ceo_optional_model("marketing", "AccountMetricDaily")
    Campaign = _ceo_optional_model("marketing", "Campaign")
    InsightItem = _ceo_optional_model("marketing", "InsightItem")

    website_totals = _ceo_aggregate(
        WebsiteTrafficDaily,
        WebsiteTrafficDaily.objects.filter(date__range=(start_period, period_end)) if WebsiteTrafficDaily else None,
        visitors=Sum("visitors"),
        sessions=Sum("sessions"),
        page_views=Sum("page_views"),
        conversions=Sum("conversions"),
    )
    search_totals = _ceo_aggregate(
        SeoQueryDaily,
        SeoQueryDaily.objects.filter(date__range=(start_period, period_end)) if SeoQueryDaily else None,
        clicks=Sum("clicks"),
        impressions=Sum("impressions"),
    )
    social_totals = _ceo_aggregate(
        AccountMetricDaily,
        AccountMetricDaily.objects.filter(date__range=(start_period, period_end)) if AccountMetricDaily else None,
        engagement=Sum("engagement_total"),
        reach=Sum("reach"),
        views=Sum("views"),
    )
    active_campaigns = 0
    open_insights = 0
    top_pages = []
    try:
        if Campaign is not None:
            active_campaigns = Campaign.objects.filter(is_active=True).count()
        if InsightItem is not None:
            open_insights = InsightItem.objects.filter(status="open").count()
        if WebsitePageDaily is not None:
            top_pages = list(
                WebsitePageDaily.objects.filter(date__range=(start_period, period_end))
                .values("page_path")
                .annotate(page_views=Sum("page_views"), visitors=Sum("visitors"))
                .order_by("-page_views")[:6]
            )
    except (OperationalError, ProgrammingError):
        active_campaigns = 0
        open_insights = 0
        top_pages = []

    month_rows = []
    month_keys = ceo_month_keys
    lead_month_map = {
        row["month"]: int(row["count"] or 0)
        for row in lead_kpi_qs.filter(created_date__gte=month_keys[0])
        .annotate(month=TruncMonth("created_date"))
        .values("month")
        .annotate(count=Count("id"))
    }
    opp_month_map = {
        row["month"]: int(row["count"] or 0)
        for row in opportunity_reporting_qs.filter(**{f"{OPPORTUNITY_REPORTING_DATE_ALIAS}__gte": month_keys[0]})
        .annotate(month=TruncMonth(OPPORTUNITY_REPORTING_DATE_ALIAS))
        .values("month")
        .annotate(count=Count("id"))
    }
    revenue_month_map = defaultdict(lambda: Decimal("0"))
    expense_month_map = defaultdict(lambda: Decimal("0"))
    for entry in accounting_qs.filter(date__gte=month_keys[0]).only(
        "date", "direction", "currency", "amount_original", "amount_cad", "rate_to_cad"
    ).iterator():
        if not entry.date:
            continue
        month_key = entry.date.replace(day=1)
        if month_key not in month_keys:
            continue
        amount = _ceo_amount_cad(entry, cad_to_bdt)
        if (entry.direction or "").upper().strip() == "IN":
            revenue_month_map[month_key] += amount
        elif (entry.direction or "").upper().strip() == "OUT":
            expense_month_map[month_key] += amount
    for month_key in month_keys:
        revenue = revenue_month_map[month_key]
        expenses = expense_month_map[month_key]
        month_rows.append(
            {
                "label": month_key.strftime("%b"),
                "leads": lead_month_map.get(month_key, 0),
                "opportunities": opp_month_map.get(month_key, 0),
                "revenue": revenue,
                "expenses": expenses,
                "net": revenue - expenses,
            }
        )
    month_rows = _ceo_bar_rows(month_rows, ["leads", "opportunities", "revenue", "expenses", "net"])

    production_status_labels = [row.get("status") or "Unknown" for row in production_rows]
    production_status_values = [int(row.get("count") or 0) for row in production_rows]
    active_production_count = len(production_active_rows_ceo)
    production_due_soon = len([
        row for row in production_active_rows_ceo
        if row["order"].bulk_deadline
        and today <= row["order"].bulk_deadline <= today + timedelta(days=7)
    ])
    urgent_production = len([
        row for row in production_active_rows_ceo
        if row["order"].bulk_deadline
        and row["order"].bulk_deadline <= today + timedelta(days=2)
    ])
    shipment_pending_count = production_operational_counts_ceo.get(OPERATIONAL_STATUS_READY_TO_SHIP, 0)
    production_completion_rate = _ceo_percent(
        production_done_period,
        production_done_period + active_production_count,
    )
    production_intelligence = {
        "active_production": active_production_count,
        "delayed_orders": production_delayed,
        "urgent_production": urgent_production,
        "shipment_pending": shipment_pending_count,
        "due_soon": production_due_soon,
        "completion_rate": production_completion_rate,
    }

    inventory_intelligence = _ceo_safe_inventory_snapshot(can_view_executive_financials)

    quotation_count = 0
    quote_to_invoice_count = 0
    quotation_approval_rate = Decimal("0")
    repeat_customer_rate = Decimal("0")
    repeat_customer_count = 0
    total_invoice_customers = 0
    quotation_funnel_labels = ["Costings", "Approved", "Quoted", "Invoiced"]
    quotation_funnel_values = [0, 0, 0, 0]
    quick_revision_metrics = {
        "total_active_revisions": 0,
        "superseded_revisions": 0,
        "recalled_revisions": 0,
    }
    try:
        advanced_costings_total = CostingHeader.objects.count()
        advanced_approved_costings = CostingHeader.objects.filter(status="approved").count()
        advanced_quotation_count = CostingHeader.objects.exclude(quotation_number="").count()
        advanced_quote_to_invoice_count = (
            CostingHeader.objects.exclude(quotation_number="")
            .filter(invoices__isnull=False)
            .distinct()
            .count()
        )
        active_quick_costings = QuickCosting.objects.exclude(status__in=QuickCosting.INACTIVE_REPORTING_STATUSES)
        approved_quick_costings = active_quick_costings.filter(
            status__in=QuickCosting.ACTIVE_APPROVED_STATUSES,
        )
        quick_costings_total = active_quick_costings.count()
        quick_approved_costings = approved_quick_costings.count()
        quick_quotation_count = approved_quick_costings.exclude(quotation_number="").filter(
            quotation_revision_required=False
        ).count()
        quick_quote_to_invoice_count = (
            approved_quick_costings.exclude(quotation_number="")
            .filter(quotation_revision_required=False, invoices__isnull=False)
            .distinct()
            .count()
        )
        quick_revision_metrics = {
            "total_active_revisions": quick_costings_total,
            "superseded_revisions": QuickCosting.objects.filter(
                status=QuickCosting.STATUS_SUPERSEDED
            ).count(),
            "recalled_revisions": QuickCosting.objects.filter(
                status=QuickCosting.STATUS_RECALLED
            ).count(),
        }
        costings_total = advanced_costings_total + quick_costings_total
        approved_costings = advanced_approved_costings + quick_approved_costings
        quotation_count = advanced_quotation_count + quick_quotation_count
        quote_to_invoice_count = advanced_quote_to_invoice_count + quick_quote_to_invoice_count
        quotation_approval_rate = _ceo_percent(quote_to_invoice_count, quotation_count)
        quotation_funnel_values = [
            int(costings_total),
            int(approved_costings),
            int(quotation_count),
            int(quote_to_invoice_count),
        ]
        if invoice_qs is not None:
            customer_invoice_qs = (
                invoice_qs.exclude(customer__isnull=True)
                .values("customer_id")
                .annotate(invoice_count=Count("id"))
            )
            total_invoice_customers = customer_invoice_qs.count()
            repeat_customer_count = customer_invoice_qs.filter(invoice_count__gt=1).count()
            repeat_customer_rate = _ceo_percent(repeat_customer_count, total_invoice_customers)
    except (OperationalError, ProgrammingError):
        logger.exception("ceo_dashboard: sales intelligence metrics unavailable")

    sales_intelligence = {
        "top_customers": top_customers,
        "lead_conversion_rate": conversion_rate,
        "quotation_approval_rate": quotation_approval_rate,
        "repeat_customer_rate": repeat_customer_rate,
        "repeat_customer_count": repeat_customer_count,
        "total_invoice_customers": total_invoice_customers,
        "quotation_count": quotation_count,
        "quote_to_invoice_count": quote_to_invoice_count,
        "quick_revision_metrics": quick_revision_metrics,
    }

    lifecycle_active_orders = 0
    lifecycle_waiting_payment = None
    lifecycle_in_production = 0
    lifecycle_shipping = 0
    lifecycle_completed_month = 0
    try:
        lifecycle_active_qs = OrderLifecycle.objects.exclude(status__in=["completed", "cancelled"])
        lifecycle_completed_qs = OrderLifecycle.objects.filter(status="completed")
        if side == "CA":
            side_filter = (
                Q(production_order__factory_location="ca")
                | Q(invoice__invoice_region="CA")
                | Q(invoice__currency="CAD")
            )
            lifecycle_active_qs = lifecycle_active_qs.filter(side_filter).distinct()
            lifecycle_completed_qs = lifecycle_completed_qs.filter(side_filter).distinct()
        elif side == "BD":
            side_filter = (
                Q(production_order__factory_location="bd")
                | Q(invoice__invoice_region="BD")
                | Q(invoice__currency="BDT")
            )
            lifecycle_active_qs = lifecycle_active_qs.filter(side_filter).distinct()
            lifecycle_completed_qs = lifecycle_completed_qs.filter(side_filter).distinct()
        lifecycle_active_orders = lifecycle_active_qs.count()
        lifecycle_in_production = lifecycle_active_qs.filter(status="production").count()
        lifecycle_shipping = lifecycle_active_qs.filter(status="shipping").count()
        lifecycle_completed_month = lifecycle_completed_qs.filter(
            updated_at__date__gte=today.replace(day=1),
        ).count()
        if can_view_executive_financials:
            lifecycle_waiting_payment = lifecycle_active_qs.filter(
                invoice__total_amount__gt=F("invoice__paid_amount")
            ).count()
    except (OperationalError, ProgrammingError):
        logger.exception("ceo_dashboard: lifecycle operations metrics unavailable")

    shipped_this_month = shipping_qs.filter(
        ship_date__gte=today.replace(day=1),
        status__in=["shipped", "out_for_delivery", "delivered"],
    ).count()
    operations_snapshot = {
        "active_lifecycle_orders": lifecycle_active_orders,
        "waiting_for_payment": lifecycle_waiting_payment,
        "in_production": lifecycle_in_production,
        "shipping": lifecycle_shipping,
        "completed_lifecycles_month": lifecycle_completed_month,
        "shipped_this_month": shipped_this_month,
        "production_completion_rate": production_completion_rate,
        "overdue_invoices": overdue_invoice_count if can_view_executive_financials else None,
    }

    profit_overview = None
    low_margin_orders = []
    top_profit_customers = []
    top_profit_customer_groups = []
    profit_month_labels = []
    profit_month_values = []
    profit_month_series = []
    if can_view_executive_financials:
        try:
            lifecycle_qs = OrderLifecycle.objects.select_related(
                "customer", "invoice", "invoice__quick_costing", "costing", "quotation", "production_order"
            ).prefetch_related(
                "production_order__shipments", "production_order__actual_cost_entries"
            ).exclude(
                status="cancelled"
            )
            if side == "CA":
                lifecycle_qs = lifecycle_qs.filter(
                    Q(production_order__factory_location="ca")
                    | Q(invoice__invoice_region="CA")
                    | Q(invoice__currency="CAD")
                ).distinct()
            elif side == "BD":
                lifecycle_qs = lifecycle_qs.filter(
                    Q(production_order__factory_location="bd")
                    | Q(invoice__invoice_region="BD")
                    | Q(invoice__currency="BDT")
                ).distinct()
            profit_totals = defaultdict(
                lambda: {
                    "revenue": Decimal("0"),
                    "costed_revenue": Decimal("0"),
                    "cost": Decimal("0"),
                    "profit": Decimal("0"),
                    "unavailable_cost_count": Decimal("0"),
                }
            )
            profit_month_map = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
            customer_totals = defaultdict(
                lambda: {"label": "Customer", "revenue": Decimal("0"), "profit": Decimal("0"), "order_count": 0}
            )
            low_margin_source = []
            latest_cad_to_bdt = _get_latest_cad_to_bdt_rate()
            for lifecycle in lifecycle_qs:
                currency_code = lifecycle_currency(lifecycle)
                if not currency_code:
                    continue
                revenue = _ceo_decimal(lifecycle.estimated_revenue)
                cost = _ceo_decimal(lifecycle.estimated_cost)
                profit = _ceo_decimal(lifecycle.estimated_profit)
                margin = _ceo_decimal(lifecycle.estimated_margin)
                cost_available = cost > 0
                if lifecycle.invoice_id and getattr(lifecycle.invoice, "quick_costing_id", None):
                    lifecycle._bdt_per_cad_rate = latest_cad_to_bdt
                    quick_breakdown = build_lifecycle_profit_breakdown(lifecycle)
                    revenue = _ceo_decimal(quick_breakdown["invoice_total"])
                    cost_available = bool(
                        quick_breakdown["is_comparable"] and quick_breakdown.get("cost_available")
                    )
                    if cost_available:
                        cost = _ceo_decimal(quick_breakdown["total_cost"])
                        profit = _ceo_decimal(quick_breakdown["net_profit"])
                        margin = _ceo_decimal(quick_breakdown["margin"])
                    else:
                        cost = profit = margin = Decimal("0")
                totals = profit_totals[currency_code]
                totals["revenue"] += revenue
                if cost_available:
                    totals["costed_revenue"] += revenue
                    totals["cost"] += cost
                    totals["profit"] += profit
                else:
                    totals["unavailable_cost_count"] += Decimal("1")
                month_key = _ceo_month_key(lifecycle.updated_at)
                if month_key in ceo_month_keys:
                    profit_month_map[currency_code][month_key] += profit
                customer = lifecycle.customer
                if customer and cost_available:
                    customer_key = (currency_code, customer.pk)
                    customer_row = customer_totals[customer_key]
                    customer_row["label"] = customer.account_brand or customer.contact_name or "Customer"
                    customer_row["revenue"] += revenue
                    customer_row["profit"] += profit
                    customer_row["order_count"] += 1
                if revenue > 0 and cost_available and margin < Decimal("15"):
                    low_margin_source.append((margin, -revenue, lifecycle, currency_code, profit))

            profit_currency_rows = currency_summary_rows(
                profit_totals,
                ("revenue", "costed_revenue", "cost", "profit", "unavailable_cost_count"),
            )
            for row in profit_currency_rows:
                row["margin"] = (
                    _ceo_percent(row["profit"], row["costed_revenue"])
                    if row["costed_revenue"] > 0 and row["cost"] > 0
                    else None
                )
            for _margin, _revenue, lifecycle, currency_code, profit in sorted(
                low_margin_source, key=lambda item: (item[0], item[1])
            )[:5]:
                customer = lifecycle.customer
                low_margin_orders.append(
                    {
                        "label": (
                            getattr(customer, "account_brand", "")
                            or getattr(customer, "contact_name", "")
                            or getattr(getattr(lifecycle, "invoice", None), "invoice_number", "")
                            or getattr(getattr(lifecycle, "production_order", None), "purchase_order_number", "")
                            or f"Lifecycle {lifecycle.pk}"
                        ),
                        "currency": currency_code,
                        "profit": profit,
                        "margin": _margin,
                    }
                )
            for currency_code in ("CAD", "USD", "BDT"):
                rows = [
                    {"currency": code, **values}
                    for (code, _customer_id), values in customer_totals.items()
                    if code == currency_code and values["profit"] > 0
                ]
                rows = sorted(rows, key=lambda row: row["profit"], reverse=True)[:3]
                for row in rows:
                    row["profit_display"] = format_finance_money(row["profit"], currency_code)
                    row["profit_title"] = format_finance_money(row["profit"], currency_code)
                    if currency_code == "BDT":
                        row["profit_display"] = f"BDT {row['profit_display']}"
                        row["profit_title"] = f"BDT {row['profit_title']}"
                        if latest_cad_to_bdt > 0:
                            try:
                                cad_equivalent = convert_currency(
                                    row["profit"],
                                    "BDT",
                                    "CAD",
                                    bdt_per_cad=latest_cad_to_bdt,
                                )
                                row["cad_equivalent"] = cad_equivalent
                                row["cad_equivalent_display"] = format_finance_money(cad_equivalent, "CAD")
                            except CurrencyConversionError:
                                row["cad_equivalent"] = None
                                row["cad_equivalent_display"] = ""
                        else:
                            row["cad_equivalent"] = None
                            row["cad_equivalent_display"] = ""
                    else:
                        row["cad_equivalent"] = None
                        row["cad_equivalent_display"] = ""
                if rows:
                    top_profit_customer_groups.append({"currency": currency_code, "rows": rows})
                    top_profit_customers.extend(rows)
            for month_key in ceo_month_keys:
                profit_month_labels.append(month_key.strftime("%b"))
            for row in profit_currency_rows:
                profit_month_series.append(
                    {
                        "currency": row["currency"],
                        "values": [
                            _to_float(profit_month_map[row["currency"]][month_key])
                            for month_key in ceo_month_keys
                        ],
                    }
                )
            if len(profit_month_series) == 1:
                profit_month_values = profit_month_series[0]["values"]
            profit_overview = {
                "currency_rows": profit_currency_rows,
                "low_margin_orders": low_margin_orders,
                "top_profit_customers": top_profit_customers,
                "top_profit_customer_groups": top_profit_customer_groups,
            }
        except (OperationalError, ProgrammingError):
            logger.exception("ceo_dashboard: profit overview unavailable")
            profit_overview = None

    ai_insight_cards = []
    if production_delayed:
        ai_insight_cards.append(
            {"title": "Production delay risk", "detail": f"{production_delayed} production order(s) are delayed.", "tone": "bad"}
        )
    else:
        ai_insight_cards.append({"title": "Production flow", "detail": "No active production delays are showing.", "tone": "good"})
    if can_view_executive_financials and top_profit_customers:
        row = top_profit_customers[0]
        label = row.get("label") or "Top customer"
        ai_insight_cards.append(
            {"title": "Profit concentration", "detail": f"{label} currently leads estimated profit.", "tone": "blue"}
        )
    if inventory_intelligence["low_fabric"]:
        ai_insight_cards.append(
            {"title": "Fabric stock watch", "detail": f"{inventory_intelligence['low_fabric']} fabric item(s) are below reorder level.", "tone": "warn"}
        )
    if quotation_count and quotation_approval_rate < Decimal("40"):
        ai_insight_cards.append(
            {"title": "Quotation conversion watch", "detail": f"Quote-to-invoice rate is {quotation_approval_rate}%.", "tone": "warn"}
        )
    else:
        ai_insight_cards.append(
            {"title": "Quotation movement", "detail": f"{quote_to_invoice_count} quoted costing(s) have moved to invoice.", "tone": "blue"}
        )

    executive_alert_cards = []
    if can_view_executive_financials and overdue_invoice_count:
        executive_alert_cards.append(
            {"title": "Overdue invoices", "detail": f"{overdue_invoice_count} open invoice(s) are past due.", "tone": "bad"}
        )
    if inventory_intelligence["low_stock_count"]:
        executive_alert_cards.append(
            {"title": "Low inventory", "detail": f"{inventory_intelligence['low_stock_count']} material item(s) need reorder review.", "tone": "warn"}
        )
    if shipments_delayed:
        executive_alert_cards.append(
            {"title": "Delayed shipment", "detail": f"{shipments_delayed} shipment(s) are past ship date.", "tone": "warn"}
        )
    if can_view_executive_financials and inventory_intelligence["waste_material_count"]:
        executive_alert_cards.append(
            {"title": "Waste material", "detail": f"{inventory_intelligence['waste_material_count']} material item(s) have recorded waste or damage.", "tone": "warn"}
        )
    if can_view_executive_financials and low_margin_orders:
        executive_alert_cards.append(
            {"title": "Low margin order", "detail": f"{len(low_margin_orders)} order(s) are below 15% estimated margin.", "tone": "bad"}
        )
    if urgent_production:
        executive_alert_cards.append(
            {"title": "Urgent production", "detail": f"{urgent_production} production order(s) need immediate attention.", "tone": "bad"}
        )
    if not executive_alert_cards:
        executive_alert_cards.append(
            {"title": "Executive alerts clear", "detail": "No high-priority executive alerts are active for this period.", "tone": "good"}
        )
    automation_context = automation_dashboard_context(request.user, sync=False)

    alerts = []
    if overdue_followups:
        alerts.append({"title": f"{overdue_followups} overdue follow-up(s)", "detail": "Sales follow-up dates are past due.", "tone": "bad"})
    if can_view_executive_financials and overdue_invoice_count:
        alerts.append({"title": f"{overdue_invoice_count} overdue invoice(s)", "detail": "Open receivables need collection attention.", "tone": "bad"})
    if production_delayed:
        alerts.append({"title": f"{production_delayed} delayed production order(s)", "detail": "Bulk deadlines have passed on active orders.", "tone": "warn"})
    if shipments_delayed:
        alerts.append({"title": f"{shipments_delayed} delayed shipment(s)", "detail": "Ship dates have passed without delivery.", "tone": "warn"})
    if can_view_executive_financials and finance_totals["net"] < 0:
        alerts.append({"title": "Negative net cash movement", "detail": f"{period_label} outflows exceed inflows.", "tone": "bad"})
    if open_insights:
        alerts.append({"title": f"{open_insights} open marketing insight(s)", "detail": "Marketing recommendations are waiting for review.", "tone": "blue"})
    if not alerts:
        alerts.append({"title": "No critical alerts", "detail": "Core sales, operations, and finance alerts are clear.", "tone": "good"})

    key_trends = [
        {
            "label": "Lead Volume",
            "value": f"{_delta_pct(leads_period, prev_leads_period):+.0f}%",
            "tone": _delta_tone(_delta_pct(leads_period, prev_leads_period)),
            "detail": f"{leads_period:,} leads vs {prev_leads_period:,} previous.",
        },
        {
            "label": "Opportunity Creation",
            "value": f"{_delta_pct(opp_period, prev_opp_period):+.0f}%",
            "tone": _delta_tone(_delta_pct(opp_period, prev_opp_period)),
            "detail": f"{opp_period:,} opportunities vs {prev_opp_period:,} previous.",
        },
        {
            "label": "Accounting Revenue",
            "value": f"{_delta_pct(finance_totals['revenue'], prev_finance_totals['revenue']):+.0f}%",
            "tone": _delta_tone(_delta_pct(finance_totals["revenue"], prev_finance_totals["revenue"])),
            "detail": f"{period_label} revenue compared with prior window.",
        },
        {
            "label": "Conversion",
            "value": f"{conversion_rate - prev_conversion_rate:+.1f} pts",
            "tone": _delta_tone(conversion_rate - prev_conversion_rate),
            "detail": f"{conversion_rate:.1f}% lead-to-opportunity rate.",
        },
    ]
    if not can_view_executive_financials:
        key_trends = [trend for trend in key_trends if trend["label"] != "Accounting Revenue"]

    kpi_cards = [
        {"label": "Accounting Revenue", "value": format_compact_finance_money(finance_totals["revenue"], "CAD"), "note": f"{period_label} CAD equivalent accounting inflow.", "tone": "good"},
        {"label": "Net Cash", "value": format_compact_finance_money(finance_totals["net"], "CAD"), "note": "CAD equivalent revenue less accounting outflow.", "tone": "good" if finance_totals["net"] >= 0 else "bad"},
        {"label": "Open Pipeline", "value": _format_currency_summary(open_pipeline_values), "note": f"{open_opps:,} open opportunities; currencies are not combined.", "tone": "blue"},
        {"label": "Lead Conversion", "value": _format_percent(conversion_rate), "note": f"{opp_period:,} opportunities from {leads_period:,} leads.", "tone": "good" if conversion_rate >= prev_conversion_rate else "warn"},
        {"label": "Production Running", "value": _format_count(production_running), "note": f"{production_quantity:,} active units.", "tone": "blue"},
        {"label": "Shipments In Transit", "value": _format_count(shipments_in_transit), "note": f"{shipments_period:,} shipment(s) created in period.", "tone": "blue"},
        {"label": "Website Visitors", "value": _format_count(website_totals.get("visitors")), "note": f"{_format_count(website_totals.get('page_views'))} page views.", "tone": "blue"},
        {"label": "Receivables", "value": _format_currency_summary(invoice_open_values), "note": f"{overdue_invoice_count:,} overdue invoice(s); currencies are not combined.", "tone": "warn" if overdue_invoice_count else "blue"},
    ]
    production_business = summarize_production_business_models()
    local_sewing_summary = production_business["local_sewing"]
    if can_view_local_financials:
        kpi_cards.extend(
            [
                {"label": "Bangladesh Sewing Revenue", "value": format_compact_finance_money(local_sewing_summary["total_sewing_revenue"], "BDT"), "note": "Sewing-only order value; native BDT.", "tone": "blue"},
                {"label": "Bangladesh Sewing Cost", "value": format_compact_finance_money(local_sewing_summary["total_sewing_cost"], "BDT") if local_sewing_summary["cost_available"] else "Cost unavailable", "note": "Costed local sewing orders only.", "tone": "blue"},
                {"label": "Bangladesh Sewing Profit", "value": format_compact_finance_money(local_sewing_summary["profit"], "BDT") if local_sewing_summary["profit"] is not None else "N/A", "note": "Revenue less available sewing cost.", "tone": "good"},
                {"label": "Bangladesh Sewing Margin", "value": f"{local_sewing_summary['margin']:.2f}%" if local_sewing_summary["margin"] is not None else "Margin N/A", "note": "Never calculated without positive cost.", "tone": "good"},
                {"label": "Bangladesh Local Orders", "value": _format_count(local_sewing_summary["order_count"]), "note": "Bangladesh sewing-charge production orders.", "tone": "blue"},
                {"label": "Sewing Orders In Progress", "value": _format_count(local_sewing_summary["in_progress_count"]), "note": "Open local sewing orders.", "tone": "blue"},
                {"label": "Sewing Orders Completed", "value": _format_count(local_sewing_summary["completed_count"]), "note": "Completed local sewing orders.", "tone": "good"},
                {"label": "Approved Bangladesh Sewing", "value": _format_count(local_sewing_summary["approved_count"]), "note": "CEO-approved CMT Quick Costing.", "tone": "good"},
                {"label": "Pending CEO Approval", "value": _format_count(local_sewing_summary["pending_approval_count"]), "note": "Bangladesh sewing costings awaiting CEO decision.", "tone": "warn"},
                {"label": "Rejected", "value": _format_count(local_sewing_summary["rejected_count"]), "note": "Rejected Bangladesh sewing costings.", "tone": "warn"},
            ]
        )
    if not can_view_executive_financials:
        kpi_cards = [
            card
            for card in kpi_cards
            if card["label"] not in {"Accounting Revenue", "Net Cash", "Receivables"}
        ]
        for card in kpi_cards:
            if card["label"] == "Open Pipeline":
                card["value"] = _format_count(open_opps)
                card["note"] = "Open opportunity rows. Pipeline value is restricted."
        kpi_cards.append(
            {
                "label": "Financials",
                "value": "Restricted",
                "note": "Internal financial metrics require costing permission.",
                "tone": "warn",
            }
        )

    ceo_chart_data = {
        "production_status_labels": production_status_labels,
        "production_status_values": production_status_values,
        "inventory_usage_labels": inventory_intelligence["category_usage_labels"],
        "inventory_usage_values": inventory_intelligence["category_usage_values"],
        "quotation_funnel_labels": quotation_funnel_labels,
        "quotation_funnel_values": quotation_funnel_values,
    }
    if can_view_executive_financials:
        ceo_chart_data.update(
            {
                "invoice_month_labels": invoice_month_labels,
                "invoice_month_values": invoice_month_values,
                "invoice_month_series": invoice_month_series,
                "profit_month_labels": profit_month_labels,
                "profit_month_values": profit_month_values,
                "profit_month_series": profit_month_series,
            }
        )

    context = {
        "today": today,
        "period_days": period_days,
        "period_label": period_label,
        "filter_values": filter_values,
        "side_options": [("", "All sides"), ("CA", "Canada"), ("BD", "Bangladesh")],
        "can_view_executive_financials": can_view_executive_financials,
        "local_sewing_summary": local_sewing_summary,
        "canada_export_revenue_rows": production_business["canada_export_revenue_rows"],
        "kpi_cards": kpi_cards,
        "alerts": alerts,
        "executive_alert_cards": executive_alert_cards,
        **automation_context,
        "ai_insight_cards": ai_insight_cards,
        "revenue_overview": revenue_overview,
        "profit_overview": profit_overview,
        "production_intelligence": production_intelligence,
        "inventory_intelligence": inventory_intelligence,
        "sales_intelligence": sales_intelligence,
        "operations_snapshot": operations_snapshot,
        "ceo_chart_data": ceo_chart_data,
        "key_trends": key_trends,
        "month_rows": month_rows,
        "lead_source_rows": lead_source_rows,
        "stage_rows": stage_rows,
        "production_rows": production_rows,
        "shipment_status_rows": shipment_status_rows,
        "top_assignees": top_assignees,
        "top_customers": top_customers,
        "top_pages": top_pages,
        "marketing_summary": {
            "visitors": website_totals.get("visitors") or 0,
            "sessions": website_totals.get("sessions") or 0,
            "page_views": website_totals.get("page_views") or 0,
            "conversions": website_totals.get("conversions") or 0,
            "search_clicks": search_totals.get("clicks") or 0,
            "search_impressions": search_totals.get("impressions") or 0,
            "social_engagement": social_totals.get("engagement") or 0,
            "social_reach": social_totals.get("reach") or 0,
            "social_views": social_totals.get("views") or 0,
            "active_campaigns": active_campaigns,
            "open_insights": open_insights,
        },
        "operations_summary": {
            "production_running": production_running,
            "production_hold": production_hold,
            "production_done_period": production_done_period,
            "production_delayed": production_delayed,
            "shipments_in_transit": shipments_in_transit,
            "shipments_delayed": shipments_delayed,
        },
        "finance_summary": {
            "revenue": finance_totals["revenue"] if can_view_executive_financials else None,
            "expenses": finance_totals["expenses"] if can_view_executive_financials else None,
            "net": finance_totals["net"] if can_view_executive_financials else None,
            "invoice_open_total": invoice_open_total if can_view_executive_financials else None,
            "invoice_open_values": invoice_open_values if can_view_executive_financials else [],
            "overdue_invoice_count": overdue_invoice_count if can_view_executive_financials else None,
        },
        "sales_summary": {
            "leads_period": leads_period,
            "opp_period": opp_period,
            "open_opps": open_opps,
            "won_period": won_period,
            "lost_period": lost_period,
            "overdue_followups": overdue_followups,
            "due_soon_followups": due_soon_followups,
        },
    }
    return render(request, "crm/ceo_dashboard.html", context)


def _advisor_score(base=82, penalties=None, bonuses=None):
    score = Decimal(str(base))
    for penalty in penalties or []:
        score -= Decimal(str(penalty))
    for bonus in bonuses or []:
        score += Decimal(str(bonus))
    return int(max(Decimal("0"), min(Decimal("100"), score)))


def _advisor_score_tone(score):
    if score >= 82:
        return "good"
    if score >= 65:
        return "blue"
    if score >= 45:
        return "warn"
    return "bad"


def _advisor_answer(question, metrics):
    text = (question or "").strip()
    if not text:
        return ""
    q = text.lower()

    if any(word in q for word in ["cash", "flow", "money", "runway"]):
        return (
            f"Cash is projected at {_format_money(metrics['forecast_cash_30'])} over the next 30 days. "
            f"Expected collections are {_format_money(metrics['receivables_30'])}, expected supplier payments are "
            f"{_format_money(metrics['payables_30'])}, and current net cash movement is "
            f"{_format_money(metrics['finance_net'])}. Prioritize overdue receivables if the forecast is tight."
        )

    if any(word in q for word in ["receivable", "invoice", "collection", "paid"]):
        return (
            f"There are {metrics['overdue_invoice_count']} overdue invoice(s) and "
            f"{_format_money(metrics['invoice_open_total'])} in open receivables. The next 30 days show "
            f"{_format_money(metrics['receivables_30'])} expected customer collections."
        )

    if any(word in q for word in ["sales", "pipeline", "lead", "opportunity", "conversion"]):
        return (
            f"Sales generated {metrics['leads_period']} lead(s) and {metrics['opp_period']} opportunity row(s) "
            f"in this period. Open pipeline is {metrics['open_pipeline_display']}, with a "
            f"{metrics['conversion_rate']:.1f}% lead-to-opportunity conversion rate. "
            f"{metrics['overdue_followups']} follow-up(s) are overdue."
        )

    if any(word in q for word in ["production", "factory", "order", "manufacturing"]):
        return (
            f"Production has {metrics['production_running']} running order(s), {metrics['production_hold']} on hold, "
            f"and {metrics['production_delayed']} delayed active order(s). Clear held and delayed orders before adding load."
        )

    if any(word in q for word in ["shipping", "shipment", "delivery", "logistics"]):
        return (
            f"Shipping has {metrics['shipments_in_transit']} shipment(s) in transit and "
            f"{metrics['shipments_delayed']} delayed shipment(s). Review delayed shipments first and update client visibility."
        )

    if any(word in q for word in ["marketing", "website", "google", "social", "campaign"]):
        return (
            f"Marketing shows {metrics['website_visitors']} website visitor(s), {metrics['search_clicks']} Google click(s), "
            f"{metrics['social_engagement']} social engagement(s), and {metrics['active_campaigns']} active campaign(s) "
            f"in the selected period."
        )

    return (
        f"The business health score is {metrics['overall_score']}/100. The biggest watch items are "
        f"{metrics['overdue_followups']} overdue follow-up(s), {metrics['overdue_invoice_count']} overdue invoice(s), "
        f"{metrics['production_delayed']} delayed production order(s), and {metrics['shipments_delayed']} delayed shipment(s)."
    )


@login_required
def ai_executive_advisor(request):
    today = timezone.localdate()
    try:
        period_days = int((request.GET.get("days") or "30").strip())
    except Exception:
        period_days = 30
    if period_days not in (7, 30, 60, 90, 180):
        period_days = 30

    side = (request.GET.get("side") or "").strip().upper()
    if side not in {"", "CA", "BD"}:
        side = ""

    question = (request.GET.get("question") or "").strip()[:500]
    start_period = today - timedelta(days=period_days - 1)
    previous_end = start_period - timedelta(days=1)
    previous_start = previous_end - timedelta(days=period_days - 1)
    period_label = f"Last {period_days} days"

    leads_period = Lead.objects.filter(created_date__range=(start_period, today)).count()
    prev_leads_period = Lead.objects.filter(created_date__range=(previous_start, previous_end)).count()
    overdue_followups = Lead.objects.filter(next_followup__lt=today).exclude(
        lead_status__in=["Converted", "Closed", "Disqualified", "Lost"]
    ).count()
    high_priority_leads = Lead.objects.filter(
        created_date__range=(start_period, today),
    ).filter(Q(priority__iexact="High") | Q(priority_level__iexact="High")).count()
    due_soon_followups = Lead.objects.filter(
        next_followup__range=(today, today + timedelta(days=7))
    ).exclude(lead_status__in=["Converted", "Closed", "Disqualified", "Lost"]).count()

    advisor_opportunity_qs = with_opportunity_reporting_date(Opportunity.objects.all())
    opp_period = advisor_opportunity_qs.filter(
        **{f"{OPPORTUNITY_REPORTING_DATE_ALIAS}__range": (start_period, today)}
    ).count()
    prev_opp_period = advisor_opportunity_qs.filter(
        **{f"{OPPORTUNITY_REPORTING_DATE_ALIAS}__range": (previous_start, previous_end)}
    ).count()
    open_pipeline_qs = open_pipeline_queryset(Opportunity.objects.all())
    open_pipeline_summary = summarize_pipeline(open_pipeline_qs, apply_open_definition=False)
    open_opps = open_pipeline_summary["count"]
    open_pipeline_values = open_pipeline_summary["rows"]
    open_pipeline_display = _format_currency_summary(open_pipeline_values)
    won_period = Opportunity.objects.filter(stage="Closed Won", updated_at__date__range=(start_period, today)).count()
    lost_period = Opportunity.objects.filter(stage="Closed Lost", updated_at__date__range=(start_period, today)).count()
    conversion_rate = round((opp_period / leads_period) * 100, 1) if leads_period else 0
    prev_conversion_rate = round((prev_opp_period / prev_leads_period) * 100, 1) if prev_leads_period else 0
    pipeline_rows = list(
        _with_opportunity_kpi_value(open_pipeline_qs)
        .values("stage", "kpi_currency")
        .annotate(count=Count("id"), value=Sum("kpi_order_value"))
        .order_by("-value", "-count")[:10]
    )
    pipeline_rows = _ceo_bar_rows(
        [
            {
                "label": row.get("stage") or "Unknown",
                "currency": (row.get("kpi_currency") or "CAD").upper(),
                "count": int(row.get("count") or 0),
                "value": _ceo_decimal(row.get("value")),
            }
            for row in pipeline_rows
        ],
        ["count", "value"],
    )

    production_qs = ProductionOrder.objects.all()
    if side == "CA":
        production_qs = production_qs.filter(factory_location="ca")
    elif side == "BD":
        production_qs = production_qs.filter(factory_location="bd")
    advisor_production_rows = [
        {
            "order": order,
            "operational_status": get_production_operational_status(order),
        }
        for order in production_qs.prefetch_related("stages", "shipments")
    ]
    advisor_running_statuses = {
        "sample_development",
        "fabric_sourcing",
        "cutting",
        "printing",
        "sewing",
        "qc",
        "packing",
    }
    advisor_active_rows = [
        row for row in advisor_production_rows
        if row["operational_status"] in OPERATIONAL_ACTIVE_STATUSES
    ]
    production_running = len([
        row for row in advisor_production_rows
        if row["operational_status"] in advisor_running_statuses
    ])
    production_hold = 0
    production_delayed = len([
        row for row in advisor_production_rows
        if row["order"].bulk_deadline
        and row["order"].bulk_deadline < today
        and row["operational_status"] not in OPERATIONAL_FINISHED_STATUSES
    ])
    production_due_soon = len([
        row for row in advisor_active_rows
        if row["order"].bulk_deadline
        and today <= row["order"].bulk_deadline <= today + timedelta(days=14)
    ])

    shipping_qs = Shipment.objects.all()
    if side == "CA":
        shipping_qs = shipping_qs.filter(order__factory_location="ca")
    elif side == "BD":
        shipping_qs = shipping_qs.filter(order__factory_location="bd")
    shipments_in_transit = shipping_qs.filter(status__in=["booked", "shipped", "out_for_delivery"]).count()
    shipments_delayed = shipping_qs.filter(ship_date__lt=today).exclude(status__in=["delivered", "cancelled"]).count()
    shipments_without_tracking = shipping_qs.filter(tracking_number="").exclude(status__in=["delivered", "cancelled"]).count()

    cad_to_bdt = _get_latest_cad_to_bdt_rate()
    accounting_qs = AccountingEntry.objects.exclude(main_type="TRANSFER").exclude(status__iexact="CANCELLED")
    if side:
        accounting_qs = accounting_qs.filter(side=side)
    period_entries = list(
        accounting_qs.filter(date__range=(start_period, today)).only(
            "date", "direction", "side", "currency", "main_type", "amount_original", "amount_cad", "rate_to_cad"
        )[:2500]
    )
    previous_entries = list(
        accounting_qs.filter(date__range=(previous_start, previous_end)).only(
            "date", "direction", "side", "currency", "main_type", "amount_original", "amount_cad", "rate_to_cad"
        )[:2500]
    )

    def _period_finance(entries):
        revenue = Decimal("0")
        expenses = Decimal("0")
        for entry in entries:
            amount = _ceo_amount_cad(entry, cad_to_bdt)
            if (entry.direction or "").upper().strip() == "IN":
                revenue += amount
            elif (entry.direction or "").upper().strip() == "OUT":
                expenses += amount
        return {"revenue": revenue, "expenses": expenses, "net": revenue - expenses}

    finance = _period_finance(period_entries)
    previous_finance = _period_finance(previous_entries)
    current_cash = Decimal("0")
    for entry in accounting_qs.filter(date__lte=today).only(
        "date", "direction", "currency", "amount_original", "amount_cad", "rate_to_cad"
    )[:3500]:
        amount = _ceo_amount_cad(entry, cad_to_bdt)
        if (entry.direction or "").upper().strip() == "IN":
            current_cash += amount
        elif (entry.direction or "").upper().strip() == "OUT":
            current_cash -= amount

    invoice_qs = Invoice.objects.filter(is_archived=False).exclude(status="cancelled") if Invoice is not None else None
    if invoice_qs is not None and side:
        invoice_qs = invoice_qs.filter(Q(invoice_region=side) | Q(invoice_region="", currency="BDT" if side == "BD" else "CAD"))
    invoice_open_total = Decimal("0")
    overdue_invoice_count = 0
    receivables_30 = Decimal("0")
    overdue_receivable_rows = []
    if invoice_qs is not None:
        try:
            open_invoices = list(
                invoice_qs.exclude(status="paid")
                .select_related("customer", "order", "order__customer")
                .order_by("due_date", "-issue_date")[:1500]
            )
            for invoice in open_invoices:
                balance = _ceo_decimal(invoice.balance)
                invoice_open_total += balance
                if invoice.due_date and invoice.due_date < today:
                    overdue_invoice_count += 1
                    overdue_receivable_rows.append(
                        {
                            "invoice": invoice,
                            "customer": _exec_customer_label(invoice.customer or getattr(invoice.order, "customer", None)) if "_exec_customer_label" in globals() else str(invoice.customer or "No customer"),
                            "due_date": invoice.due_date,
                            "balance": balance,
                        }
                    )
                if invoice.due_date and today <= invoice.due_date <= today + timedelta(days=30):
                    receivables_30 += balance
        except (OperationalError, ProgrammingError):
            invoice_open_total = Decimal("0")
            overdue_invoice_count = 0
            receivables_30 = Decimal("0")
            overdue_receivable_rows = []

    payable_entries_30 = list(
        accounting_qs.filter(direction="OUT", date__lte=today + timedelta(days=30))
        .exclude(status__iexact="PAID")
        .only("date", "direction", "currency", "amount_original", "amount_cad", "rate_to_cad", "status", "description", "sub_type")[:1000]
    )
    payables_30 = sum((_ceo_amount_cad(entry, cad_to_bdt) for entry in payable_entries_30), Decimal("0"))
    forecast_cash_30 = current_cash + receivables_30 - payables_30
    forecast_rows = [
        {"label": "30 days", "cash": forecast_cash_30, "collections": receivables_30, "payments": payables_30},
        {
            "label": "60 days",
            "cash": current_cash + (receivables_30 * Decimal("1.6")) - (payables_30 * Decimal("1.6")),
            "collections": receivables_30 * Decimal("1.6"),
            "payments": payables_30 * Decimal("1.6"),
        },
        {
            "label": "90 days",
            "cash": current_cash + (receivables_30 * Decimal("2.1")) - (payables_30 * Decimal("2.1")),
            "collections": receivables_30 * Decimal("2.1"),
            "payments": payables_30 * Decimal("2.1"),
        },
    ]
    forecast_rows = _ceo_bar_rows(forecast_rows, ["cash", "collections", "payments"])

    WebsiteTrafficDaily = _ceo_optional_model("marketing", "WebsiteTrafficDaily")
    SeoQueryDaily = _ceo_optional_model("marketing", "SeoQueryDaily")
    AccountMetricDaily = _ceo_optional_model("marketing", "AccountMetricDaily")
    Campaign = _ceo_optional_model("marketing", "Campaign")
    InsightItem = _ceo_optional_model("marketing", "InsightItem")
    website_totals = _ceo_aggregate(
        WebsiteTrafficDaily,
        WebsiteTrafficDaily.objects.filter(date__range=(start_period, today)) if WebsiteTrafficDaily else None,
        visitors=Sum("visitors"),
        page_views=Sum("page_views"),
        conversions=Sum("conversions"),
    )
    search_totals = _ceo_aggregate(
        SeoQueryDaily,
        SeoQueryDaily.objects.filter(date__range=(start_period, today)) if SeoQueryDaily else None,
        clicks=Sum("clicks"),
        impressions=Sum("impressions"),
    )
    social_totals = _ceo_aggregate(
        AccountMetricDaily,
        AccountMetricDaily.objects.filter(date__range=(start_period, today)) if AccountMetricDaily else None,
        engagement=Sum("engagement_total"),
        reach=Sum("reach"),
    )
    active_campaigns = 0
    open_insights = 0
    try:
        if Campaign is not None:
            active_campaigns = Campaign.objects.filter(is_active=True).count()
        if InsightItem is not None:
            open_insights = InsightItem.objects.filter(status="open").count()
    except (OperationalError, ProgrammingError):
        active_campaigns = 0
        open_insights = 0

    sales_score = _advisor_score(
        82,
        penalties=[min(overdue_followups * 2, 18), 8 if conversion_rate < prev_conversion_rate else 0],
        bonuses=[6 if open_opps else 0],
    )
    production_score = _advisor_score(
        84,
        penalties=[min(production_delayed * 5, 25), min(production_hold * 3, 15)],
        bonuses=[4 if production_running else 0],
    )
    shipping_score = _advisor_score(
        86,
        penalties=[min(shipments_delayed * 6, 24), min(shipments_without_tracking * 2, 12)],
        bonuses=[4 if shipments_in_transit else 0],
    )
    marketing_score = _advisor_score(
        74,
        penalties=[8 if not website_totals.get("visitors") else 0, 6 if not search_totals.get("clicks") else 0],
        bonuses=[5 if active_campaigns else 0, 4 if open_insights else 0],
    )
    accounting_score = _advisor_score(
        82,
        penalties=[min(overdue_invoice_count * 4, 24), 12 if forecast_cash_30 < 0 else 0, 8 if finance["net"] < 0 else 0],
        bonuses=[6 if finance["net"] >= 0 else 0],
    )
    department_scores = [
        {"label": "Sales", "score": sales_score, "tone": _advisor_score_tone(sales_score), "detail": f"{overdue_followups} overdue follow-up(s), {conversion_rate:.1f}% conversion."},
        {"label": "Production", "score": production_score, "tone": _advisor_score_tone(production_score), "detail": f"{production_running} running, {production_delayed} delayed."},
        {"label": "Shipping", "score": shipping_score, "tone": _advisor_score_tone(shipping_score), "detail": f"{shipments_in_transit} in transit, {shipments_delayed} delayed."},
        {"label": "Marketing", "score": marketing_score, "tone": _advisor_score_tone(marketing_score), "detail": f"{website_totals.get('visitors') or 0} visitors, {active_campaigns} active campaigns."},
        {"label": "Accounting", "score": accounting_score, "tone": _advisor_score_tone(accounting_score), "detail": f"{overdue_invoice_count} overdue invoice(s), {_format_money(forecast_cash_30)} 30-day cash."},
    ]
    overall_score = int(sum(row["score"] for row in department_scores) / len(department_scores))

    critical_alerts = []
    if overdue_invoice_count:
        critical_alerts.append({"title": f"{overdue_invoice_count} overdue receivable(s)", "detail": f"Open receivables total {_format_money(invoice_open_total)}.", "tone": "bad"})
    if forecast_cash_30 < 0:
        critical_alerts.append({"title": "30-day cash forecast is negative", "detail": f"Projected cash is {_format_money(forecast_cash_30)}.", "tone": "bad"})
    if overdue_followups:
        critical_alerts.append({"title": f"{overdue_followups} overdue sales follow-up(s)", "detail": "Pipeline risk increases when follow-ups slip.", "tone": "warn"})
    if production_delayed:
        critical_alerts.append({"title": f"{production_delayed} delayed production order(s)", "detail": "Factory timelines need review.", "tone": "warn"})
    if shipments_delayed:
        critical_alerts.append({"title": f"{shipments_delayed} delayed shipment(s)", "detail": "Delivery status needs client-facing updates.", "tone": "warn"})
    if not critical_alerts:
        critical_alerts.append({"title": "No critical executive alerts", "detail": "Core warning signals are clear for this period.", "tone": "good"})

    recommended_actions = [
        {"title": "Collect overdue receivables", "detail": f"Follow up on {overdue_invoice_count} overdue invoice(s) before new spending commitments.", "priority": "High" if overdue_invoice_count else "Normal"},
        {"title": "Work overdue sales follow-ups", "detail": f"Prioritize {overdue_followups} overdue lead follow-up(s) and {due_soon_followups} due soon.", "priority": "High" if overdue_followups else "Normal"},
        {"title": "Clear production blockers", "detail": f"Review {production_hold} held order(s), {production_delayed} delayed order(s), and {production_due_soon} due soon.", "priority": "High" if production_delayed else "Normal"},
        {"title": "Update logistics visibility", "detail": f"Check {shipments_delayed} delayed shipment(s) and {shipments_without_tracking} shipment(s) without tracking.", "priority": "High" if shipments_delayed else "Normal"},
        {"title": "Review marketing insights", "detail": f"Use {open_insights} open marketing insight(s) and {active_campaigns} active campaign(s) to protect lead flow.", "priority": "Normal"},
    ]

    summary_cards = [
        {"label": "Business Health", "value": f"{overall_score}/100", "note": "Composite department score.", "tone": _advisor_score_tone(overall_score)},
        {"label": "30-Day Cash Forecast", "value": _format_money(forecast_cash_30), "note": f"Collections {_format_money(receivables_30)} less payments {_format_money(payables_30)}.", "tone": "good" if forecast_cash_30 >= 0 else "bad"},
        {"label": "Open Pipeline", "value": open_pipeline_display, "note": f"{open_opps} open opportunity row(s); currencies are not combined.", "tone": "blue"},
        {"label": "Overdue Receivables", "value": _format_count(overdue_invoice_count), "note": f"Open AR {_format_money(invoice_open_total)}.", "tone": "bad" if overdue_invoice_count else "good"},
        {"label": "Sales Follow-Ups", "value": _format_count(overdue_followups), "note": f"{due_soon_followups} due in the next 7 days.", "tone": "warn" if overdue_followups else "good"},
        {"label": "Production Warnings", "value": _format_count(production_delayed), "note": f"{production_hold} order(s) on hold.", "tone": "warn" if production_delayed else "good"},
        {"label": "Shipping Warnings", "value": _format_count(shipments_delayed), "note": f"{shipments_without_tracking} shipment(s) missing tracking.", "tone": "warn" if shipments_delayed else "good"},
        {"label": "Marketing Signals", "value": _format_count(website_totals.get("visitors")), "note": f"{search_totals.get('clicks') or 0} search clicks, {social_totals.get('engagement') or 0} social engagement.", "tone": "blue"},
    ]

    metrics = {
        "overall_score": overall_score,
        "forecast_cash_30": forecast_cash_30,
        "receivables_30": receivables_30,
        "payables_30": payables_30,
        "finance_net": finance["net"],
        "overdue_invoice_count": overdue_invoice_count,
        "invoice_open_total": invoice_open_total,
        "leads_period": leads_period,
        "opp_period": opp_period,
        "open_pipeline_display": open_pipeline_display,
        "conversion_rate": conversion_rate,
        "overdue_followups": overdue_followups,
        "production_running": production_running,
        "production_hold": production_hold,
        "production_delayed": production_delayed,
        "shipments_in_transit": shipments_in_transit,
        "shipments_delayed": shipments_delayed,
        "website_visitors": website_totals.get("visitors") or 0,
        "search_clicks": search_totals.get("clicks") or 0,
        "social_engagement": social_totals.get("engagement") or 0,
        "active_campaigns": active_campaigns,
    }
    advisor_answer = _advisor_answer(question, metrics)

    context = {
        "today": today,
        "period_label": period_label,
        "filter_values": {"days": str(period_days), "side": side, "question": question},
        "side_options": [("", "All sides"), ("CA", "Canada"), ("BD", "Bangladesh")],
        "summary_cards": summary_cards,
        "critical_alerts": critical_alerts,
        "recommended_actions": recommended_actions,
        "department_scores": department_scores,
        "forecast_rows": forecast_rows,
        "overdue_receivable_rows": overdue_receivable_rows[:8],
        "pipeline_rows": pipeline_rows,
        "advisor_answer": advisor_answer,
        "suggested_questions": [
            "What should I focus on today?",
            "How is cash flow looking?",
            "What sales risks should I watch?",
            "Are production or shipping timelines at risk?",
            "How is marketing contributing to leads?",
        ],
        "sales_metrics": {
            "leads_period": leads_period,
            "prev_leads_period": prev_leads_period,
            "opp_period": opp_period,
            "prev_opp_period": prev_opp_period,
            "won_period": won_period,
            "lost_period": lost_period,
            "conversion_rate": conversion_rate,
            "prev_conversion_rate": prev_conversion_rate,
            "high_priority_leads": high_priority_leads,
        },
        "operations_metrics": {
            "production_running": production_running,
            "production_hold": production_hold,
            "production_delayed": production_delayed,
            "production_due_soon": production_due_soon,
            "shipments_in_transit": shipments_in_transit,
            "shipments_delayed": shipments_delayed,
            "shipments_without_tracking": shipments_without_tracking,
        },
        "marketing_metrics": {
            "website_visitors": website_totals.get("visitors") or 0,
            "page_views": website_totals.get("page_views") or 0,
            "conversions": website_totals.get("conversions") or 0,
            "search_clicks": search_totals.get("clicks") or 0,
            "search_impressions": search_totals.get("impressions") or 0,
            "social_engagement": social_totals.get("engagement") or 0,
            "social_reach": social_totals.get("reach") or 0,
            "active_campaigns": active_campaigns,
            "open_insights": open_insights,
        },
        "finance_metrics": {
            "revenue": finance["revenue"],
            "expenses": finance["expenses"],
            "net": finance["net"],
            "previous_revenue": previous_finance["revenue"],
            "current_cash": current_cash,
            "invoice_open_total": invoice_open_total,
            "receivables_30": receivables_30,
            "payables_30": payables_30,
            "forecast_cash_30": forecast_cash_30,
        },
    }
    return render(request, "crm/ai_executive_advisor.html", context)


def _briefing_parse_date(value, fallback):
    parsed = parse_date((value or "").strip()) if value else None
    return parsed or fallback


def _briefing_customer_label(customer):
    if not customer:
        return "No customer"
    return getattr(customer, "account_brand", "") or getattr(customer, "contact_name", "") or f"Customer {customer.pk}"


def _briefing_invoice_side(invoice):
    region = (getattr(invoice, "invoice_region", "") or "").upper().strip()
    if region in {"CA", "BD"}:
        return region
    currency = (getattr(invoice, "currency", "") or "").upper().strip()
    return "BD" if currency == "BDT" else "CA"


def _briefing_option_label(options, value, fallback):
    for option_value, label in options:
        if option_value == value:
            return label
    return fallback


def _briefing_date_label(value):
    return value.strftime("%b %d, %Y") if value else "No date"


def _briefing_person_label(user):
    if not user:
        return "Unassigned"
    full_name = ""
    try:
        full_name = user.get_full_name()
    except Exception:
        full_name = ""
    return full_name or getattr(user, "username", "") or "Assigned"


def _briefing_lead_label(lead):
    return getattr(lead, "account_brand", "") or getattr(lead, "contact_name", "") or getattr(lead, "lead_id", "") or "Unnamed lead"


def _briefing_opportunity_customer_label(opportunity):
    customer = getattr(opportunity, "customer", None)
    if customer:
        return _briefing_customer_label(customer)
    lead = getattr(opportunity, "lead", None)
    return getattr(lead, "account_brand", "") or "No customer"


def _build_daily_ceo_briefing_email_draft(context):
    side_label = _briefing_option_label(
        context.get("side_options", []),
        context.get("filter_values", {}).get("side", ""),
        "All sides",
    )
    department_label = _briefing_option_label(
        context.get("department_options", []),
        context.get("filter_values", {}).get("department", ""),
        "All departments",
    )
    subject = f"Daily CEO Briefing - {context.get('range_label', 'Today')}"
    lines = [
        "Daily CEO Briefing",
        f"Report window: {context.get('range_label', '')}",
        f"Side: {side_label}",
        f"Department: {department_label}",
        "",
        "Draft only. No email has been sent automatically.",
        "",
        "EXECUTIVE SUMMARY",
    ]

    for card in context.get("executive_summary", []):
        lines.append(f"- {card.get('label')}: {card.get('value')} - {card.get('note')}")

    lines.extend(["", "NEW LEADS"])
    new_leads = context.get("new_leads") or []
    if new_leads:
        for lead in new_leads:
            priority = getattr(lead, "priority", "") or getattr(lead, "priority_level", "") or "Normal"
            source = getattr(lead, "source", "") or "Unknown source"
            product = getattr(lead, "product_interest", "") or "No product listed"
            assigned = _briefing_person_label(getattr(lead, "assigned_to", None))
            lines.append(f"- {_briefing_lead_label(lead)} ({getattr(lead, 'lead_id', 'No ID')}): {priority}, {source}, {product}, assigned to {assigned}.")
    else:
        lines.append("- No new leads in this briefing window.")

    lines.extend(["", "OPPORTUNITY FOLLOW UPS"])
    opportunity_rows = context.get("open_opportunity_rows") or []
    if opportunity_rows:
        for opportunity in opportunity_rows:
            amount = _format_money(getattr(opportunity, "order_value", None))
            lines.append(
                f"- {getattr(opportunity, 'opportunity_id', 'Opportunity')}: "
                f"{_briefing_opportunity_customer_label(opportunity)}, "
                f"{getattr(opportunity, 'stage', 'No stage')}, "
                f"next follow up {_briefing_date_label(getattr(opportunity, 'next_followup', None))}, "
                f"value {amount}."
            )
    else:
        lines.append("- No open opportunity follow-ups need attention for this filter.")

    lines.extend(["", "PRODUCTION ALERTS"])
    production_rows = context.get("production_alert_rows") or []
    if production_rows:
        for order in production_rows:
            customer = _briefing_customer_label(getattr(order, "customer", None))
            product = getattr(getattr(order, "product", None), "name", "") or "No product"
            lines.append(
                f"- {getattr(order, 'purchase_order_number', '') or getattr(order, 'title', 'Production order')}: "
                f"{customer}, {product}, status {order.get_status_display()}, "
                f"deadline {_briefing_date_label(getattr(order, 'bulk_deadline', None))}, "
                f"qty {getattr(order, 'qty_total', 0)}."
            )
    else:
        lines.append("- No production delay or due-soon alerts for this filter.")

    lines.extend(["", "SHIPPING ALERTS"])
    shipping_rows = context.get("shipping_alert_rows") or []
    if shipping_rows:
        for shipment in shipping_rows:
            customer = _briefing_customer_label(getattr(shipment, "customer", None))
            tracking = getattr(shipment, "tracking_number", "") or "Pending tracking"
            lines.append(
                f"- {tracking}: {customer}, carrier {shipment.get_carrier_display()}, "
                f"status {shipment.get_status_display()}, ship date {_briefing_date_label(getattr(shipment, 'ship_date', None))}."
            )
    else:
        lines.append("- No delayed or due-soon shipments for this filter.")

    lines.extend(["", "OVERDUE INVOICES"])
    overdue_rows = context.get("overdue_invoice_rows") or []
    if overdue_rows:
        for row in overdue_rows:
            lines.append(
                f"- {row.get('invoice')}: {row.get('customer')}, "
                f"due {_briefing_date_label(row.get('due_date'))}, "
                f"balance {_format_money(row.get('balance'))}, side {row.get('side')}."
            )
    else:
        lines.append("- No overdue invoices for this filter.")

    lines.extend(
        [
            "",
            "CASH POSITION",
            f"- Current cash estimate: {_format_money(context.get('current_cash'))}",
            f"- Period revenue: {_format_money(context.get('period_revenue'))}",
            f"- Period expenses: {_format_money(context.get('period_expenses'))}",
            f"- Period net cash: {_format_money(context.get('period_net_cash'))}",
            f"- Payables due soon: {_format_money(context.get('payables_due_soon'))}",
            f"- Open receivables: {_format_money(context.get('invoice_open_total'))}",
            f"- Cash warning: {'Yes' if context.get('cash_flow_warning') else 'No'}",
            "",
            "RECOMMENDED ACTIONS",
        ]
    )
    for action in context.get("recommended_actions", []):
        lines.append(f"- {action.get('title')}: {action.get('detail')}")

    lines.extend(["", "AI STYLE RECOMMENDATIONS"])
    for recommendation in context.get("ai_recommendations", []):
        lines.append(f"- {recommendation.get('title')}: {recommendation.get('detail')}")

    lines.extend(["", "Review this draft before sending."])
    return subject, "\n".join(lines)


def _build_daily_ceo_briefing_context(request):
    today = timezone.localdate()
    date_from = _briefing_parse_date(request.GET.get("date_from"), today)
    date_to = _briefing_parse_date(request.GET.get("date_to"), today)
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    side = (request.GET.get("side") or "").strip().upper()
    if side not in {"", "CA", "BD"}:
        side = ""

    department = (request.GET.get("department") or "").strip().lower()
    department_options = [
        ("", "All departments"),
        ("sales", "Sales"),
        ("production", "Production"),
        ("shipping", "Shipping"),
        ("accounting", "Accounting"),
        ("marketing", "Marketing"),
    ]
    if department not in {value for value, _label in department_options}:
        department = ""

    def _show(name):
        return not department or department == name

    filter_values = {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "side": side,
        "department": department,
    }
    range_label = date_from.strftime("%b %d, %Y") if date_from == date_to else f"{date_from.strftime('%b %d, %Y')} - {date_to.strftime('%b %d, %Y')}"

    leads_qs = Lead.objects.filter(created_date__range=(date_from, date_to))
    if side:
        leads_qs = leads_qs.filter(market=side)
    new_leads_total = leads_qs.count()
    high_priority_leads = leads_qs.filter(Q(priority__iexact="High") | Q(priority_level__iexact="High")).count()
    new_leads = list(
        leads_qs.select_related("assigned_to")
        .only("lead_id", "account_brand", "contact_name", "source", "priority", "priority_level", "product_interest", "created_date", "assigned_to__username", "assigned_to__first_name", "assigned_to__last_name")
        .order_by("-created_date", "-id")[:10]
    )

    opportunities_needing_followup_qs = (
        open_pipeline_queryset(Opportunity.objects.all())
        .filter(Q(next_followup__lte=date_to) | Q(next_followup__isnull=True))
    )
    if side:
        opportunities_needing_followup_qs = opportunities_needing_followup_qs.filter(lead__market=side)
    opportunities_needing_followup = opportunities_needing_followup_qs.count()
    open_opportunities = opportunities_needing_followup_qs.aggregate(
        count=Count("id"),
        total=Sum("order_value"),
    )
    open_opportunity_rows = list(
        opportunities_needing_followup_qs.select_related("customer", "lead")
        .only("opportunity_id", "stage", "order_value", "next_followup", "customer__account_brand", "customer__contact_name", "lead__account_brand")
        .order_by("next_followup", "-order_value")[:10]
    )

    production_qs = ProductionOrder.objects.all()
    if side == "CA":
        production_qs = production_qs.filter(factory_location="ca")
    elif side == "BD":
        production_qs = production_qs.filter(factory_location="bd")
    briefing_production_rows = [
        {
            "order": order,
            "operational_status": get_production_operational_status(order),
        }
        for order in production_qs.select_related("customer", "opportunity", "product")
        .prefetch_related("stages", "shipments")
        .only(
            "order_code",
            "title",
            "status",
            "bulk_deadline",
            "qty_total",
            "factory_location",
            "production_order_type",
            "style_name",
            "notes",
            "accessories_note",
            "extra_order_note",
            "fabric_required_kg",
            "fabric_received_kg",
            "updated_at",
            "customer__account_brand",
            "customer__contact_name",
            "opportunity__opportunity_id",
            "product__name",
        )
    ]
    briefing_active_rows = [
        row for row in briefing_production_rows
        if row["operational_status"] in OPERATIONAL_ACTIVE_STATUSES
    ]
    production_delays = len([
        row for row in briefing_production_rows
        if row["order"].bulk_deadline
        and row["order"].bulk_deadline < today
        and row["operational_status"] not in OPERATIONAL_FINISHED_STATUSES
    ])
    production_due_soon = len([
        row for row in briefing_active_rows
        if row["order"].bulk_deadline
        and today <= row["order"].bulk_deadline <= today + timedelta(days=7)
    ])
    production_alert_rows = [
        row["order"] for row in sorted(
            [
                row for row in briefing_active_rows
                if row["order"].bulk_deadline
                and row["order"].bulk_deadline <= today + timedelta(days=7)
            ],
            key=lambda row: (row["order"].bulk_deadline or date.max, row["order"].updated_at),
        )[:10]
    ]

    shipping_qs = Shipment.objects.all()
    if side == "CA":
        shipping_qs = shipping_qs.filter(order__factory_location="ca")
    elif side == "BD":
        shipping_qs = shipping_qs.filter(order__factory_location="bd")
    shipments_due_soon = shipping_qs.filter(ship_date__range=(today, today + timedelta(days=7))).exclude(status__in=["delivered", "cancelled"]).count()
    shipments_delayed = shipping_qs.filter(ship_date__lt=today).exclude(status__in=["delivered", "cancelled"]).count()
    shipping_alert_rows = list(
        shipping_qs.select_related("customer", "order", "opportunity")
        .filter(Q(ship_date__lt=today) | Q(ship_date__range=(today, today + timedelta(days=7))))
        .exclude(status__in=["delivered", "cancelled"])
        .only("tracking_number", "carrier", "status", "ship_date", "customer__account_brand", "customer__contact_name", "order__order_code", "opportunity__opportunity_id")
        .order_by("ship_date", "-created_at")[:10]
    )

    invoice_open_total = Decimal("0")
    overdue_receivables = Decimal("0")
    overdue_invoice_count = 0
    overdue_invoice_rows = []
    if Invoice is not None:
        invoice_qs = Invoice.objects.filter(is_archived=False).exclude(status__in=["paid", "cancelled"]).select_related("customer", "order", "order__customer")
        if side:
            invoice_qs = invoice_qs.filter(Q(invoice_region=side) | Q(invoice_region="", currency="BDT" if side == "BD" else "CAD"))
        for invoice in invoice_qs.order_by("due_date", "-issue_date")[:1500]:
            balance = _ceo_decimal(invoice.balance)
            invoice_open_total += balance
            if invoice.due_date and invoice.due_date < today:
                overdue_invoice_count += 1
                overdue_receivables += balance
                overdue_invoice_rows.append(
                    {
                        "invoice": invoice,
                        "customer": _briefing_customer_label(invoice.customer or getattr(invoice.order, "customer", None)),
                        "balance": balance,
                        "due_date": invoice.due_date,
                        "side": _briefing_invoice_side(invoice),
                    }
                )

    cad_to_bdt = _get_latest_cad_to_bdt_rate()
    accounting_qs = AccountingEntry.objects.exclude(main_type="TRANSFER").exclude(status__iexact="CANCELLED")
    if side:
        accounting_qs = accounting_qs.filter(side=side)

    period_revenue = Decimal("0")
    period_expenses = Decimal("0")
    current_cash = Decimal("0")
    for entry in accounting_qs.filter(date__lte=today).only("date", "direction", "currency", "amount_original", "amount_cad", "rate_to_cad", "main_type")[:3500]:
        amount = _ceo_amount_cad(entry, cad_to_bdt)
        direction = (entry.direction or "").upper().strip()
        if direction == "IN":
            current_cash += amount
        elif direction == "OUT":
            current_cash -= amount
        if date_from <= entry.date <= date_to:
            if direction == "IN":
                period_revenue += amount
            elif direction == "OUT":
                period_expenses += amount
    period_net_cash = period_revenue - period_expenses

    payable_entries_due = list(
        accounting_qs.filter(direction="OUT", date__range=(today, today + timedelta(days=7)))
        .exclude(status__iexact="PAID")
        .only("date", "status", "sub_type", "description", "currency", "amount_original", "amount_cad", "rate_to_cad")[:100]
    )
    payables_due_soon = sum((_ceo_amount_cad(entry, cad_to_bdt) for entry in payable_entries_due), Decimal("0"))
    cash_flow_warning = current_cash + overdue_receivables - payables_due_soon < 0 or period_net_cash < 0

    top_customer_qs = with_opportunity_reporting_date(
        Opportunity.objects.select_related("customer", "lead")
    ).filter(**{f"{OPPORTUNITY_REPORTING_DATE_ALIAS}__range": (date_from, date_to)})
    if side:
        top_customer_qs = top_customer_qs.filter(lead__market=side)
    top_customer_rows = list(
        top_customer_qs
        .values("customer__account_brand", "customer__contact_name", "lead__account_brand")
        .annotate(total=Sum("order_value"), count=Count("id"))
        .order_by("-total", "-count")[:8]
    )
    top_customer_activity = [
        {
            "label": row.get("customer__account_brand") or row.get("customer__contact_name") or row.get("lead__account_brand") or "Unassigned customer",
            "total": _ceo_decimal(row.get("total")),
            "count": int(row.get("count") or 0),
        }
        for row in top_customer_rows
    ]

    WebsiteTrafficDaily = _ceo_optional_model("marketing", "WebsiteTrafficDaily")
    SeoQueryDaily = _ceo_optional_model("marketing", "SeoQueryDaily")
    AccountMetricDaily = _ceo_optional_model("marketing", "AccountMetricDaily")
    Campaign = _ceo_optional_model("marketing", "Campaign")
    InsightItem = _ceo_optional_model("marketing", "InsightItem")
    website_totals = _ceo_aggregate(
        WebsiteTrafficDaily,
        WebsiteTrafficDaily.objects.filter(date__range=(date_from, date_to)) if WebsiteTrafficDaily else None,
        visitors=Sum("visitors"),
        page_views=Sum("page_views"),
        conversions=Sum("conversions"),
    )
    search_totals = _ceo_aggregate(
        SeoQueryDaily,
        SeoQueryDaily.objects.filter(date__range=(date_from, date_to)) if SeoQueryDaily else None,
        clicks=Sum("clicks"),
        impressions=Sum("impressions"),
    )
    social_totals = _ceo_aggregate(
        AccountMetricDaily,
        AccountMetricDaily.objects.filter(date__range=(date_from, date_to)) if AccountMetricDaily else None,
        engagement=Sum("engagement_total"),
        reach=Sum("reach"),
    )
    active_campaigns = 0
    open_insights = 0
    try:
        if Campaign is not None:
            active_campaigns = Campaign.objects.filter(is_active=True).count()
        if InsightItem is not None:
            open_insights = InsightItem.objects.filter(status="open").count()
    except (OperationalError, ProgrammingError):
        active_campaigns = 0
        open_insights = 0

    executive_summary = [
        {"label": "New Leads", "value": _format_count(new_leads_total), "note": f"{high_priority_leads} high priority lead(s).", "tone": "good" if new_leads_total else "blue"},
        {"label": "Open Follow Ups", "value": _format_count(opportunities_needing_followup), "note": "Open opportunities due or missing follow-up.", "tone": "warn" if opportunities_needing_followup else "good"},
        {"label": "Production Delays", "value": _format_count(production_delays), "note": f"{production_due_soon} production order(s) due soon.", "tone": "bad" if production_delays else "blue"},
        {"label": "Shipping Alerts", "value": _format_count(shipments_delayed + shipments_due_soon), "note": f"{shipments_delayed} delayed, {shipments_due_soon} due soon.", "tone": "warn" if shipments_delayed else "blue"},
        {"label": "Overdue Receivables", "value": _format_money(overdue_receivables), "note": f"{overdue_invoice_count} overdue invoice(s).", "tone": "bad" if overdue_invoice_count else "good"},
        {"label": "Cash Position", "value": _format_money(current_cash), "note": f"Period net cash {_format_money(period_net_cash)}.", "tone": "bad" if cash_flow_warning else "good"},
    ]

    recommended_actions = []
    if high_priority_leads:
        recommended_actions.append({"title": "Assign high priority leads today", "detail": f"{high_priority_leads} high priority lead(s) arrived in the selected range.", "tone": "warn"})
    if opportunities_needing_followup:
        recommended_actions.append({"title": "Review open opportunity follow-ups", "detail": f"{opportunities_needing_followup} opportunity row(s) need follow-up attention.", "tone": "warn"})
    if production_delays:
        recommended_actions.append({"title": "Clear factory delay risks", "detail": f"{production_delays} active production order(s) are past bulk deadline.", "tone": "bad"})
    if shipments_delayed:
        recommended_actions.append({"title": "Update delayed shipments", "detail": f"{shipments_delayed} shipment(s) are past ship date and not delivered.", "tone": "warn"})
    if overdue_invoice_count:
        recommended_actions.append({"title": "Collect overdue receivables", "detail": f"Follow up on {overdue_invoice_count} overdue invoice(s) totaling {_format_money(overdue_receivables)}.", "tone": "bad"})
    if cash_flow_warning:
        recommended_actions.append({"title": "Watch cash before new commitments", "detail": f"Current cash plus overdue AR less near payables signals pressure.", "tone": "bad"})
    if not recommended_actions:
        recommended_actions.append({"title": "Keep the current operating rhythm", "detail": "No critical daily briefing blockers are showing for the selected filters.", "tone": "good"})

    ai_recommendations = [
        {
            "title": "Protect revenue follow-up",
            "detail": f"Start with {high_priority_leads} high priority lead(s) and {opportunities_needing_followup} open opportunity follow-up(s).",
        },
        {
            "title": "Reduce execution risk",
            "detail": f"Review {production_delays} production delay(s), {production_due_soon} due-soon order(s), and {shipments_delayed} delayed shipment(s).",
        },
        {
            "title": "Preserve cash discipline",
            "detail": f"Collect {_format_money(overdue_receivables)} overdue AR before approving discretionary spending.",
        },
        {
            "title": "Use marketing signals",
            "detail": f"Website visitors: {website_totals.get('visitors') or 0}; search clicks: {search_totals.get('clicks') or 0}; open insights: {open_insights}.",
        },
    ]

    visible = {
        "sales": _show("sales"),
        "production": _show("production"),
        "shipping": _show("shipping"),
        "accounting": _show("accounting"),
        "marketing": _show("marketing"),
    }

    email_draft_url = reverse("daily_ceo_briefing_email_draft")
    if request.GET.urlencode():
        email_draft_url = f"{email_draft_url}?{request.GET.urlencode()}"

    context = {
        "today": today,
        "range_label": range_label,
        "filter_values": filter_values,
        "side_options": [("", "All sides"), ("CA", "Canada"), ("BD", "Bangladesh")],
        "department_options": department_options,
        "visible": visible,
        "executive_summary": executive_summary,
        "new_leads_total": new_leads_total,
        "high_priority_leads": high_priority_leads,
        "new_leads": new_leads,
        "opportunities_needing_followup": opportunities_needing_followup,
        "open_opportunities": open_opportunities,
        "open_opportunity_rows": open_opportunity_rows,
        "production_delays": production_delays,
        "production_due_soon": production_due_soon,
        "production_alert_rows": production_alert_rows,
        "shipments_due_soon": shipments_due_soon,
        "shipments_delayed": shipments_delayed,
        "shipping_alert_rows": shipping_alert_rows,
        "overdue_invoice_count": overdue_invoice_count,
        "overdue_receivables": overdue_receivables,
        "overdue_invoice_rows": overdue_invoice_rows[:10],
        "invoice_open_total": invoice_open_total,
        "payables_due_soon": payables_due_soon,
        "payable_entries_due": payable_entries_due[:8],
        "current_cash": current_cash,
        "period_revenue": period_revenue,
        "period_expenses": period_expenses,
        "period_net_cash": period_net_cash,
        "cash_flow_warning": cash_flow_warning,
        "top_customer_activity": top_customer_activity,
        "marketing_summary": {
            "visitors": website_totals.get("visitors") or 0,
            "page_views": website_totals.get("page_views") or 0,
            "conversions": website_totals.get("conversions") or 0,
            "search_clicks": search_totals.get("clicks") or 0,
            "search_impressions": search_totals.get("impressions") or 0,
            "social_engagement": social_totals.get("engagement") or 0,
            "social_reach": social_totals.get("reach") or 0,
            "active_campaigns": active_campaigns,
            "open_insights": open_insights,
        },
        "recommended_actions": recommended_actions,
        "ai_recommendations": ai_recommendations,
        "email_future_note": "Daily email delivery can be added later, but no automatic emails are sent by this page.",
        "email_draft_url": email_draft_url,
    }
    return context


@login_required
def daily_ceo_briefing(request):
    return render(request, "crm/daily_ceo_briefing.html", _build_daily_ceo_briefing_context(request))


@login_required
def daily_ceo_briefing_email_draft(request):
    context = _build_daily_ceo_briefing_context(request)
    subject, body = _build_daily_ceo_briefing_email_draft(context)
    briefing_url = reverse("daily_ceo_briefing")
    if request.GET.urlencode():
        briefing_url = f"{briefing_url}?{request.GET.urlencode()}"
    context.update(
        {
            "email_subject": subject,
            "email_body": body,
            "briefing_url": briefing_url,
            "draft_generated_at": timezone.localtime(),
            "draft_notice": "This is a preview draft only. No email was sent, queued, or saved to the outbox.",
        }
    )
    return render(request, "crm/daily_ceo_briefing_email_draft.html", context)


@login_required
def main_dashboard(request):
    today = timezone.localdate()
    period_days, start_period, period_end, previous_start, previous_end, period_label, filter_values = (
        _dashboard_period_from_request(request, today)
    )
    can_view_order_lifecycle_profit = can_view_lifecycle_profit(request.user)
    lead_kpi_qs = _active_lead_queryset()
    opportunity_kpi_qs = _active_opportunity_queryset()
    opportunity_reporting_qs = with_opportunity_reporting_date(opportunity_kpi_qs)
    production_kpi_qs = _active_production_queryset()
    opp_period_filter = {f"{OPPORTUNITY_REPORTING_DATE_ALIAS}__range": (start_period, period_end)}
    prev_opp_period_filter = {f"{OPPORTUNITY_REPORTING_DATE_ALIAS}__range": (previous_start, previous_end)}

    # Leads
    leads_today = lead_kpi_qs.filter(created_date=today).count()
    leads_period = lead_kpi_qs.filter(created_date__range=(start_period, period_end)).count()
    prev_leads_period = lead_kpi_qs.filter(created_date__range=(previous_start, previous_end)).count()

    leads_daily_qs = (
        lead_kpi_qs.filter(created_date__range=(start_period, period_end))
        .values("created_date")
        .annotate(c=Count("id"))
        .order_by("created_date")
    )
    lead_map = {row["created_date"]: int(row["c"]) for row in leads_daily_qs if row.get("created_date")}

    leads_daily_labels = []
    leads_daily_values = []
    for i in range(period_days):
        d = start_period + timedelta(days=i)
        leads_daily_labels.append(d.strftime("%Y-%m-%d"))
        leads_daily_values.append(int(lead_map.get(d, 0)))

    # Opportunities
    opp_period = opportunity_reporting_qs.filter(**opp_period_filter).count()
    prev_opp_period = opportunity_reporting_qs.filter(**prev_opp_period_filter).count()

    opp_stage_base = opportunity_reporting_qs.filter(**opp_period_filter)
    if not opp_period:
        opp_stage_base = opportunity_kpi_qs
    opp_by_stage_qs = opp_stage_base.values("stage").annotate(c=Count("id"))
    opp_stage_map = {row.get("stage") or "Unknown": int(row.get("c") or 0) for row in opp_by_stage_qs}
    opp_stage_labels = []
    opp_stage_values = []
    for st, _ in Opportunity.STAGE_CHOICES:
        opp_stage_labels.append(st)
        opp_stage_values.append(int(opp_stage_map.get(st, 0)))
    # Include any unknown stages
    for stage, count in opp_stage_map.items():
        if stage not in opp_stage_labels:
            opp_stage_labels.append(stage)
            opp_stage_values.append(int(count))

    # Opp daily (for Leads vs Opportunities chart)
    opp_daily_qs = (
        opportunity_reporting_qs.filter(**opp_period_filter)
        .values(OPPORTUNITY_REPORTING_DATE_ALIAS)
        .annotate(c=Count("id"))
        .order_by(OPPORTUNITY_REPORTING_DATE_ALIAS)
    )
    opp_map = {
        row[OPPORTUNITY_REPORTING_DATE_ALIAS]: int(row["c"])
        for row in opp_daily_qs
        if row.get(OPPORTUNITY_REPORTING_DATE_ALIAS)
    }
    opp_daily_values = []
    for i in range(period_days):
        d = start_period + timedelta(days=i)
        opp_daily_values.append(int(opp_map.get(d, 0)))

    # Win vs Loss (safe guess using stage text)
    won_count = opportunity_reporting_qs.filter(
        stage__iexact="Closed Won",
        **opp_period_filter,
    ).count()
    lost_count = opportunity_reporting_qs.filter(
        stage__iexact="Closed Lost",
        **opp_period_filter,
    ).count()
    win_loss_labels = ["Won", "Lost"]
    win_loss_values = [int(won_count), int(lost_count)]

    # Lead status funnel (show qualification stage)
    lead_status_base = lead_kpi_qs.filter(created_date__range=(start_period, period_end))
    if not lead_status_base.exists():
        lead_status_base = lead_kpi_qs
    lead_status_qs = lead_status_base.values("lead_status").annotate(c=Count("id"))
    lead_status_map = {row.get("lead_status") or "Unknown": int(row.get("c") or 0) for row in lead_status_qs}
    lead_status_labels = []
    lead_status_values = []
    for st, _ in LEAD_STATUS_CHOICES:
        lead_status_labels.append(st)
        lead_status_values.append(int(lead_status_map.get(st, 0)))
    for st, cnt in lead_status_map.items():
        if st not in lead_status_labels:
            lead_status_labels.append(st)
            lead_status_values.append(int(cnt))

    # Accounting net per day (real cash flow line)
    cad_to_bdt = _get_latest_cad_to_bdt_rate()
    if not cad_to_bdt or cad_to_bdt <= 0:
        cad_to_bdt = None

    def _entry_amount_cad(entry):
        try:
            return convert_currency(
                entry.amount_original,
                entry.currency,
                "CAD",
                bdt_per_cad=cad_to_bdt,
                stored_rate_to_cad=getattr(entry, "rate_to_cad", None),
                stored_rate_to_bdt=entry.__dict__.get("rate_to_bdt"),
            )
        except CurrencyConversionError:
            return Decimal("0")

    def _entry_amount_bdt(entry):
        try:
            return convert_currency(
                entry.amount_original,
                entry.currency,
                "BDT",
                bdt_per_cad=cad_to_bdt,
                stored_rate_to_cad=getattr(entry, "rate_to_cad", None),
                stored_rate_to_bdt=entry.__dict__.get("rate_to_bdt"),
            )
        except CurrencyConversionError:
            return Decimal("0")

    def _sum_side(qs, side_code):
        totals = {
            "entries": 0,
            "income": Decimal("0"),
            "out": Decimal("0"),
        }
        source = qs.iterator() if hasattr(qs, "iterator") else iter(qs)
        for entry in source:
            totals["entries"] += 1
            direction = (entry.direction or "").upper().strip()
            if side_code == "CA":
                amt = _entry_amount_cad(entry)
            else:
                amt = _entry_amount_bdt(entry)
            if direction == "IN":
                totals["income"] += amt
            elif direction == "OUT":
                totals["out"] += amt
        totals["net"] = totals["income"] - totals["out"]
        return totals

    acc_only_fields = [
        "date",
        "direction",
        "side",
        "currency",
        "main_type",
        "sub_type",
        "amount_original",
        "amount_cad",
        "amount_bdt",
        "rate_to_cad",
        "rate_to_bdt",
        "production_order_id",
    ]
    all_accounting_entries = list(AccountingEntry.objects.only(*acc_only_fields))

    def _cash_window_metrics(start_date, end_date):
        revenue = Decimal("0")
        expense = Decimal("0")
        if start_date > end_date:
            return {"revenue": revenue, "expense": expense, "net": revenue}
        for entry in all_accounting_entries:
            if not entry.date or entry.date < start_date or entry.date > end_date:
                continue
            if (entry.main_type or "").upper().strip() == "TRANSFER":
                continue
            direction = (entry.direction or "").upper().strip()
            amt_cad = _entry_amount_cad(entry)
            if direction == "IN":
                revenue += amt_cad
            elif direction == "OUT":
                expense += amt_cad
        return {"revenue": revenue, "expense": expense, "net": revenue - expense}

    acc_map = defaultdict(lambda: Decimal("0"))
    revenue_map = defaultdict(lambda: Decimal("0"))
    expense_map = defaultdict(lambda: Decimal("0"))
    acc_entries_period = 0
    acc_entries_ca_period = 0
    acc_entries_bd_period = 0

    acc_income_cad = Decimal("0")
    acc_out_cad = Decimal("0")
    acc_income_bdt = Decimal("0")
    acc_out_bdt = Decimal("0")
    acc_ca_income_cad = Decimal("0")
    acc_ca_out_cad = Decimal("0")
    acc_bd_income_bdt = Decimal("0")
    acc_bd_out_bdt = Decimal("0")
    revenue_cad_period = Decimal("0")
    expense_cad_period = Decimal("0")
    swing_cad_period = Decimal("0")
    swing_bdt_period = Decimal("0")

    acc_period = [entry for entry in all_accounting_entries if entry.date and start_period <= entry.date <= period_end]
    for entry in acc_period:
        acc_entries_period += 1
        side = (entry.side or "").upper().strip()
        direction = (entry.direction or "").upper().strip()
        main_type = (entry.main_type or "").upper().strip()
        sub_type = (entry.sub_type or "").strip()
        is_transfer = main_type == "TRANSFER"
        is_swing = sub_type.lower() == "swing"

        if side == "CA":
            acc_entries_ca_period += 1
        elif side == "BD":
            acc_entries_bd_period += 1

        amt_cad = _entry_amount_cad(entry)
        amt_bdt = _entry_amount_bdt(entry)

        if entry.date and not is_transfer:
            if direction == "IN":
                acc_map[entry.date] += amt_cad
                revenue_map[entry.date] += amt_cad
            elif direction == "OUT":
                acc_map[entry.date] -= amt_cad
                expense_map[entry.date] += amt_cad

        if direction == "IN":
            acc_income_cad += amt_cad
            acc_income_bdt += amt_bdt
            if not is_transfer:
                revenue_cad_period += amt_cad
            if side == "CA":
                acc_ca_income_cad += amt_cad
            elif side == "BD":
                acc_bd_income_bdt += amt_bdt
            if is_swing and side == "CA":
                swing_cad_period += amt_cad
        elif direction == "OUT":
            acc_out_cad += amt_cad
            acc_out_bdt += amt_bdt
            if not is_transfer:
                expense_cad_period += amt_cad
            if side == "CA":
                acc_ca_out_cad += amt_cad
            elif side == "BD":
                acc_bd_out_bdt += amt_bdt
            if is_swing and side == "BD":
                swing_bdt_period += amt_bdt

    acc_net_cad = acc_income_cad - acc_out_cad
    acc_net_bdt = acc_income_bdt - acc_out_bdt
    acc_ca_net_cad = acc_ca_income_cad - acc_ca_out_cad
    acc_bd_net_bdt = acc_bd_income_bdt - acc_bd_out_bdt
    gross_margin_pct_period = Decimal("0")
    if revenue_cad_period > 0:
        gross_margin_pct_period = (revenue_cad_period - expense_cad_period) / revenue_cad_period * Decimal("100")
    net_cash_cad_period = revenue_cad_period - expense_cad_period
    previous_window = _cash_window_metrics(previous_start, previous_end)

    current_month_start = today.replace(day=1)
    month_starts = [_shift_month_start(current_month_start, offset) for offset in range(-5, 1)]
    monthly_revenue_map = {month_key: Decimal("0") for month_key in month_starts}
    monthly_expense_map = {month_key: Decimal("0") for month_key in month_starts}
    month_floor = month_starts[0]
    monthly_entries = [entry for entry in all_accounting_entries if entry.date and month_floor <= entry.date <= today]
    for entry in monthly_entries:
        if not entry.date:
            continue
        month_key = entry.date.replace(day=1)
        if month_key not in monthly_revenue_map:
            continue
        if (entry.main_type or "").upper().strip() == "TRANSFER":
            continue
        direction = (entry.direction or "").upper().strip()
        amt_cad = _entry_amount_cad(entry)
        if direction == "IN":
            monthly_revenue_map[month_key] += amt_cad
        elif direction == "OUT":
            monthly_expense_map[month_key] += amt_cad

    monthly_profit_labels = []
    monthly_profit_values = []
    monthly_profit_map = {}
    for month_key in month_starts:
        monthly_profit = monthly_revenue_map[month_key] - monthly_expense_map[month_key]
        monthly_profit_map[month_key] = monthly_profit
        monthly_profit_labels.append(month_key.strftime("%b"))
        monthly_profit_values.append(_to_float(monthly_profit))

    monthly_profit_cad = monthly_profit_map.get(current_month_start, Decimal("0"))
    previous_month_start = month_starts[-2] if len(month_starts) > 1 else current_month_start
    previous_month_profit_cad = monthly_profit_map.get(previous_month_start, Decimal("0"))

    prod_revenue_cad_period = Decimal("0")
    prod_cost_cad_period = Decimal("0")
    prod_profit_cad_period = Decimal("0")
    prod_margin_pct_period = Decimal("0")
    prod_entries = [entry for entry in acc_period if entry.production_order_id is not None]
    for entry in prod_entries:
        side = (entry.side or "").upper().strip()
        direction = (entry.direction or "").upper().strip()
        main_type = (entry.main_type or "").upper().strip()
        amt_cad = _entry_amount_cad(entry)
        if side == "CA" and direction == "IN":
            prod_revenue_cad_period += amt_cad
        if side == "BD" and direction == "OUT" and main_type in ["COGS", "EXPENSE"]:
            prod_cost_cad_period += amt_cad

    cash_daily_values = []
    for i in range(period_days):
        d = start_period + timedelta(days=i)
        cash_daily_values.append(_to_float(acc_map.get(d, Decimal("0"))))
    revenue_daily_values = []
    expense_daily_values = []
    for i in range(period_days):
        d = start_period + timedelta(days=i)
        revenue_daily_values.append(_to_float(revenue_map.get(d, Decimal("0"))))
        expense_daily_values.append(_to_float(expense_map.get(d, Decimal("0"))))

    orders_created_period = 0
    orders_processed_period = 0
    production_cost_bdt_period = Decimal("0")
    orders_created_map = {}
    orders_processed_map = {}
    production_orders_for_status = None
    if ProductionOrder is not None:
        try:
            orders_created_period = ProductionOrder.objects.filter(
                is_archived=False,
                created_at__date__range=(start_period, period_end)
            ).count()
            orders_created_qs = (
                production_kpi_qs.filter(created_at__date__range=(start_period, period_end))
                .annotate(d=TruncDate("created_at"))
                .values("d")
                .annotate(c=Count("id"))
                .order_by("d")
            )
            orders_created_map = {
                row["d"]: int(row["c"]) for row in orders_created_qs if row.get("d")
            }
            production_orders_for_status = list(
                production_kpi_qs.prefetch_related("stages", "shipments")
            )
            processed_orders = [
                order for order in production_orders_for_status
                if get_production_operational_status(order) == OPERATIONAL_STATUS_SHIPPED
                and order.updated_at.date() >= start_period
                and order.updated_at.date() <= period_end
            ]
            orders_processed_period = len(processed_orders)
            for order in processed_orders:
                processed_day = order.updated_at.date()
                orders_processed_map[processed_day] = int(orders_processed_map.get(processed_day, 0)) + 1
                cost = (
                    order.actual_total_cost_bdt
                    or order.production_total_cost_bdt
                    or order.production_sewing_cost_bdt
                    or Decimal("0")
                )
                if cost:
                    production_cost_bdt_period += cost
        except Exception:
            pass

    orders_created_daily_values = []
    orders_processed_daily_values = []
    for i in range(period_days):
        d = start_period + timedelta(days=i)
        orders_created_daily_values.append(int(orders_created_map.get(d, 0)))
        orders_processed_daily_values.append(int(orders_processed_map.get(d, 0)))

    if prod_cost_cad_period == 0 and production_cost_bdt_period and cad_to_bdt:
        prod_cost_cad_period = convert_currency(
            production_cost_bdt_period,
            "BDT",
            "CAD",
            bdt_per_cad=cad_to_bdt,
        )
    prod_cost_available = prod_cost_cad_period > 0
    prod_profit_cad_period = (
        prod_revenue_cad_period - prod_cost_cad_period
        if prod_revenue_cad_period > 0 and prod_cost_available
        else None
    )
    if prod_revenue_cad_period > 0 and prod_cost_available:
        prod_margin_pct_period = (prod_profit_cad_period / prod_revenue_cad_period) * Decimal("100")
    else:
        prod_margin_pct_period = None

    month_start = today.replace(day=1)
    acc_ca_month = _sum_side(
        [entry for entry in all_accounting_entries if entry.side == "CA" and entry.date and month_start <= entry.date <= today],
        "CA",
    )
    acc_ca_all = _sum_side(
        [entry for entry in all_accounting_entries if entry.side == "CA"],
        "CA",
    )
    acc_bd_month = _sum_side(
        [entry for entry in all_accounting_entries if entry.side == "BD" and entry.date and month_start <= entry.date <= today],
        "BD",
    )
    acc_bd_all = _sum_side(
        [entry for entry in all_accounting_entries if entry.side == "BD"],
        "BD",
    )

    # Payroll
    payroll_year = today.year
    payroll_month = today.month
    pm = BDStaffMonth.objects.filter(year=payroll_year, month=payroll_month)
    if not pm.exists():
        latest_pm = BDStaffMonth.objects.order_by("-year", "-month").first()
        if latest_pm:
            payroll_year = latest_pm.year
            payroll_month = latest_pm.month
            pm = BDStaffMonth.objects.filter(year=payroll_year, month=payroll_month)
    payroll_total = _to_float(pm.aggregate(s=Sum("final_pay_bdt"))["s"])
    payroll_ot = _to_float(pm.aggregate(s=Sum("overtime_total_bdt"))["s"])
    payroll_bonus = _to_float(pm.aggregate(s=Sum("bonus_bdt"))["s"])
    payroll_deduction = _to_float(pm.aggregate(s=Sum("deduction_bdt"))["s"])
    payroll_paid = pm.filter(is_paid=True).count()
    payroll_unpaid = pm.filter(is_paid=False).count()

    # Production status (optional)
    production_operational_rows = []
    production_operational_counts = Counter()
    prod_labels = []
    prod_counts = []
    if ProductionOrder is not None:
        try:
            if production_orders_for_status is None:
                production_orders_for_status = list(
                    production_kpi_qs.prefetch_related("stages", "shipments")
                )
            production_operational_rows = [
                {
                    "order": order,
                    "operational_status": get_production_operational_status(order),
                }
                for order in production_orders_for_status
            ]
            production_operational_counts = Counter(
                row["operational_status"] for row in production_operational_rows
            )
            for status, label in OPERATIONAL_STATUS_LABELS.items():
                count = production_operational_counts.get(status, 0)
                if count:
                    prod_labels.append(label)
                    prod_counts.append(int(count))
        except Exception:
            production_operational_rows = []
            production_operational_counts = Counter()
            pass

    # Shipping status (optional)
    ship_labels = ["This month"]
    ship_shipped = [0]
    ship_pending = [0]
    ship_delayed = [0]
    if Shipment is not None:
        try:
            shipped = Shipment.objects.filter(status__in=["shipped", "out_for_delivery", "delivered"]).count()
            pending = Shipment.objects.filter(status__in=["planned", "booked"]).count()
            delayed = Shipment.objects.filter(ship_date__lt=today).exclude(
                status__in=["delivered", "cancelled"]
            ).count()
            ship_shipped = [int(shipped)]
            ship_pending = [int(pending)]
            ship_delayed = [int(delayed)]
        except Exception:
            pass

    # Lead sources, market, priority
    lead_source_qs = lead_kpi_qs.values("source").annotate(c=Count("id")).order_by("-c")
    lead_source_labels, lead_source_values = _top_buckets(lead_source_qs, "source", limit=6)

    lead_priority_map = {row.get("priority") or "Unknown": int(row.get("c") or 0) for row in lead_kpi_qs.values("priority").annotate(c=Count("id"))}
    lead_priority_labels = []
    lead_priority_values = []
    for p, _ in PRIORITY_CHOICES:
        lead_priority_labels.append(p)
        lead_priority_values.append(int(lead_priority_map.get(p, 0)))
    for p, cnt in lead_priority_map.items():
        if p not in lead_priority_labels:
            lead_priority_labels.append(p)
            lead_priority_values.append(int(cnt))

    lead_market_map = {row.get("market") or "Unknown": int(row.get("c") or 0) for row in lead_kpi_qs.values("market").annotate(c=Count("id"))}
    lead_market_labels = []
    lead_market_values = []
    for m, _ in Lead.MARKET_CHOICES:
        lead_market_labels.append(m)
        lead_market_values.append(int(lead_market_map.get(m, 0)))
    for m, cnt in lead_market_map.items():
        if m not in lead_market_labels:
            lead_market_labels.append(m)
            lead_market_values.append(int(cnt))

    open_opportunity_qs = open_pipeline_queryset(opportunity_kpi_qs)
    open_opps = open_opportunity_qs.count()
    open_pipeline_values = _sum_opportunity_kpi_values_by_currency(open_opportunity_qs)
    overdue_followups = lead_kpi_qs.filter(next_followup__lt=today).exclude(
        lead_status__in=["Converted", "Closed", "Disqualified", "Lost"]
    ).count()
    due_soon_followups = lead_kpi_qs.filter(
        next_followup__gte=today,
        next_followup__lte=today + timedelta(days=7),
    ).exclude(lead_status__in=["Converted", "Closed", "Disqualified", "Lost"]).count()
    conversion_rate = 0.0
    if leads_period > 0:
        conversion_rate = round((opp_period / leads_period) * 100, 1)
    prev_conversion_rate = 0.0
    if prev_leads_period > 0:
        prev_conversion_rate = round((prev_opp_period / prev_leads_period) * 100, 1)

    fit_buckets = lead_kpi_qs.aggregate(
        q1=Count("id", filter=Q(brand_fit_score__lte=24)),
        q2=Count("id", filter=Q(brand_fit_score__gte=25, brand_fit_score__lte=49)),
        q3=Count("id", filter=Q(brand_fit_score__gte=50, brand_fit_score__lte=74)),
        q4=Count("id", filter=Q(brand_fit_score__gte=75)),
    )
    lead_fit_labels = ["0-24", "25-49", "50-74", "75-100"]
    lead_fit_values = [
        int(fit_buckets.get("q1") or 0),
        int(fit_buckets.get("q2") or 0),
        int(fit_buckets.get("q3") or 0),
        int(fit_buckets.get("q4") or 0),
    ]

    lead_source_total = sum(lead_source_values)
    lead_source_breakdown = []
    for label, count in zip(lead_source_labels, lead_source_values):
        share = 0
        if lead_source_total:
            share = int(round((count / lead_source_total) * 100))
        lead_source_breakdown.append(
            {
                "label": label,
                "count": int(count),
                "share": share,
            }
        )

    top_niches = []
    niche_rows = []
    niche_base = opportunity_reporting_qs.filter(
        stage__iexact="Closed Won",
        **opp_period_filter,
    )
    if not opp_period:
        niche_base = opportunity_reporting_qs.filter(**opp_period_filter)
    if not niche_base.exists():
        niche_base = opportunity_kpi_qs
    niche_rows = list(
        niche_base.values("product_type").annotate(c=Count("id")).order_by("-c")
    )
    niche_total = sum(int(row.get("c") or 0) for row in niche_rows)
    for row in niche_rows[:4]:
        count = int(row.get("c") or 0)
        share = int(round((count / niche_total) * 100)) if niche_total else 0
        top_niches.append(
            {
                "label": (row.get("product_type") or "Other").strip() or "Other",
                "count": count,
                "share": share,
            }
        )

    production_running_count = 0
    production_hold_count = 0
    ship_pending_total = int(ship_pending[0] if ship_pending else 0)
    ship_delayed_total = int(ship_delayed[0] if ship_delayed else 0)
    factory_workload = [
        {"label": "Bangladesh", "orders": 0, "units": 0, "load_pct": 0},
        {"label": "Canada", "orders": 0, "units": 0, "load_pct": 0},
    ]
    if ProductionOrder is not None:
        try:
            running_operational_statuses = {
                "sample_development",
                "fabric_sourcing",
                "cutting",
                "printing",
                "sewing",
                "qc",
                "packing",
            }
            active_workload_rows = [
                row for row in production_operational_rows
                if row["operational_status"] in OPERATIONAL_ACTIVE_STATUSES
            ]
            production_running_count = len([
                row for row in production_operational_rows
                if row["operational_status"] in running_operational_statuses
            ])
            production_hold_count = 0
            workload_map = {}
            for row in active_workload_rows:
                order = row["order"]
                code = (order.factory_location or "").lower()
                workload_map.setdefault(code, {"orders": 0, "units": 0})
                workload_map[code]["orders"] += 1
                workload_map[code]["units"] += int(order.qty_total or 0)
            max_orders = max([row["orders"] for row in workload_map.values()] or [0])
            factory_workload = []
            for code, label in (("bd", "Bangladesh"), ("ca", "Canada")):
                orders = int(workload_map.get(code, {}).get("orders", 0))
                units = int(workload_map.get(code, {}).get("units", 0))
                load_pct = int(round((orders / max_orders) * 100)) if max_orders else 0
                factory_workload.append(
                    {
                        "label": label,
                        "orders": orders,
                        "units": units,
                        "load_pct": load_pct,
                    }
                )
        except Exception:
            pass

    pending_invoice_approvals = 0
    outstanding_invoices_total = Decimal("0")
    outstanding_invoice_values = []
    overdue_invoices_count = 0
    unpaid_invoices_count = 0
    partial_payments_count = 0
    outstanding_invoices = []
    draft_invoices = []
    invoice_status_labels = ["Draft", "Sent", "Partial", "Paid"]
    invoice_status_values = [0, 0, 0, 0]
    if Invoice is not None:
        try:
            invoice_counts = Invoice.objects.filter(is_archived=False).aggregate(
                draft=Count("id", filter=Q(status="draft")),
                sent=Count("id", filter=Q(status="sent")),
                partial=Count("id", filter=Q(status="partial")),
                paid=Count("id", filter=Q(status="paid")),
            )
            invoice_status_values = [
                int(invoice_counts.get("draft") or 0),
                int(invoice_counts.get("sent") or 0),
                int(invoice_counts.get("partial") or 0),
                int(invoice_counts.get("paid") or 0),
            ]
            open_invoice_base = Invoice.objects.filter(is_archived=False).exclude(status__in=["paid", "cancelled"])
            outstanding_totals = defaultdict(lambda: {"amount": Decimal("0")})
            for invoice in open_invoice_base.only("total_amount", "paid_amount", "currency"):
                code = (invoice.currency or "CAD").upper().strip()
                outstanding_totals[code]["amount"] += _ceo_decimal(invoice.balance)
            outstanding_invoice_values = currency_summary_rows(outstanding_totals)
            if len(outstanding_invoice_values) == 1:
                outstanding_invoices_total = outstanding_invoice_values[0]["amount"]
            overdue_invoices_count = open_invoice_base.filter(due_date__lt=today).count()
            unpaid_invoices_count = open_invoice_base.filter(paid_amount__lte=0).count()
            partial_payments_count = Invoice.objects.filter(is_archived=False, status="partial").count()
            pending_invoice_approvals = Invoice.objects.filter(is_archived=False, invoice_status="DRAFT").count()
            outstanding_invoices = list(
                open_invoice_base.select_related("customer", "order").order_by("due_date", "-issue_date")[:5]
            )
            draft_invoices = list(
                Invoice.objects.select_related("customer", "order")
                .filter(is_archived=False, invoice_status="DRAFT")
                .order_by("-created_at")[:4]
            )
        except Exception:
            pass

    new_leads_count = lead_kpi_qs.filter(lead_status="New").count()
    customer_count = Customer.objects.count()
    pending_quotations_count = CostingHeader.objects.filter(status="approved").filter(
        Q(quotation_number="") | Q(quotation_number__isnull=True)
    ).count()

    active_production_count = 0
    delayed_production_count = 0
    ready_to_ship_count = 0
    awaiting_approval_samples_count = 0
    sampling_production_count = 0
    sampling_production_units_count = 0
    bulk_production_count = 0
    bulk_production_units_count = 0
    active_production_units_count = 0
    completed_production_count = 0
    production_completion_percent = 0
    shipped_this_month_count = 0
    if ProductionOrder is not None:
        try:
            active_production_rows = [
                row for row in production_operational_rows
                if row["operational_status"] in OPERATIONAL_ACTIVE_STATUSES
            ]
            completed_production_rows = [
                row for row in production_operational_rows
                if row["operational_status"] == OPERATIONAL_STATUS_SHIPPED
            ]
            active_production_count = len(active_production_rows)
            active_production_units_count = sum((row["order"].qty_total or 0) for row in active_production_rows)
            delayed_production_count = len([
                row for row in production_operational_rows
                if row["order"].bulk_deadline
                and row["order"].bulk_deadline < today
                and row["operational_status"] not in OPERATIONAL_FINISHED_STATUSES
            ])
            sampling_production_rows = [
                row for row in active_production_rows
                if row["order"].production_order_type == "sampling"
            ]
            bulk_production_rows = [
                row for row in active_production_rows
                if row["order"].production_order_type == "bulk"
            ]
            sampling_production_count = len(sampling_production_rows)
            sampling_production_units_count = sum((row["order"].qty_total or 0) for row in sampling_production_rows)
            bulk_production_count = len(bulk_production_rows)
            bulk_production_units_count = sum((row["order"].qty_total or 0) for row in bulk_production_rows)
            completed_production_count = len(completed_production_rows)
            production_completion_denominator = active_production_count + completed_production_count
            production_completion_percent = (
                round((completed_production_count / production_completion_denominator) * 100)
                if production_completion_denominator
                else 0
            )
            ready_to_ship_count = production_operational_counts.get(OPERATIONAL_STATUS_READY_TO_SHIP, 0)
            awaiting_approval_samples_count = production_operational_counts.get(OPERATIONAL_STATUS_SAMPLE_SENT, 0)
        except Exception:
            active_production_count = 0
            delayed_production_count = 0
            ready_to_ship_count = 0
            awaiting_approval_samples_count = 0
            sampling_production_count = 0
            sampling_production_units_count = 0
            bulk_production_count = 0
            bulk_production_units_count = 0
            active_production_units_count = 0
            completed_production_count = 0
            production_completion_percent = 0
    if Shipment is not None:
        try:
            shipped_this_month_count = Shipment.objects.filter(
                ship_date__gte=current_month_start,
                status__in=["shipped", "out_for_delivery", "delivered"],
            ).count()
        except Exception:
            shipped_this_month_count = 0

    lifecycle_workflow_summary = {
        "active_order_lifecycles": 0,
        "waiting_for_payment": 0,
        "in_production": 0,
        "shipping": 0,
    }
    if not can_view_order_lifecycle_profit:
        try:
            active_lifecycles = OrderLifecycle.objects.exclude(status__in=["completed", "cancelled"])
            lifecycle_workflow_summary = {
                "active_order_lifecycles": active_lifecycles.count(),
                "waiting_for_payment": active_lifecycles.filter(invoice__total_amount__gt=F("invoice__paid_amount")).count(),
                "in_production": active_lifecycles.filter(status="production").count(),
                "shipping": active_lifecycles.filter(status="shipping").count(),
            }
        except Exception:
            logger.exception("main_dashboard: failed to build public lifecycle workflow counts")

    dashboard_notification_items = [
        {
            "label": "Overdue invoices",
            "count": overdue_invoices_count,
            "detail": "Invoices past due date and not fully paid.",
            "tone": "warn" if overdue_invoices_count else "good",
            "href": "#finance-section",
        },
        {
            "label": "Production delays",
            "count": delayed_production_count,
            "detail": "Active production orders with missed bulk deadlines.",
            "tone": "warn" if delayed_production_count else "good",
            "href": "#operations-section",
        },
        {
            "label": "Shipment updates",
            "count": ship_delayed_total,
            "detail": "Shipments past ship date and not delivered.",
            "tone": "warn" if ship_delayed_total else "good",
            "href": "#operations-section",
        },
        {
            "label": "Unpaid balances",
            "count": unpaid_invoices_count,
            "detail": "Open invoices with no recorded payment yet.",
            "tone": "warn" if unpaid_invoices_count else "good",
            "href": "#finance-section",
        },
        {
            "label": "Pending approvals",
            "count": pending_invoice_approvals + pending_quotations_count,
            "detail": "Draft invoices and approved costings waiting to move forward.",
            "tone": "flat" if pending_invoice_approvals or pending_quotations_count else "good",
            "href": "#workflow-section",
        },
    ]
    automation_context = automation_dashboard_context(request.user, sync=False)
    operations_context = operations_dashboard_context(request.user, today=today)
    if automation_context.get("automation_notification_cards"):
        dashboard_notification_items = automation_context["automation_notification_cards"]

    recent_leads = list(
        Lead.objects
        .only(
            "lead_id",
            "account_brand",
            "source",
            "brand_fit_score",
            "lead_status",
            "created_date",
            "next_followup",
        )
        .order_by("-created_date", "-id")[:5]
    )

    recent_production_updates = []
    if ProductionOrder is not None:
        try:
            recent_production_updates = list(
                ProductionOrder.objects
                .only(
                    "order_code",
                    "title",
                    "status",
                    "updated_at",
                    "qty_total",
                    "factory_location",
                )
                .order_by("-updated_at")[:5]
            )
        except Exception:
            recent_production_updates = []

    recent_client_activity = []
    if CustomerEvent is not None:
        try:
            recent_client_activity = list(
                CustomerEvent.objects.select_related("customer", "opportunity", "production")
                .only(
                    "title",
                    "details",
                    "event_type",
                    "created_at",
                    "customer__account_brand",
                    "customer__contact_name",
                    "opportunity__opportunity_id",
                    "production__order_code",
                )
                .order_by("-created_at")[:5]
            )
        except Exception:
            recent_client_activity = []

    revenue_delta_pct = _delta_pct(revenue_cad_period, previous_window["revenue"])
    conversion_delta_pct = conversion_rate - prev_conversion_rate
    monthly_profit_delta_pct = _delta_pct(monthly_profit_cad, previous_month_profit_cad)

    production_business = summarize_production_business_models()
    local_sewing_summary = production_business["local_sewing"]
    canada_export_revenue_rows = production_business["canada_export_revenue_rows"]
    primary_kpis = [
        {
            "title": "Accounting Revenue",
            "value": format_compact_finance_money(revenue_cad_period, "CAD"),
            "note": f"{period_label} CAD equivalent revenue excluding transfers.",
            "trend_text": f"{revenue_delta_pct:+.0f}% vs prior window",
            "trend_tone": _delta_tone(revenue_delta_pct),
            "accent": "revenue",
            "icon": "wallet",
            "href": "#finance-section",
        },
        {
            "title": "Open Pipeline",
            "value": _format_count(open_opps),
            "note": f"Open pipeline {_format_currency_summary(open_pipeline_values)}.",
            "trend_text": f"{_format_count(opp_period)} added in {period_label.lower()}",
            "trend_tone": "up" if opp_period else "flat",
            "accent": "pipeline",
            "icon": "briefcase-business",
            "href": "#sales-section",
        },
        {
            "title": "Lead Conversion Rate",
            "value": _format_percent(conversion_rate),
            "note": f"{_format_count(opp_period)} opportunities from {_format_count(leads_period)} leads.",
            "trend_text": f"{conversion_delta_pct:+.1f} pts vs prior window",
            "trend_tone": _delta_tone(conversion_delta_pct),
            "accent": "conversion",
            "icon": "activity",
            "href": "#lead-intelligence-section",
        },
        {
            "title": "Production Running",
            "value": _format_count(production_running_count),
            "note": f"{_format_count(production_hold_count)} order(s) currently on hold.",
            "trend_text": f"{_format_count(orders_processed_period)} processed in {period_label.lower()}",
            "trend_tone": "up" if orders_processed_period else "flat",
            "accent": "operations",
            "icon": "factory",
            "href": "#operations-section",
        },
        {
            "title": "Monthly Profit",
            "value": format_compact_finance_money(monthly_profit_cad, "CAD"),
            "note": f"{today.strftime('%B')} CAD equivalent net cash after expenses.",
            "trend_text": f"{monthly_profit_delta_pct:+.0f}% vs last month",
            "trend_tone": _delta_tone(monthly_profit_delta_pct),
            "accent": "profit",
            "icon": "badge-dollar-sign",
            "href": "#finance-section",
        },
        {
            "title": "Pending Follow Ups",
            "value": _format_count(overdue_followups),
            "note": f"{_format_count(due_soon_followups)} due in the next 7 days.",
            "trend_text": "Needs action" if overdue_followups else "In control",
            "trend_tone": "down" if overdue_followups else "up",
            "accent": "followup",
            "icon": "bell-ring",
            "href": "#activity-section",
        },
    ]
    if can_view_local_sewing_financials(request.user):
        primary_kpis.extend(
            [
                {"title": "Canada Export Revenue", "value": _format_currency_summary(canada_export_revenue_rows), "note": "FOB and Canada door-to-door approved order value; currencies are not combined.", "trend_text": "Native currencies", "trend_tone": "flat", "accent": "revenue", "icon": "plane"},
                {"title": "Bangladesh Sewing Revenue", "value": format_compact_finance_money(local_sewing_summary["total_sewing_revenue"], "BDT"), "note": "Sewing-only order value in native BDT.", "trend_text": f"{local_sewing_summary['order_count']:,} local order(s)", "trend_tone": "flat", "accent": "revenue", "icon": "shirt"},
                {"title": "Bangladesh Sewing Cost", "value": format_compact_finance_money(local_sewing_summary["total_sewing_cost"], "BDT") if local_sewing_summary["cost_available"] else "Cost unavailable", "note": "Positive recorded sewing cost plus extra local cost.", "trend_text": f"{local_sewing_summary['costed_order_count']:,} costed order(s)", "trend_tone": "flat", "accent": "neutral", "icon": "calculator"},
                {"title": "Bangladesh Sewing Profit", "value": format_compact_finance_money(local_sewing_summary["profit"], "BDT") if local_sewing_summary["profit"] is not None else "N/A", "note": "Costed local sewing revenue less cost.", "trend_text": f"{local_sewing_summary['margin']:.2f}% margin" if local_sewing_summary["margin"] is not None else "Margin N/A", "trend_tone": "up" if local_sewing_summary["profit"] is not None and local_sewing_summary["profit"] >= 0 else "flat", "accent": "profit", "icon": "badge-dollar-sign"},
                {"title": "Bangladesh Sewing Margin", "value": f"{local_sewing_summary['margin']:.2f}%" if local_sewing_summary["margin"] is not None else "Margin N/A", "note": "Calculated only when positive sewing cost exists.", "trend_text": "Cost-aware margin", "trend_tone": "flat", "accent": "profit", "icon": "percent"},
                {"title": "Bangladesh Local Orders", "value": f"{local_sewing_summary['order_count']:,}", "note": "Bangladesh sewing-charge production orders.", "trend_text": f"{local_sewing_summary['in_progress_count']:,} in progress", "trend_tone": "flat", "accent": "pipeline", "icon": "list-checks"},
                {"title": "Sewing Orders In Progress", "value": f"{local_sewing_summary['in_progress_count']:,}", "note": "Open Bangladesh local sewing orders.", "trend_text": "Current production", "trend_tone": "flat", "accent": "pipeline", "icon": "activity"},
                {"title": "Sewing Orders Completed", "value": f"{local_sewing_summary['completed_count']:,}", "note": "Completed Bangladesh local sewing orders.", "trend_text": "Production status", "trend_tone": "up", "accent": "pipeline", "icon": "circle-check"},
                {"title": "Approved Bangladesh Sewing", "value": f"{local_sewing_summary['approved_count']:,}", "note": "CEO-approved CMT Quick Costing.", "trend_text": "Approval workflow", "trend_tone": "up", "accent": "pipeline", "icon": "circle-check"},
                {"title": "Pending CEO Approval", "value": f"{local_sewing_summary['pending_approval_count']:,}", "note": "Bangladesh sewing costings awaiting CEO decision.", "trend_text": "Approval workflow", "trend_tone": "flat", "accent": "pipeline", "icon": "clock"},
                {"title": "Rejected", "value": f"{local_sewing_summary['rejected_count']:,}", "note": "Rejected Bangladesh sewing costings.", "trend_text": "Approval workflow", "trend_tone": "flat", "accent": "pipeline", "icon": "circle-x"},
            ]
        )
    if not can_view_order_lifecycle_profit:
        primary_kpis = [card for card in primary_kpis if card.get("title") != "Monthly Profit"]

    finance_summary_cards = [
        {
            "title": "Net Cash",
            "value": format_compact_finance_money(net_cash_cad_period, "CAD"),
            "note": f"{period_label} CAD equivalent revenue minus expenses.",
            "trend_text": f"{gross_margin_pct_period:.1f}% gross margin",
            "trend_tone": "up" if net_cash_cad_period >= 0 else "down",
            "accent": "neutral",
            "icon": "landmark",
        },
        {
            "title": "Production Profit",
            "value": (
                format_compact_finance_money(prod_profit_cad_period, "CAD")
                if prod_cost_available
                else "Cost unavailable"
            ),
            "note": (
                "CAD equivalent production-linked accounting revenue less production costs."
                if prod_cost_available
                else "Production revenue exists, but no positive production cost is recorded."
            ),
            "trend_text": f"{prod_margin_pct_period:.1f}% margin" if prod_cost_available else "Margin N/A",
            "trend_tone": "up" if prod_cost_available and prod_profit_cad_period >= 0 else "flat",
            "accent": "neutral",
            "icon": "sparkles",
        },
        {
            "title": "Outstanding Invoices",
            "value": _format_currency_summary(outstanding_invoice_values),
            "note": f"{_format_count(overdue_invoices_count)} invoice(s) overdue; currencies are not combined.",
            "trend_text": f"{_format_count(pending_invoice_approvals)} pending approval",
            "trend_tone": "down" if overdue_invoices_count else "flat",
            "accent": "neutral",
            "icon": "receipt-text",
        },
        {
            "title": "Orders Processed",
            "value": _format_count(orders_processed_period),
            "note": f"{_format_count(orders_created_period)} created in {period_label.lower()}.",
            "trend_text": f"{format_finance_money(production_cost_bdt_period, 'BDT')} production cost",
            "trend_tone": "flat",
            "accent": "neutral",
            "icon": "package-check",
        },
    ]
    if not can_view_order_lifecycle_profit:
        finance_summary_cards = [
            card
            for card in finance_summary_cards
            if card.get("title") == "Outstanding Invoices"
        ]

    payroll_summary_cards = [
        {
            "title": "Payroll Total",
            "value": format_finance_money(payroll_total, "BDT"),
            "note": f"{payroll_paid} paid | {payroll_unpaid} unpaid",
            "trend_text": f"OT {format_finance_money(payroll_ot, 'BDT')}",
            "trend_tone": "flat",
            "accent": "neutral",
            "icon": "users",
        },
        {
            "title": "Bonus",
            "value": format_finance_money(payroll_bonus, "BDT"),
            "note": "Current payroll cycle bonus total.",
            "trend_text": f"Deduction {format_finance_money(payroll_deduction, 'BDT')}",
            "trend_tone": "flat",
            "accent": "neutral",
            "icon": "gift",
        },
    ]

    dashboard_alerts = []
    if overdue_followups:
        dashboard_alerts.append(
            {
                "title": f"{overdue_followups} overdue follow-up(s)",
                "detail": "Leads with follow-up dates in the past.",
                "tone": "down",
            }
        )
    if pending_invoice_approvals:
        dashboard_alerts.append(
            {
                "title": f"{pending_invoice_approvals} pending invoice approval(s)",
                "detail": "Draft invoices waiting for approval review.",
                "tone": "flat",
            }
        )
    if ship_delayed_total:
        dashboard_alerts.append(
            {
                "title": f"{ship_delayed_total} delayed shipment(s)",
                "detail": "Shipments past ship date and not yet delivered.",
                "tone": "down",
            }
        )
    if production_hold_count:
        dashboard_alerts.append(
            {
                "title": f"{production_hold_count} order(s) on hold",
                "detail": "Production orders blocked or paused.",
                "tone": "down",
            }
        )
    if not dashboard_alerts:
        dashboard_alerts.append(
            {
                "title": "No urgent blockers",
                "detail": "Dashboard alerts are currently clear.",
                "tone": "up",
            }
        )

    action_recommendations = [
        {
            "title": f"Prioritize {overdue_followups} overdue follow-up(s)",
            "detail": "Work the oldest leads first to protect pipeline conversion.",
        },
        {
            "title": f"Review {pending_invoice_approvals} draft approval(s)",
            "detail": "Clear invoice approvals before month-end billing slips.",
        },
        {
            "title": f"Watch {ship_delayed_total} delayed shipment(s)",
            "detail": "Check shipment notes and update client communication where needed.",
        },
        {
            "title": f"Lean into {lead_source_labels[0] if lead_source_labels else 'top lead source'}",
            "detail": "Highest-volume source remains the strongest acquisition channel right now.",
        },
    ]

    ai_notes = [
        f"Lead -> Opportunity conversion: {conversion_rate}%",
        f"Open opportunities: {open_opps}",
        f"Overdue follow-ups: {overdue_followups}",
        f"Top lead source: {lead_source_labels[0] if lead_source_labels else 'N/A'}",
    ]

    chart_data = {
        "leads_labels": leads_daily_labels,
        "leads_values": leads_daily_values,
        "opp_daily_values": opp_daily_values,
        "cash_daily_values": cash_daily_values,
        "revenue_daily_values": revenue_daily_values,
        "expense_daily_values": expense_daily_values,
        "orders_created_values": orders_created_daily_values,
        "orders_processed_values": orders_processed_daily_values,
        "opp_stage_labels": opp_stage_labels,
        "opp_stage_values": opp_stage_values,
        "lead_status_labels": lead_status_labels,
        "lead_status_values": lead_status_values,
        "lead_source_labels": lead_source_labels,
        "lead_source_values": lead_source_values,
        "lead_fit_labels": lead_fit_labels,
        "lead_fit_values": lead_fit_values,
        "lead_priority_labels": lead_priority_labels,
        "lead_priority_values": lead_priority_values,
        "lead_market_labels": lead_market_labels,
        "lead_market_values": lead_market_values,
        "win_loss_labels": win_loss_labels,
        "win_loss_values": win_loss_values,
        "invoice_status_labels": invoice_status_labels,
        "invoice_status_values": invoice_status_values,
        "prod_labels": prod_labels,
        "prod_counts": prod_counts,
        "ship_labels": ship_labels,
        "ship_shipped": ship_shipped,
        "ship_pending": ship_pending,
        "ship_delayed": ship_delayed,
    }
    if can_view_order_lifecycle_profit:
        chart_data["monthly_profit_labels"] = monthly_profit_labels
        chart_data["monthly_profit_values"] = monthly_profit_values

    lifecycle_summary = None
    if can_view_order_lifecycle_profit:
        try:
            lifecycle_summary = lifecycle_dashboard_metrics()
            lifecycle_workflow_summary = {
                "active_order_lifecycles": lifecycle_summary["active_orders"],
                "waiting_for_payment": lifecycle_summary["orders_waiting_payment"],
                "in_production": lifecycle_summary["orders_in_production"],
                "shipping": OrderLifecycle.objects.filter(status="shipping").exclude(status__in=["completed", "cancelled"]).count(),
            }
        except Exception:
            logger.exception("main_dashboard: failed to build order lifecycle summary")
            lifecycle_summary = None

    ctx = {
        "today": today,
        "local_sewing_summary": local_sewing_summary,
        "canada_export_revenue_rows": canada_export_revenue_rows,
        "leads_today": leads_today,
        "leads_period": leads_period,

        "opp_period": opp_period,
        "open_opps": open_opps,
        "conversion_rate": conversion_rate,
        "overdue_followups": overdue_followups,

        "acc_income_cad_period": acc_income_cad,
        "acc_out_cad_period": acc_out_cad,
        "acc_net_cad_period": acc_net_cad,
        "acc_income_bdt_period": acc_income_bdt,
        "acc_out_bdt_period": acc_out_bdt,
        "acc_net_bdt_period": acc_net_bdt,
        "acc_ca_income_cad_period": acc_ca_income_cad,
        "acc_ca_out_cad_period": acc_ca_out_cad,
        "acc_ca_net_cad_period": acc_ca_net_cad,
        "acc_bd_income_bdt_period": acc_bd_income_bdt,
        "acc_bd_out_bdt_period": acc_bd_out_bdt,
        "acc_bd_net_bdt_period": acc_bd_net_bdt,
        "acc_entries_period": acc_entries_period,
        "acc_entries_ca_period": acc_entries_ca_period,
        "acc_entries_bd_period": acc_entries_bd_period,
        "acc_ca_entries_month": acc_ca_month["entries"],
        "acc_ca_entries_all": acc_ca_all["entries"],
        "acc_ca_income_cad_month": acc_ca_month["income"],
        "acc_ca_out_cad_month": acc_ca_month["out"],
        "acc_ca_net_cad_month": acc_ca_month["net"],
        "acc_ca_income_cad_all": acc_ca_all["income"],
        "acc_ca_out_cad_all": acc_ca_all["out"],
        "acc_ca_net_cad_all": acc_ca_all["net"],
        "acc_bd_entries_month": acc_bd_month["entries"],
        "acc_bd_entries_all": acc_bd_all["entries"],
        "acc_bd_income_bdt_month": acc_bd_month["income"],
        "acc_bd_out_bdt_month": acc_bd_month["out"],
        "acc_bd_net_bdt_month": acc_bd_month["net"],
        "acc_bd_income_bdt_all": acc_bd_all["income"],
        "acc_bd_out_bdt_all": acc_bd_all["out"],
        "acc_bd_net_bdt_all": acc_bd_all["net"],
        "revenue_cad_period": revenue_cad_period,
        "expense_cad_period": expense_cad_period,
        "net_cash_cad_period": net_cash_cad_period,
        "swing_cad_period": swing_cad_period,
        "swing_bdt_period": swing_bdt_period,
        "gross_margin_pct_period": gross_margin_pct_period if can_view_order_lifecycle_profit else None,
        "orders_created_period": orders_created_period,
        "orders_processed_period": orders_processed_period,
        "production_cost_bdt_period": production_cost_bdt_period if can_view_order_lifecycle_profit else None,
        "prod_revenue_cad_period": prod_revenue_cad_period,
        "prod_cost_cad_period": prod_cost_cad_period if can_view_order_lifecycle_profit else None,
        "prod_cost_available": prod_cost_available if can_view_order_lifecycle_profit else False,
        "prod_profit_cad_period": prod_profit_cad_period if can_view_order_lifecycle_profit else None,
        "prod_margin_pct_period": prod_margin_pct_period if can_view_order_lifecycle_profit else None,

        "payroll_total": payroll_total,
        "payroll_ot": payroll_ot,
        "payroll_bonus": payroll_bonus,
        "payroll_deduction": payroll_deduction,
        "payroll_paid": payroll_paid,
        "payroll_unpaid": payroll_unpaid,
        "payroll_year": payroll_year,
        "payroll_month": payroll_month,
        "period_days": period_days,
        "period_label": period_label,
        "filter_values": filter_values,
        "monthly_profit_cad": monthly_profit_cad if can_view_order_lifecycle_profit else None,
        "production_running_count": production_running_count,
        "production_hold_count": production_hold_count,
        "active_production_count": active_production_count,
        "active_production_units_count": active_production_units_count,
        "completed_production_count": completed_production_count,
        "production_completion_percent": production_completion_percent,
        "delayed_production_count": delayed_production_count,
        "ready_to_ship_count": ready_to_ship_count,
        "awaiting_approval_samples_count": awaiting_approval_samples_count,
        "sampling_production_count": sampling_production_count,
        "sampling_production_units_count": sampling_production_units_count,
        "bulk_production_count": bulk_production_count,
        "bulk_production_units_count": bulk_production_units_count,
        "ship_pending_total": ship_pending_total,
        "ship_delayed_total": ship_delayed_total,
        "shipped_this_month_count": shipped_this_month_count,
        "new_leads_count": new_leads_count,
        "customer_count": customer_count,
        "pending_quotations_count": pending_quotations_count,
        "pending_invoice_approvals": pending_invoice_approvals,
        "outstanding_invoices_total": outstanding_invoices_total,
        "outstanding_invoice_values": outstanding_invoice_values,
        "overdue_invoices_count": overdue_invoices_count,
        "unpaid_invoices_count": unpaid_invoices_count,
        "partial_payments_count": partial_payments_count,
        "lifecycle_workflow_summary": lifecycle_workflow_summary,
        "dashboard_notification_items": dashboard_notification_items,
        "lead_source_breakdown": lead_source_breakdown,
        "top_niches": top_niches,
        "factory_workload": factory_workload,
        "outstanding_invoices": outstanding_invoices,
        "draft_invoices": draft_invoices,
        "recent_leads": recent_leads,
        "recent_production_updates": recent_production_updates,
        "recent_client_activity": recent_client_activity,
        "primary_kpis": primary_kpis,
        "finance_summary_cards": finance_summary_cards,
        "payroll_summary_cards": payroll_summary_cards,
        "dashboard_alerts": dashboard_alerts,
        "dashboard_alerts_count": automation_context.get("automation_unread_count") or len([a for a in dashboard_alerts if a.get("tone") != "up"]),
        "action_recommendations": action_recommendations,
        "ai_notes": ai_notes,
        "chart_data": chart_data,
        "can_view_order_lifecycle_profit": can_view_order_lifecycle_profit,
        "lifecycle_summary": lifecycle_summary,
        **automation_context,
        **operations_context,
        **dashboard_personalization(request.user),
    }

    return render(request, "crm/main_dashboard.html", ctx)

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect

from .models import Lead, Opportunity


def convert_lead_to_opportunity(request, pk):
    lead = get_object_or_404(scope_owned_sales_leads(Lead.objects.all(), request.user), pk=pk)

    if request.method == "POST":
        customer = lead.customer if lead.customer_id else _find_or_create_customer_for_lead(lead)
        if not lead.customer_id and customer:
            lead.customer = customer
            lead.save(update_fields=["customer"])

        opp = Opportunity.objects.create(
            lead=lead,
            customer=customer,
            assigned_to=lead.assigned_to if lead.assigned_to_id else None,
            stage="Prospecting",
            product_category="Other",
            product_type="Other",
            converted_from_lead_type=getattr(lead, "lead_type", ""),
            converted_from_source_channel=getattr(lead, "source_channel", ""),
            converted_from_outbound_status=getattr(lead, "outbound_status", ""),
        )
        link_reference_images_to_opportunity(lead, opp)
        lead.lead_status = "Converted"
        update_fields = ["lead_status"]
        if lead.lead_type == "outbound":
            lead.outbound_status = "Converted to Opportunity"
            update_fields.append("outbound_status")
        lead.save(update_fields=update_fields)
        messages.success(request, "Lead converted to opportunity.")

        _record_customer_event(
            customer=customer,
            event_type="opportunity_created",
            title="Opportunity created",
            details=f"Opportunity {opp.opportunity_id} created from lead.",
            opportunity=opp,
        )

        return redirect("opportunity_detail", pk=opp.pk)

    return redirect("lead_detail", pk=pk)


def add_opportunity(request):
    customers = Customer.objects.filter(is_archived=False).order_by("account_brand")
    leads = scope_owned_sales_leads(
        Lead.objects.all().order_by("-created_date"),
        request.user,
    )
    salesperson_options = _active_crm_user_options()
    selected_customer = None
    selected_lead = None
    selected_customer_id = request.GET.get("customer") or ""
    selected_lead_id = request.GET.get("lead") or ""
    if selected_customer_id:
        latest_customer_lead = (
            Lead.objects.filter(customer=OuterRef("pk"))
            .order_by("-created_date", "-id")
        )
        latest_customer_note = (
            CustomerNote.objects.filter(customer=OuterRef("pk"))
            .order_by("-created_at")
        )
        selected_customer = (
            Customer.objects
            .filter(pk=selected_customer_id, is_archived=False)
            .annotate(
                context_latest_lead_source=Subquery(latest_customer_lead.values("source")[:1]),
                context_latest_lead_source_channel=Subquery(latest_customer_lead.values("source_channel")[:1]),
                context_latest_lead_first_touch_channel=Subquery(latest_customer_lead.values("first_touch_channel")[:1]),
                context_latest_lead_product_interest=Subquery(latest_customer_lead.values("product_interest")[:1]),
                context_latest_lead_primary_product_type=Subquery(latest_customer_lead.values("primary_product_type")[:1]),
                context_latest_lead_product_category=Subquery(latest_customer_lead.values("product_category")[:1]),
                context_latest_lead_assigned_to_id=Subquery(latest_customer_lead.values("assigned_to_id")[:1]),
                context_latest_customer_note=Subquery(latest_customer_note.values("content")[:1]),
            )
            .first()
        )
    if selected_lead_id:
        selected_lead = leads.filter(pk=selected_lead_id).select_related("customer", "assigned_to").first()
    if selected_lead and not selected_customer and selected_lead.customer_id:
        selected_customer = selected_lead.customer
        selected_customer_id = str(selected_customer.pk)
    customer_context = _customer_opportunity_context(
        selected_customer,
        salesperson_options=salesperson_options,
    )
    customer_prefill = customer_context["prefill"]
    selected_product_type = customer_prefill["product_type"]
    selected_product_category = customer_prefill["product_category"]
    selected_notes = customer_prefill["notes"]
    can_edit_historical_dates_flag = can_edit_historical_dates(request.user)
    default_salesperson = None
    if selected_lead and selected_lead.assigned_to_id:
        default_salesperson = selected_lead.assigned_to
    elif selected_customer:
        default_salesperson = customer_context["default_salesperson"]
    active_customer_opportunities = customer_context["active_opportunities"] if selected_customer else []

    if request.method == "POST":
        customer_id = request.POST.get("customer")
        lead_id = request.POST.get("lead")
        assigned_to_id = request.POST.get("assigned_to")

        customer = None
        lead = None

        if lead_id:
            lead = leads.filter(pk=lead_id).select_related("customer", "assigned_to").first()

        if customer_id:
            customer = Customer.objects.filter(pk=customer_id, is_archived=False).first()

        if not lead and not customer:
            messages.error(request, "Please select a lead or a customer.")
            return redirect("add_opportunity")

        if lead:
            if lead.customer_id:
                customer = lead.customer
            elif customer:
                lead.customer = customer
                lead.save(update_fields=["customer"])
            else:
                customer = _find_or_create_customer_for_lead(lead)
                lead.customer = customer
                lead.save(update_fields=["customer"])

        assigned_to = None
        if assigned_to_id:
            assigned_to = salesperson_options.filter(pk=assigned_to_id).first()
        if not assigned_to and lead and lead.assigned_to_id:
            assigned_to = lead.assigned_to
        if not assigned_to and customer:
            assigned_to = _customer_default_salesperson(customer)

        stage = request.POST.get("stage") or "Prospecting"
        product_type = request.POST.get("product_type") or "Other"
        product_category = request.POST.get("product_category") or "Other"
        order_currency = (request.POST.get("order_currency") or "CAD").upper()
        if order_currency not in {"CAD", "USD", "BDT"}:
            order_currency = "CAD"
        moq_units_raw = request.POST.get("moq_units")
        order_value_raw = request.POST.get("order_value")
        order_value_usd_raw = request.POST.get("order_value_usd")
        fx_rate_raw = request.POST.get("fx_rate_bdt_per_usd")
        opportunity_date = None
        if can_edit_historical_dates_flag:
            opportunity_date_raw = (request.POST.get("opportunity_date") or "").strip()
            if opportunity_date_raw:
                opportunity_date = parse_date(opportunity_date_raw)
                if opportunity_date is None:
                    messages.error(request, "Please enter a valid opportunity date.")
                    return redirect("add_opportunity")

        moq_units = None
        if moq_units_raw:
            try:
                moq_units = int(moq_units_raw)
            except ValueError:
                moq_units = None

        order_value = _safe_decimal_or_none(order_value_raw)
        order_value_usd = _safe_decimal_or_none(order_value_usd_raw)
        fx_rate = _safe_decimal_or_none(fx_rate_raw)
        if order_value_usd is not None:
            order_value = _calc_order_value_bdt(order_value_usd, fx_rate, order_currency)

        opp = Opportunity.objects.create(
            lead=lead,
            stage=stage,
            product_type=product_type,
            product_category=product_category,
            customer=customer,
            assigned_to=assigned_to,
            moq_units=moq_units,
            order_currency=order_currency,
            order_value=order_value,
            order_value_usd=order_value_usd,
            fx_rate_bdt_per_usd=fx_rate,
            opportunity_date=opportunity_date,
            notes=(request.POST.get("notes") or "").strip(),
        )
        if lead:
            lead.lead_status = "Converted"
            lead.save(update_fields=["lead_status"])

        messages.success(request, "Opportunity created.")

        _record_customer_event(
            customer=customer,
            event_type="opportunity_created",
            title="Opportunity created",
            details=f"Opportunity {opp.opportunity_id} created.",
            opportunity=opp,
        )

        return redirect("opportunity_detail", pk=opp.pk)

    context = {
        "customers": customers,
        "leads": leads,
        "stage_choices": Opportunity.STAGE_CHOICES,
        "type_choices": Opportunity.PRODUCT_TYPE_CHOICES,
        "category_choices": Opportunity.PRODUCT_CATEGORY_CHOICES,
        "currency_choices": Opportunity.ORDER_CURRENCY_CHOICES,
        "default_currency": "CAD",
        "salesperson_options": salesperson_options,
        "default_salesperson": default_salesperson,
        "default_salesperson_id": getattr(default_salesperson, "pk", ""),
        "selected_customer": selected_customer,
        "selected_customer_id": selected_customer_id,
        "selected_lead": selected_lead,
        "selected_lead_id": selected_lead_id,
        "active_customer_opportunities": active_customer_opportunities,
        "customer_prefill": customer_prefill,
        "customer_stats": customer_context["stats"],
        "previous_customer_opportunities": customer_context["previous_opportunities"],
        "selected_product_type": selected_product_type,
        "selected_product_category": selected_product_category,
        "selected_notes": selected_notes,
        "can_edit_historical_dates": can_edit_historical_dates_flag,
    }
    return render(request, "crm/add_opportunity.html", context)



@login_required
def library_home(request):
    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()

        if action == "upload_attachment":
            form = LibraryAttachmentForm(request.POST, request.FILES)
            if form.is_valid():
                attachment = form.save(commit=False)
                attachment.uploaded_by = request.user if request.user.is_authenticated else None
                attachment.save()
                messages.success(request, "Library document uploaded.")
            else:
                messages.error(request, "Please fill the required fields and choose a file.")
            return redirect("library_home")

        if action == "delete_attachment":
            attach_id = (request.POST.get("attachment_id") or "").strip()
            if attach_id:
                LibraryAttachment.objects.filter(pk=attach_id).delete()
                messages.success(request, "Library document removed.")
            return redirect("library_home")

    attachments = LibraryAttachment.objects.all().order_by("-uploaded_at", "-id")

    context = {
        "product_count": Product.objects.count(),
        "fabric_count": Fabric.objects.count(),
        "accessory_count": Accessory.objects.count(),
        "trim_count": Trim.objects.count(),
        "thread_count": ThreadOption.objects.count(),
        "attachments": attachments[:50],
        "attachment_form": LibraryAttachmentForm(),
    }
    return render(request, "crm/library_home.html", context)


from django.shortcuts import render
from django.core.paginator import Paginator
from django.db.models import Q
from django.utils.dateparse import parse_date
from .models import Opportunity

def opportunities_list(request):
    q = (request.GET.get("q") or "").strip()
    stage = (request.GET.get("stage") or "").strip()
    created_from_raw = (request.GET.get("created_from") or "").strip()
    created_to_raw = (request.GET.get("created_to") or "").strip()
    value_min_raw = (request.GET.get("value_min") or "").strip()
    value_max_raw = (request.GET.get("value_max") or "").strip()
    archive_filter = (request.GET.get("archive") or "active").strip().lower()
    status = (request.GET.get("status") or ("archived" if archive_filter == "archived" else "active")).strip().lower()
    if status == "archived":
        archive_filter = "archived"
    elif status == "all" and "archive" not in request.GET:
        archive_filter = "all"

    sort = (request.GET.get("sort") or "new").strip().lower()

    try:
        per_page = int(request.GET.get("per_page") or 50)
    except ValueError:
        per_page = 50

    if per_page not in (20, 50, 100):
        per_page = 50

    active_stages = _active_opportunity_stages()
    qs = Opportunity.objects.select_related("lead", "lead__assigned_to")
    qs = scope_sales_opportunities(qs, request.user)
    qs = _with_opportunity_production_flag(qs)
    qs = with_opportunity_reporting_date(qs)

    if archive_filter == "archived":
        qs = qs.filter(is_archived=True)
    elif archive_filter != "all":
        qs = qs.filter(is_archived=False)

    if q:
        qs = qs.filter(
            Q(opportunity_id__icontains=q)
            | Q(stage__icontains=q)
            | Q(product_type__icontains=q)
            | Q(product_category__icontains=q)
            | Q(lead__primary_product_type__icontains=q)
            | Q(lead__lead_id__icontains=q)
            | Q(lead__account_brand__icontains=q)
            | Q(lead__contact_name__icontains=q)
            | Q(lead__email__icontains=q)
        )

    if stage:
        qs = qs.filter(stage__iexact=stage)

    if status in {"active", "open", ""}:
        qs = _active_opportunity_list_queryset(qs)
    elif status == "moved_to_production":
        qs = qs.filter(Q(stage="Production") | Q(list_has_production=True)).distinct()
    elif status == "closed_won":
        qs = qs.filter(stage="Closed Won")
    elif status == "closed_lost":
        qs = qs.filter(stage="Closed Lost")
    elif status == "archived":
        qs = qs.filter(is_archived=True)
    elif status == "all":
        pass
    else:
        qs = _active_opportunity_list_queryset(qs)

    created_from = parse_date(created_from_raw) if created_from_raw else None
    created_to = parse_date(created_to_raw) if created_to_raw else None
    if created_from:
        qs = qs.filter(**{f"{OPPORTUNITY_REPORTING_DATE_ALIAS}__gte": created_from})
    if created_to:
        qs = qs.filter(**{f"{OPPORTUNITY_REPORTING_DATE_ALIAS}__lte": created_to})

    qs = _with_opportunity_kpi_value(qs)
    value_min = _parse_money_value(value_min_raw) if value_min_raw else None
    value_max = _parse_money_value(value_max_raw) if value_max_raw else None
    if value_min is not None:
        qs = qs.filter(kpi_order_value__gte=value_min)
    if value_max is not None:
        qs = qs.filter(kpi_order_value__lte=value_max)

    today = timezone.localdate()
    pipeline_values = _sum_opportunity_kpi_values_by_currency(_active_opportunity_list_queryset(qs))
    due_followups = qs.filter(next_followup__isnull=False, next_followup__lte=today).count()

    if sort == "old":
        qs = qs.order_by(OPPORTUNITY_REPORTING_DATE_ALIAS, "id")
    else:
        qs = qs.order_by(f"-{OPPORTUNITY_REPORTING_DATE_ALIAS}", "-id")

    paginator = Paginator(qs, per_page)
    page_number = request.GET.get("page") or 1
    page_obj = paginator.get_page(page_number)
    page_obj.object_list = attach_primary_reference_images_to_opportunities(page_obj.object_list)
    for opp in page_obj.object_list:
        try:
            opp.can_hard_delete = not _opportunity_linked_record_labels(opp)
        except Exception:
            opp.can_hard_delete = False

    context = {
        "page_obj": page_obj,
        "per_page": per_page,
        "stage_choices": Opportunity.STAGE_CHOICES,
        "pipeline_values": pipeline_values,
        "due_followups": due_followups,
        "visible_count": len(page_obj.object_list),
        "archive_filter": archive_filter,
        "status_filter": status,
        "can_archive_records": _can_archive_workflow_record(request.user),
    }
    return render(request, "crm/opportunities_list.html", context)
