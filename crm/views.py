# crm/views.py

import json
import logging
from collections import defaultdict
from datetime import timedelta, date
from decimal import Decimal
from django.db.models import Count, Sum, Q, Max
from django.db.models.functions import TruncDate
from django.db import models
from django.conf import settings
try:
    from openai import OpenAI
except Exception:
    OpenAI = None
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Case, Count, IntegerField, Q, When
from django.db.models.functions import TruncDate, TruncMonth, TruncYear
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from .models import Product, Fabric, Accessory, Trim, ThreadOption


from .forms import (
    BDStaffMonthForm,
    EventForm,
    LeadForm,
    ShipmentForm,
    ProductForm,
    FabricForm,
    AccessoryForm,
    TrimForm,
    ThreadForm,
)
from .models import (
    AIAgent,
    BDStaff,
    BDStaffMonth,
    Customer,
    CustomerEvent,
    CustomerNote,
    Lead,
    LeadActivity,
    LeadComment,
    Opportunity,
    OpportunityFile,
    OpportunityTask,
    Product,
    ProductionOrder,
    ProductionStage,
    Shipment,
)
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
    order = get_object_or_404(ProductionOrder, pk=pk)

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
        messages.success(request, "Stage started and date saved.")

    elif stage.status == "in_progress":
        stage.status = "done"
        if not stage.actual_start:
            stage.actual_start = today
        if not stage.actual_end:
            stage.actual_end = today
        stage.save(update_fields=["status", "actual_start", "actual_end"])
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

# ------------------------------------------
# Shipment email helper (non accounting)
# ------------------------------------------

def send_shipment_update_email(shipment, event_label):
    email_to = None

    if shipment.customer and shipment.customer.email:
        email_to = shipment.customer.email
    elif shipment.opportunity and shipment.opportunity.lead and shipment.opportunity.lead.email:
        email_to = shipment.opportunity.lead.email

    if not email_to:
        return

    subject = f"Update on your shipment {shipment.tracking_number or shipment.pk}"

    lines = []
    lines.append("Hi,")
    lines.append("")
    lines.append("This is a quick update about your shipment from Iconic Apparel House.")
    lines.append(f"Status: {shipment.get_status_display()} ({event_label})")

    if shipment.carrier:
        lines.append(f"Carrier: {shipment.get_carrier_display()}")

    if shipment.tracking_number:
        if shipment.tracking_url:
            lines.append(f"Tracking link: {shipment.tracking_url}")
        else:
            lines.append(f"Tracking number: {shipment.tracking_number}")

    if shipment.box_count:
        lines.append(f"Boxes: {shipment.box_count}")

    if shipment.total_weight_kg:
        lines.append(f"Weight: {shipment.total_weight_kg} kg")

    if shipment.cost_cad:
        lines.append(f"Shipping cost we paid: {shipment.cost_cad} CAD")

    lines.append("")
    lines.append("If you have any questions just reply to this email.")
    lines.append("")
    lines.append("Thank you")
    lines.append("Iconic Apparel House team")

    body = "\n".join(lines)
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None) or "no-reply@iconicapparelhouse.com"

    try:
        send_mail(subject, body, from_email, [email_to], fail_silently=True)
    except Exception:
        pass

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

from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render
from django.utils.dateparse import parse_date

from .models import Lead, LEAD_STATUS_CHOICES

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

def leads_list(request):
    lead_id = (request.GET.get("lead_id") or "").strip()
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()
    market = (request.GET.get("market") or "").strip()
    owner = (request.GET.get("owner") or "").strip()
    created_from_raw = (request.GET.get("created_from") or "").strip()
    created_to_raw = (request.GET.get("created_to") or "").strip()
    value_min_raw = (request.GET.get("value_min") or "").strip()
    value_max_raw = (request.GET.get("value_max") or "").strip()

    sort = (request.GET.get("sort") or "new").strip().lower()

    try:
        per_page = int(request.GET.get("per_page") or 50)
    except ValueError:
        per_page = 50

    if per_page not in (20, 50, 100):
        per_page = 50

    qs = Lead.objects.all()

    if status:
        if status.lower() == "converted":
            qs = qs.filter(
                Q(lead_status__iexact="Converted") | Q(opportunities__isnull=False)
            ).distinct()
        else:
            qs = qs.filter(lead_status__iexact=status)
            qs = qs.filter(opportunities__isnull=True).exclude(
                lead_status__iexact="Converted"
            )
    else:
        qs = qs.filter(opportunities__isnull=True).exclude(lead_status__iexact="Converted")

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
            | Q(product_interest__icontains=q)
            | Q(order_quantity__icontains=q)
            | Q(lead_id__icontains=q)
        )

    if market:
        qs = qs.filter(market__iexact=market)

    if owner:
        qs = qs.filter(
            Q(owner__icontains=owner)
        )

    created_from = parse_date(created_from_raw) if created_from_raw else None
    created_to = parse_date(created_to_raw) if created_to_raw else None
    if created_from:
        qs = qs.filter(created_date__gte=created_from)
    if created_to:
        qs = qs.filter(created_date__lte=created_to)

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

    paginator = Paginator(qs, per_page)
    page_number = request.GET.get("page") or 1
    page_obj = paginator.get_page(page_number)

    context = {
        "page_obj": page_obj,
        "per_page": per_page,
        "status_choices": LEAD_STATUS_CHOICES,
        "market_choices": Lead.MARKET_CHOICES,
    }
    return render(request, "crm/leads_list.html", context)


from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.contrib import messages

from .models import Lead, LeadActivity
from .forms import LeadForm


def add_lead(request):
    if request.method == "POST":
        form = LeadForm(request.POST, request.FILES)
        if form.is_valid():
            lead = form.save(commit=False)

            if not lead.created_date:
                lead.created_date = timezone.now().date()

            customer = _find_or_create_customer_for_lead(lead)
            lead.customer = customer
            lead.save()

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

            messages.success(request, "Lead saved successfully.")
            return redirect(f"{redirect('lead_detail', pk=lead.pk).url}?saved=1")
        else:
            messages.error(request, "Could not save. Please fix the errors below.")
            print("LEAD FORM ERRORS:", form.errors.as_json())
    else:
        form = LeadForm()

    return render(request, "crm/lead_form.html", {"form": form})


def edit_lead(request, pk):
    lead = get_object_or_404(Lead, pk=pk)

    if request.method == "POST":
        form = LeadForm(request.POST, request.FILES, instance=lead)
        if form.is_valid():
            lead = form.save()

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
        moq_units_raw = request.POST.get("moq_units")
        order_value_raw = request.POST.get("order_value")

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

        order_value = None
        if order_value_raw:
            try:
                order_value = float(order_value_raw)
            except ValueError:
                order_value = None

        opp = Opportunity.objects.create(
            lead=lead,
            customer=customer,
            stage=stage,
            product_type=product_type,
            product_category=product_category,
            moq_units=moq_units,
            order_value=order_value,
        )
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
        website=getattr(lead, "company_website", "") or "",
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


def lead_detail(request, pk):
    lead = get_object_or_404(Lead, pk=pk)

    opportunities = lead.opportunities.all().order_by("-created_date", "-id")
    comments = lead.comments.all()
    tasks = lead.tasks.all()
    activities = lead.activities.all()

    customer = lead.customer if lead.customer_id else None

    agents = AIAgent.objects.all()
    selected_agent = None
    messages = []

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
            comment_text = (request.POST.get("comment_text") or "").strip()
            if comment_text:
                author_name = request.user.username if request.user.is_authenticated else "User"
                LeadComment.objects.create(
                    lead=lead,
                    author=author_name,
                    content=comment_text,
                )
                LeadActivity.objects.create(
                    lead=lead,
                    activity_type="note_added",
                    description=comment_text[:200],
                )
            comments = lead.comments.all()

        elif action == "toggle_pin_comment":
            comment_id = (request.POST.get("comment_id") or "").strip()
            if comment_id:
                try:
                    c = LeadComment.objects.get(id=comment_id, lead=lead)
                    c.pinned = not c.pinned
                    c.save(update_fields=["pinned"])
                except LeadComment.DoesNotExist:
                    pass
            comments = lead.comments.all()

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
            tasks = lead.tasks.all()

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
            tasks = lead.tasks.all()

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

                messages = conversation.messages.order_by("created_at")

        # quick AI templates
        elif action == "ai_quick":
            quick_action = (request.POST.get("quick_action") or "").strip()

            selected_agent = agents.first() if agents.exists() else None
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

                messages = conversation.messages.order_by("created_at")

    # -------------------------
    # GET: load latest conversation messages
    # -------------------------
    if request.method != "POST":
        if agents.exists():
            selected_agent = agents.first()
            conversation = (
                AIConversation.objects.filter(agent=selected_agent, lead=lead)
                .order_by("-created_at")
                .first()
            )
            if conversation:
                messages = conversation.messages.order_by("created_at")

    # upcoming events for this lead
    upcoming_events = (
        Event.objects.filter(lead=lead, start_datetime__gte=timezone.now())
        .order_by("start_datetime")[:5]
    )

    context = {
        "lead": lead,
        "opportunities": opportunities,
        "customer": customer,
        "comments": comments,
        "tasks": tasks,
        "activities": activities,
        "upcoming_events": upcoming_events,
        "agents": agents,
        "selected_agent": selected_agent,
        "messages": messages,
        # new
        "budget_cad": budget_cad,
        "budget_bdt": budget_bdt,
        "cad_to_bdt": cad_to_bdt,
    }

    return render(request, "crm/lead_detail.html", context)

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

def opportunity_detail(request, pk):
    opportunity = get_object_or_404(Opportunity, pk=pk)
    lead = opportunity.lead

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

    # Comments and activity
    comments = LeadComment.objects.filter(
        lead=lead,
        opportunity=opportunity,
    ).order_by("-pinned", "-created_at")

    activities = LeadActivity.objects.filter(lead=lead).order_by("-created_at")

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

            if lead:
                customer, created = Customer.objects.get_or_create(
                    lead=lead,
                    defaults={
                        "account_brand": lead.account_brand,
                        "contact_name": lead.contact_name,
                        "email": lead.email,
                        "phone": lead.phone,
                        "market": lead.market,
                    },
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
                    description="Shipping address updated from opportunity page.",
                )

            return redirect("opportunity_detail", pk=opportunity.pk)

        # Add comment
        if action == "add_comment":
            comment_text = (request.POST.get("comment_text") or "").strip()
            if comment_text and lead:
                author_name = request.user.username if request.user.is_authenticated else "User"
                LeadComment.objects.create(
                    lead=lead,
                    opportunity=opportunity,
                    author=author_name,
                    content=comment_text,
                )
                LeadActivity.objects.create(
                    lead=lead,
                    activity_type="note_added",
                    description=f"Opportunity note: {comment_text[:200]}",
                )

            return redirect("opportunity_detail", pk=opportunity.pk)

        # Toggle pin comment
        if action == "toggle_pin_comment":
            comment_id = (request.POST.get("comment_id") or "").strip()
            if comment_id and lead:
                c = LeadComment.objects.filter(
                    id=comment_id,
                    lead=lead,
                    opportunity=opportunity,
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
                    qty_guess = opportunity.moq_units or lead.order_quantity or 0
                    try:
                        ProductionOrder.objects.create(
                            opportunity=opportunity,
                            title=po_title,
                            qty_total=qty_guess or 0,
                        )
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
    prod_orders = ProductionOrder.objects.filter(opportunity=opportunity)
    prod_totals = prod_orders.aggregate(
        total_qty=Sum("qty_total"),
        total_reject=Sum("qty_reject"),
        total_actual_cost=Sum("actual_total_cost_bdt"),
    )

    prod_total_qty = prod_totals.get("total_qty") or 0
    prod_total_reject = prod_totals.get("total_reject") or 0
    prod_total_actual_cost = prod_totals.get("total_actual_cost") or 0

    order_value = opportunity.order_value or 0
    total_cost_bdt = (prod_total_actual_cost or 0) + (shipping_cost_bdt or 0)

    profit_after_shipping = None
    profit_after_shipping_percent = None
    if order_value:
        profit_after_shipping = order_value - total_cost_bdt
        profit_after_shipping_percent = (profit_after_shipping / order_value) * 100

    context = {
        "opportunity": opportunity,
        "lead": lead,
        "customer": customer,

        "opp_tasks": opp_tasks,
        "comments": comments,
        "activities": activities,
        "stage_choices": stage_choices,

        "agents": agents,
        "selected_agent": selected_agent,
        "messages": ai_messages_qs,

        "opp_files": opp_files,

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

        # Shipping for the template
        "ship": ship,
        "ship_locked": ship_has_any_data(),
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
    sort = (request.GET.get("sort") or "recent").strip().lower()

    active_stages = _active_opportunity_stages()
    completed_statuses = _production_completed_statuses()
    active_prod_statuses = _production_active_statuses()

    qs = Customer.objects.all()

    if q:
        qs = qs.filter(
            Q(account_brand__icontains=q)
            | Q(contact_name__icontains=q)
            | Q(email__icontains=q)
            | Q(phone__icontains=q)
        )

    qs = qs.annotate(
        active_opps=Count(
            "opportunities",
            filter=Q(opportunities__stage__in=active_stages),
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

    if sort == "name":
        customers.sort(key=lambda c: ((c.account_brand or "").lower(), (c.contact_name or "").lower()))
    else:
        customers.sort(key=lambda c: c.last_activity or date.min, reverse=True)

    context = {
        "customers": customers,
        "q": q,
        "has_active": has_active,
        "has_production": has_production,
        "has_completed": has_completed,
        "sort": sort,
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
    leads = customer.leads.all().order_by("-created_date", "-id")

    opportunities = (
        customer.opportunities
        .select_related("lead")
        .order_by("-updated_at", "-id")
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

    active_stages = _active_opportunity_stages()
    active_opps = opportunities.filter(stage__in=active_stages)

    paid_opps = opportunities.filter(order_value__isnull=False)
    totals = paid_opps.aggregate(
        total_revenue=Sum("order_value"),
        total_orders=Count("id"),
    )
    total_revenue = totals.get("total_revenue") or Decimal("0.00")
    total_orders = totals.get("total_orders") or 0

    prod_orders = (
        customer.production_orders
        .select_related("opportunity", "lead")
        .order_by("-created_at", "-id")
    )

    completed_statuses = _production_completed_statuses()
    active_prod_statuses = _production_active_statuses()

    production_active = prod_orders.filter(status__in=active_prod_statuses)
    production_completed = prod_orders.filter(status__in=completed_statuses)

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

    profit_estimate = None
    profit_margin = None
    if total_revenue is not None and total_cost_bdt is not None:
        profit_estimate = total_revenue - total_cost_bdt
        if total_revenue:
            try:
                profit_margin = (profit_estimate / total_revenue) * 100
            except Exception:
                profit_margin = None

    for opp in opportunities:
        cost = prod_cost_map.get(opp.id)
        if opp.order_value and cost is not None:
            try:
                opp.profit_margin_pct = ((opp.order_value - cost) / opp.order_value) * 100
            except Exception:
                opp.profit_margin_pct = None
        else:
            opp.profit_margin_pct = None

    notes_list = customer.notes_list.all().order_by("-created_at")
    events = customer.customer_events.all().order_by("-created_at")[:50]

    context = {
        "customer": customer,
        "leads": leads,
        "opportunities": opportunities,
        "active_opps": active_opps,
        "production_active": production_active,
        "production_completed": production_completed,
        "total_revenue": total_revenue,
        "total_orders": total_orders,
        "total_cost_bdt": total_cost_bdt,
        "profit_estimate": profit_estimate,
        "profit_margin": profit_margin,
        "notes_list": notes_list,
        "events": events,
        "prod_orders": prod_orders,
    }
    return render(request, "crm/customer_detail.html", context)


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

def inventory_list(request):
    items = InventoryItem.objects.all().order_by("name")

    search = request.GET.get("q", "").strip()
    category = request.GET.get("category", "").strip()
    status = request.GET.get("status", "all").strip() or "all"

    if search:
        items = items.filter(
            Q(name__icontains=search)
            | Q(code__icontains=search)
            | Q(sku__icontains=search)
        )

    if category:
        items = items.filter(category=category)

    if status == "active":
        items = items.filter(is_active=True)
    elif status == "inactive":
        items = items.filter(is_active=False)

    total_items = items.count()
    total_quantity = items.aggregate(s=Sum("quantity"))["s"] or 0

    total_value = 0
    low_stock_count = 0
    for it in items:
        if it.unit_cost and it.quantity:
            total_value += it.unit_cost * it.quantity
        if it.min_level is not None and it.quantity is not None:
            if it.quantity <= it.min_level:
                low_stock_count += 1

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
        "items": items,
        "search": search,
        "selected_category": category,
        "selected_status": status,
        "total_items": total_items,
        "total_quantity": total_quantity,
        "total_value": total_value,
        "low_stock_count": low_stock_count,
        "smartbrain_message": smartbrain_message,
    }
    return render(request, "crm/inventory_list.html", context)


def inventory_add(request):
    if request.method == "POST":
        form = InventoryItemForm(request.POST, request.FILES)
        if form.is_valid():
            item = form.save()
            messages.success(request, "Inventory item created.")
            return redirect("inventory_detail", pk=item.pk)
    else:
        form = InventoryItemForm()

    return render(
        request,
        "crm/inventory_form.html",
        {"form": form, "mode": "add", "item": None},
    )


def inventory_edit(request, pk):
    item = get_object_or_404(InventoryItem, pk=pk)

    if request.method == "POST":
        form = InventoryItemForm(request.POST, request.FILES, instance=item)
        if form.is_valid():
            form.save()
            messages.success(request, "Inventory item updated.")
            return redirect("inventory_detail", pk=item.pk)
    else:
        form = InventoryItemForm(instance=item)

    return render(
        request,
        "crm/inventory_form.html",
        {"form": form, "mode": "edit", "item": item},
    )


def inventory_detail(request, pk):
    item = get_object_or_404(InventoryItem, pk=pk)

    total_value = None
    if item.unit_cost and item.quantity is not None:
        total_value = item.unit_cost * item.quantity

    # handle quick reorder post from detail page
    if request.method == "POST" and "quick_reorder" in request.POST:
        qty_str = (request.POST.get("reorder_quantity") or "0").strip()
        note = (request.POST.get("reorder_note") or "").strip()

        try:
            qty = Decimal(qty_str)
        except Exception:
            qty = Decimal("0")

        if qty > 0:
            InventoryReorder.objects.create(
                item=item,
                quantity=qty,
                note=note,
                created_by=request.user if request.user.is_authenticated else None,
            )
            messages.success(request, "Reorder saved for this item.")
            return redirect("inventory_detail", pk=item.pk)
        else:
            messages.warning(request, "Please enter a reorder quantity bigger than zero.")

    reorders = item.reorders.all()[:20]

    context = {
        "item": item,
        "total_value": total_value,
        "reorders": reorders,
    }
    return render(request, "crm/inventory_detail.html", context)

def inventory_detail_pdf(request, pk):
    item = get_object_or_404(InventoryItem, pk=pk)

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

    p.setFont("Helvetica-Bold", 16)
    p.drawString(50, y, f"Inventory item: {item.name}")
    y -= 30

    p.setFont("Helvetica", 11)

    lines = [
        f"Category: {item.get_category_display()}",
        f"Code: {item.code or 'Not set'}",
        f"SKU: {item.sku or 'Not set'}",
        f"Unit type: {item.unit_type or 'Not set'}",
        f"Quantity: {item.quantity}",
        f"Minimum level: {item.min_level}",
        f"Unit cost: {item.unit_cost or 'Not set'}",
    ]

    if item.unit_cost and item.quantity is not None:
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

    total_items = items.count()
    total_quantity = items.aggregate(s=Sum("quantity"))["s"] or 0

    total_value = 0
    low_stock = 0
    by_category = {}

    for it in items:
        cat = it.get_category_display()
        by_category[cat] = by_category.get(cat, 0) + 1

        if it.unit_cost and it.quantity:
            total_value += it.unit_cost * it.quantity

        if it.min_level is not None and it.quantity is not None:
            if it.quantity <= it.min_level:
                low_stock += 1

    cat_lines = ", ".join(f"{k}: {v}" for k, v in by_category.items()) or "No categories"

    prompt = (
        "You are the SmartBrain AI for a garment inventory system.\n"
        f"Total items: {total_items}\n"
        f"Total quantity: {total_quantity}\n"
        f"Total value: {total_value}\n"
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
        {"pair": "CAD → BDT"},
        {"pair": "USD → BDT"},
        {"pair": "EUR → BDT"},
        {"pair": "GBP → BDT"},
    ]

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
    }
    return render(request, "crm/world_dashboard.html", context)


@require_POST
def world_ai_fashion_news(request):
    if not ("_ai_client" in globals() and _ai_client):
        return JsonResponse({"ok": False, "error": "AI is not configured on server."})

    try:
        response = _ai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are an assistant for Iconic Apparel House. Keep it short and useful."},
                {"role": "user", "content": "Give 4 to 6 bullet points about fashion and apparel news. Simple English."},
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

from .models import Event, Lead
from .forms import EventForm

# Optional OpenAI client (safe if package not installed)
_ai_client = client
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

    if request.method == "POST":
        form = EventForm(request.POST)
        if form.is_valid():
            event = form.save()

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
                Event.objects.create(
                    title=event.title,
                    start_datetime=dt_value,
                    end_datetime=event.end_datetime,
                    event_type=event.event_type,
                    priority=event.priority,
                    status=event.status,
                    note=event.note,
                    lead=event.lead,
                    opportunity=event.opportunity,
                    customer=event.customer,
                    assigned_to_name=event.assigned_to_name,
                    assigned_to_email=event.assigned_to_email,
                    reminder_minutes_before=event.reminder_minutes_before,
                    production_stage=event.production_stage,
                )

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

    return render(request, "crm/calendar_add.html", {"form": form})


# ==============================
# CALENDAR EDIT
# ==============================
def calendar_edit(request, pk):
    event = get_object_or_404(Event, pk=pk)

    if request.method == "POST":
        form = EventForm(request.POST, instance=event)
        if form.is_valid():
            event = form.save()

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

    return render(request, "crm/calendar_edit.html", {"form": form, "event": event})


# ==============================
# CALENDAR EVENT DETAIL
# ==============================
def calendar_event_detail(request, pk):
    event = get_object_or_404(Event, pk=pk)

    upcoming_for_lead = []
    if getattr(event, "lead", None):
        upcoming_for_lead = (
            Event.objects.filter(
                lead=event.lead,
                start_datetime__gte=timezone.now(),
            )
            .exclude(pk=event.pk)
            .order_by("start_datetime")[:5]
        )

    return render(
        request,
        "crm/calendar_event_detail.html",
        {"event": event, "upcoming_for_lead": upcoming_for_lead},
    )


# ==============================
# CALENDAR EVENT AI
# ==============================
@require_POST
def calendar_event_ai(request, pk):
    if not _ai_client:
        return JsonResponse({"ok": False, "error": "AI is not configured."}, status=500)

    event = get_object_or_404(Event, pk=pk)

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

    events_qs = Event.objects.filter(
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
    for ev in events_qs.select_related("lead"):
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
    today_events_count = Event.objects.filter(start_datetime__date=today).count()
    week_events_count = Event.objects.filter(
        start_datetime__date__gte=today,
        start_datetime__date__lte=today + timedelta(days=7),
    ).count()
    overdue_events_count = Event.objects.filter(
        status__in=["planned", "in_work"],
        start_datetime__lt=now,
    ).count()

    assigned_choices = (
        Event.objects.exclude(assigned_to_name__isnull=True)
        .exclude(assigned_to_name="")
        .values_list("assigned_to_name", flat=True)
        .distinct()
        .order_by("assigned_to_name")
    )

    lead_choices = (
        Event.objects.filter(lead__isnull=False)
        .values_list("lead_id", "lead__account_brand")
        .distinct()
        .order_by("lead_id")
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
    }

    return render(request, "crm/calendar_list.html", context)


# ==============================
# TOGGLE DONE
# ==============================
@require_POST
def calendar_toggle_done(request, pk):
    event = get_object_or_404(Event, pk=pk)
    event.status = "done"
    event.save(update_fields=["status"])
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

    event = get_object_or_404(Event, pk=event_id)

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
            title = f"{opportunity.lead.account_brand} order for {opportunity.opportunity_id}"
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
    opportunity = get_object_or_404(Opportunity, pk=pk)

    if request.method == "POST":
        # very basic safe update
        opportunity.product_type = request.POST.get("product_type") or opportunity.product_type
        opportunity.product_category = request.POST.get("product_category") or opportunity.product_category

        moq_raw = request.POST.get("moq_units")
        if moq_raw:
            try:
                opportunity.moq_units = int(moq_raw)
            except ValueError:
                pass

        order_value_raw = request.POST.get("order_value")
        if order_value_raw:
            try:
                opportunity.order_value = float(order_value_raw)
            except ValueError:
                pass

        opportunity.notes = request.POST.get("notes") or opportunity.notes

        opportunity.save()
        messages.success(request, "Opportunity updated.")
        return redirect("opportunity_detail", pk=pk)

    return render(request, "crm/opportunity_edit.html", {"opportunity": opportunity})
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
    stages = list(order.stages.all())
    stages.sort(key=lambda s: STAGE_ORDER.get(s.stage_key, 99))
    return stages


# size grid helpers

SIZE_LABELS = ["XS", "S", "M", "L", "XL", "2XL", "3XL", "4XL", "5XL"]


def build_size_grid(order):
    """
    Read size_ratio_note and build a small size grid.
    Example text: 'XS 10 S 20 M 40 L 30'
    """
    text = (order.size_ratio_note or "").upper()
    result = []
    total = 0

    import re

    for label in SIZE_LABELS:
        qty = 0
        if text:
            pattern = r"\b" + re.escape(label) + r"\s+(\d+)"
            m = re.search(pattern, text)
            if m:
                qty = int(m.group(1))
        result.append({"label": label, "qty": qty if qty else None})
        total += qty

    if total == 0:
        total = None

    return result, total


def production_list(request):
    """
    List of all production orders with small dashboard numbers.
    """
    status_filter = (request.GET.get("status") or "active").strip().lower()
    completed_statuses = _production_completed_statuses()
    active_statuses = _production_active_statuses()

    orders = (
        ProductionOrder.objects
        .select_related("customer", "product", "opportunity")
        .order_by("-created_at")
    )

    if status_filter == "completed":
        orders = orders.filter(status__in=completed_statuses)
    elif status_filter == "all":
        pass
    else:
        orders = orders.exclude(status__in=completed_statuses)

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
    active_orders = orders.filter(status__in=active_statuses).count()
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
            "status_filter": status_filter,
        },
    )


def production_add(request):
    """
    Create new production order.
    """
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
    """
    Edit existing production order.
    """
    order = get_object_or_404(ProductionOrder, pk=pk)
    old_status = order.status

    if request.method == "POST":
        form = ProductionOrderForm(request.POST, request.FILES, instance=order)
        if form.is_valid():
            obj = form.save()

            if not obj.customer_id and obj.opportunity and obj.opportunity.customer_id:
                obj.customer = obj.opportunity.customer
                obj.save(update_fields=["customer"])

            if obj.status != old_status:
                customer = obj.customer or (obj.opportunity.customer if obj.opportunity else None)
                _record_customer_event(
                    customer=customer,
                    event_type="production_status",
                    title="Production status updated",
                    details=f"Production {obj.order_code or obj.pk} is now {obj.get_status_display()}.",
                    opportunity=obj.opportunity,
                    production=obj,
                )

                if obj.status in {"done", "completed"} and obj.opportunity:
                    obj.opportunity.stage = "Closed Won"
                    obj.opportunity.save(update_fields=["stage"])
                    _record_customer_event(
                        customer=customer,
                        event_type="production_completed",
                        title="Production completed",
                        details=f"Production {obj.order_code or obj.pk} marked completed.",
                        opportunity=obj.opportunity,
                        production=obj,
                    )
                elif obj.status == "closed_won" and obj.opportunity:
                    obj.opportunity.stage = "Closed Won"
                    obj.opportunity.save(update_fields=["stage"])
                    _record_customer_event(
                        customer=customer,
                        event_type="production_closed_won",
                        title="Production closed won",
                        details=f"Production {obj.order_code or obj.pk} closed won.",
                        opportunity=obj.opportunity,
                        production=obj,
                    )
                elif obj.status == "closed_lost" and obj.opportunity:
                    obj.opportunity.stage = "Closed Lost"
                    obj.opportunity.save(update_fields=["stage"])
                    _record_customer_event(
                        customer=customer,
                        event_type="production_closed_lost",
                        title="Production closed lost",
                        details=f"Production {obj.order_code or obj.pk} closed lost.",
                        opportunity=obj.opportunity,
                        production=obj,
                    )

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
    order = get_object_or_404(ProductionOrder, pk=pk)

    # sorted stages
    stages = order.stages.all().order_by("planned_start", "stage_key")

    # size grid
    size_grid, size_total = build_size_grid(order)

    # files
    attachments = order.attachments.all().order_by("-created_at")

    # shipments for this order
    shipments = order.shipments.all().order_by("-ship_date", "-created_at")

    # progress and delay
    percent_done = order.percent_done
    order_delayed = order.is_delayed

    context = {
        "order": order,
        "stages": stages,
        "percent_done": percent_done,
        "order_delayed": order_delayed,
        "size_grid": size_grid,
        "size_total": size_total,
        "attachments": attachments,
        "shipments": shipments,
    }

    return render(request, "crm/production_detail.html", context)

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
    """
    opportunity = get_object_or_404(Opportunity, pk=pk)
    customer = _ensure_customer_for_opportunity(opportunity)

    po = ProductionOrder.objects.filter(opportunity=opportunity).first()
    created = False

    if not po:
        title = f"{opportunity.lead.account_brand} order for {opportunity.opportunity_id}"
        qty_guess = opportunity.moq_units or 0

        po = ProductionOrder.objects.create(
            opportunity=opportunity,
            lead=opportunity.lead,
            customer=customer,
            title=title,
            qty_total=qty_guess,
        )
        created = True
    elif customer and not po.customer_id:
        po.customer = customer
        po.save(update_fields=["customer"])

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
Order code: {order.order_code}
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


def shipment_list(request):
    qs = Shipment.objects.all()
    sr = _select_related_fields()
    if sr:
        qs = qs.select_related(*sr)

    shipments = qs.order_by("-ship_date", "-created_at")

    total_shipments = shipments.count()
    total_boxes = sum((s.box_count or 0) for s in shipments)
    total_weight = sum((s.total_weight_kg or 0) for s in shipments)
    total_cost_bdt = sum((s.cost_bdt or Decimal("0")) for s in shipments)
    total_cost_cad = sum((s.cost_cad or Decimal("0")) for s in shipments)

    return render(
        request,
        "crm/shipment_list.html",
        {
            "shipments": shipments,
            "total_shipments": total_shipments,
            "total_boxes": total_boxes,
            "total_weight": total_weight,
            "total_cost_bdt": total_cost_bdt,
            "total_cost_cad": total_cost_cad,
        },
    )


def shipment_add(request):
    """
    Create a new shipment from menu.
    If your ShipmentForm includes order or production_order, it will show.
    """
    if request.method == "POST":
        form = ShipmentForm(request.POST)
        if form.is_valid():
            shipment = form.save()
            messages.success(request, "Shipment created.")
            return redirect("shipment_detail", pk=shipment.pk)

        messages.error(request, "Could not save. Please fix the form errors.")
        return render(
            request,
            "crm/shipment_form.html",
            {"form": form, "is_edit": False, "order": None, "order_field": ORDER_FIELD},
        )

    form = ShipmentForm(initial={"ship_date": timezone.localdate()})
    return render(
        request,
        "crm/shipment_form.html",
        {"form": form, "is_edit": False, "order": None, "order_field": ORDER_FIELD},
    )


def shipment_detail(request, pk):
    qs = Shipment.objects.all()
    sr = _select_related_fields()
    if sr:
        qs = qs.select_related(*sr)

    shipment = get_object_or_404(qs, pk=pk)
    return render(request, "crm/shipment_detail.html", {"shipment": shipment})


def shipment_edit(request, pk):
    shipment = get_object_or_404(Shipment, pk=pk)

    if request.method == "POST":
        form = ShipmentForm(request.POST, instance=shipment)
        if form.is_valid():
            form.save()
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
            },
        )

    form = ShipmentForm(instance=shipment)
    return render(
        request,
        "crm/shipment_form.html",
        {
            "form": form,
            "is_edit": True,
            "shipment": shipment,
            "order": getattr(shipment, ORDER_FIELD, None) if ORDER_FIELD else None,
            "order_field": ORDER_FIELD,
        },
    )


def shipping_add_for_opportunity(request, pk):
    """
    Create a shipment from an opportunity.
    """
    opportunity = get_object_or_404(Opportunity, pk=pk)
    customer = getattr(opportunity, "customer", None)

    if request.method == "POST":
        form = ShipmentForm(request.POST)
        if form.is_valid():
            shipment = form.save(commit=False)

            if hasattr(shipment, "opportunity"):
                shipment.opportunity = opportunity
            if hasattr(shipment, "customer"):
                shipment.customer = customer

            if not shipment.ship_date:
                shipment.ship_date = timezone.localdate()

            shipment.save()
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
            },
        )

    initial = {"ship_date": timezone.localdate()}
    if customer and "customer" in form_fields(ShipmentForm):
        initial["customer"] = customer

    form = ShipmentForm(initial=initial)
    return render(
        request,
        "crm/shipment_form.html",
        {
            "form": form,
            "opportunity": opportunity,
            "is_edit": False,
            "order": None,
            "order_field": ORDER_FIELD,
        },
    )


def shipping_add_for_order(request, pk):
    """
    Create a shipment from a production order.
    This sets the correct FK field name every time.
    """
    order = get_object_or_404(ProductionOrder, pk=pk)

    if ORDER_FIELD is None:
        messages.error(request, "Shipment model has no order link field.")
        return redirect("production_detail", pk=order.pk)

    if request.method == "POST":
        form = ShipmentForm(request.POST)
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

            # safe numbers
            if hasattr(shipment, "cost_bdt") and shipment.cost_bdt is None:
                shipment.cost_bdt = Decimal("0")
            if hasattr(shipment, "cost_cad") and shipment.cost_cad is None:
                shipment.cost_cad = Decimal("0")

            shipment.save()
            messages.success(request, "Shipment created for this order.")
            return redirect("production_detail", pk=order.pk)

        messages.error(request, "Could not save. Please fix the form errors.")
        return render(
            request,
            "crm/shipment_form.html",
            {"form": form, "order": order, "is_edit": False, "order_field": ORDER_FIELD},
        )

    # initial values
    initial = {"ship_date": timezone.localdate()}
    if "customer" in form_fields(ShipmentForm):
        initial["customer"] = getattr(order, "customer", None)
    if "opportunity" in form_fields(ShipmentForm):
        initial["opportunity"] = getattr(order, "opportunity", None)

    form = ShipmentForm(initial=initial)
    return render(
        request,
        "crm/shipment_form.html",
        {"form": form, "order": order, "is_edit": False, "order_field": ORDER_FIELD},
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


def shipment_notify_customer(request, pk):
    qs = Shipment.objects.all()
    sr = _select_related_fields()
    if sr:
        qs = qs.select_related(*sr)

    shipment = get_object_or_404(qs, pk=pk)

    email_to = None
    if getattr(shipment, "customer", None) and shipment.customer and getattr(shipment.customer, "email", None):
        email_to = shipment.customer.email
    elif getattr(shipment, "opportunity", None) and shipment.opportunity and getattr(shipment.opportunity, "lead", None):
        if shipment.opportunity.lead and getattr(shipment.opportunity.lead, "email", None):
            email_to = shipment.opportunity.lead.email

    if not email_to:
        messages.error(request, "No email address found for this shipment.")
        return redirect("shipment_detail", pk=pk)

    carrier_name = shipment.get_carrier_display() if hasattr(shipment, "get_carrier_display") else "Carrier"
    ship_date = shipment.ship_date or timezone.localdate()
    tracking_line = shipment.tracking_number or "Tracking will be shared soon."

    subject = "Your shipment from Iconic Apparel House is on the way"

    lines = [
        "Hello,",
        "",
        "Your shipment is on the way.",
        "",
        f"Carrier: {carrier_name}",
        f"Tracking: {tracking_line}",
        f"Ship date: {ship_date}",
    ]
    if getattr(shipment, "tracking_url", None):
        lines.append(f"Tracking link: {shipment.tracking_url}")
    lines += ["", "Thank you,", "Iconic Apparel House"]

    body = "\n".join(lines)
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "info@iconicapparelhouse.com")

    try:
        send_mail(subject, body, from_email, [email_to], fail_silently=False)
        messages.success(request, "Email sent.")
    except Exception as e:
        messages.error(request, f"Could not send email. {e}")

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


@login_required
def main_dashboard(request):
    today = timezone.localdate()
    try:
        period_days = int((request.GET.get("days") or "30").strip())
    except Exception:
        period_days = 30
    if period_days not in (7, 30, 60, 90, 180):
        period_days = 30

    start_period = today - timedelta(days=period_days - 1)

    # Leads
    leads_today = Lead.objects.filter(created_date=today).count()
    leads_period = Lead.objects.filter(created_date__gte=start_period).count()

    leads_daily_qs = (
        Lead.objects.filter(created_date__gte=start_period)
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
    opp_period = Opportunity.objects.filter(created_date__gte=start_period).count()

    opp_by_stage_qs = Opportunity.objects.values("stage").annotate(c=Count("id"))
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
        Opportunity.objects.filter(created_date__gte=start_period)
        .values("created_date")
        .annotate(c=Count("id"))
        .order_by("created_date")
    )
    opp_map = {row["created_date"]: int(row["c"]) for row in opp_daily_qs if row.get("created_date")}
    opp_daily_values = []
    for i in range(period_days):
        d = start_period + timedelta(days=i)
        opp_daily_values.append(int(opp_map.get(d, 0)))

    # Win vs Loss (safe guess using stage text)
    won_count = Opportunity.objects.filter(stage__iexact="Closed Won").count()
    lost_count = Opportunity.objects.filter(stage__iexact="Closed Lost").count()
    win_loss_labels = ["Won", "Lost"]
    win_loss_values = [int(won_count), int(lost_count)]

    # Lead status funnel (show qualification stage)
    lead_status_qs = Lead.objects.values("lead_status").annotate(c=Count("id"))
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
    acc_qs = AccountingEntry.objects.filter(date__gte=start_period).values("date").annotate(
        in_sum=Sum("amount_cad", filter=Q(direction="IN")),
        out_sum=Sum("amount_cad", filter=Q(direction="OUT")),
    )
    acc_map = {}
    for row in acc_qs:
        d = row.get("date")
        if not d:
            continue
        inc = _to_float(row.get("in_sum"))
        out = _to_float(row.get("out_sum"))
        acc_map[d] = inc - out

    cash_daily_values = []
    for i in range(period_days):
        d = start_period + timedelta(days=i)
        cash_daily_values.append(_to_float(acc_map.get(d, 0)))

    acc_period = AccountingEntry.objects.filter(date__gte=start_period)
    acc_income_cad = _to_float(acc_period.filter(direction="IN").aggregate(s=Sum("amount_cad"))["s"])
    acc_out_cad = _to_float(acc_period.filter(direction="OUT").aggregate(s=Sum("amount_cad"))["s"])
    acc_net_cad = acc_income_cad - acc_out_cad

    # Payroll
    pm = BDStaffMonth.objects.filter(year=today.year, month=today.month)
    payroll_total = _to_float(pm.aggregate(s=Sum("final_pay_bdt"))["s"])
    payroll_ot = _to_float(pm.aggregate(s=Sum("overtime_total_bdt"))["s"])
    payroll_bonus = _to_float(pm.aggregate(s=Sum("bonus_bdt"))["s"])
    payroll_deduction = _to_float(pm.aggregate(s=Sum("deduction_bdt"))["s"])
    payroll_paid = pm.filter(is_paid=True).count()
    payroll_unpaid = pm.filter(is_paid=False).count()

    # Production status (optional)
    prod_labels = ["On time", "Delayed", "Remake"]
    prod_counts = [0, 0, 0]
    if ProductionOrder is not None:
        try:
            on_time = ProductionOrder.objects.filter(Q(status__icontains="on time") | Q(status__icontains="ontime")).count()
            delayed = ProductionOrder.objects.filter(Q(status__icontains="delay")).count()
            remake = ProductionOrder.objects.filter(Q(status__icontains="remake") | Q(status__icontains="redo")).count()
            prod_counts = [int(on_time), int(delayed), int(remake)]
        except Exception:
            pass

    # Shipping status (optional)
    ship_labels = ["This month"]
    ship_shipped = [0]
    ship_pending = [0]
    ship_delayed = [0]
    if Shipment is not None:
        try:
            shipped = Shipment.objects.filter(Q(status__icontains="ship") | Q(status__icontains="delivered")).count()
            pending = Shipment.objects.filter(Q(status__icontains="pending") | Q(status__icontains="preparing")).count()
            delayed = Shipment.objects.filter(Q(status__icontains="delay")).count()
            ship_shipped = [int(shipped)]
            ship_pending = [int(pending)]
            ship_delayed = [int(delayed)]
        except Exception:
            pass

    # Lead sources, market, priority
    lead_source_qs = Lead.objects.values("source").annotate(c=Count("id")).order_by("-c")
    lead_source_labels, lead_source_values = _top_buckets(lead_source_qs, "source", limit=6)

    lead_priority_map = {row.get("priority") or "Unknown": int(row.get("c") or 0) for row in Lead.objects.values("priority").annotate(c=Count("id"))}
    lead_priority_labels = []
    lead_priority_values = []
    for p, _ in PRIORITY_CHOICES:
        lead_priority_labels.append(p)
        lead_priority_values.append(int(lead_priority_map.get(p, 0)))
    for p, cnt in lead_priority_map.items():
        if p not in lead_priority_labels:
            lead_priority_labels.append(p)
            lead_priority_values.append(int(cnt))

    lead_market_map = {row.get("market") or "Unknown": int(row.get("c") or 0) for row in Lead.objects.values("market").annotate(c=Count("id"))}
    lead_market_labels = []
    lead_market_values = []
    for m, _ in Lead.MARKET_CHOICES:
        lead_market_labels.append(m)
        lead_market_values.append(int(lead_market_map.get(m, 0)))
    for m, cnt in lead_market_map.items():
        if m not in lead_market_labels:
            lead_market_labels.append(m)
            lead_market_values.append(int(cnt))

    open_opps = Opportunity.objects.filter(is_open=True).count()
    overdue_followups = Lead.objects.filter(next_followup__lt=today).count()
    conversion_rate = 0.0
    if leads_period > 0:
        conversion_rate = round((opp_period / leads_period) * 100, 1)

    ai_notes = [
        f"Lead → Opportunity conversion: {conversion_rate}%",
        f"Open opportunities: {open_opps}",
        f"Overdue follow-ups: {overdue_followups}",
        f"Top lead source: {lead_source_labels[0] if lead_source_labels else 'N/A'}",
    ]

    chart_data = {
        "leads_labels": leads_daily_labels,
        "leads_values": leads_daily_values,
        "opp_daily_values": opp_daily_values,
        "cash_daily_values": cash_daily_values,
        "opp_stage_labels": opp_stage_labels,
        "opp_stage_values": opp_stage_values,
        "lead_status_labels": lead_status_labels,
        "lead_status_values": lead_status_values,
        "lead_source_labels": lead_source_labels,
        "lead_source_values": lead_source_values,
        "lead_priority_labels": lead_priority_labels,
        "lead_priority_values": lead_priority_values,
        "lead_market_labels": lead_market_labels,
        "lead_market_values": lead_market_values,
        "win_loss_labels": win_loss_labels,
        "win_loss_values": win_loss_values,
        "prod_labels": prod_labels,
        "prod_counts": prod_counts,
        "ship_labels": ship_labels,
        "ship_shipped": ship_shipped,
        "ship_pending": ship_pending,
        "ship_delayed": ship_delayed,
    }

    ctx = {
        "today": today,
        "leads_today": leads_today,
        "leads_period": leads_period,

        "opp_period": opp_period,
        "open_opps": open_opps,
        "conversion_rate": conversion_rate,
        "overdue_followups": overdue_followups,

        "acc_income_cad_period": acc_income_cad,
        "acc_out_cad_period": acc_out_cad,
        "acc_net_cad_period": acc_net_cad,

        "payroll_total": payroll_total,
        "payroll_ot": payroll_ot,
        "payroll_bonus": payroll_bonus,
        "payroll_deduction": payroll_deduction,
        "payroll_paid": payroll_paid,
        "payroll_unpaid": payroll_unpaid,
        "payroll_year": today.year,
        "payroll_month": today.month,
        "period_days": period_days,
        "period_label": f"Last {period_days} days",
        "ai_notes": ai_notes,
        "chart_data": chart_data,
    }

    return render(request, "crm/main_dashboard.html", ctx)

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect

from .models import Lead, Opportunity


def convert_lead_to_opportunity(request, pk):
    lead = get_object_or_404(Lead, pk=pk)

    if request.method == "POST":
        customer = lead.customer if lead.customer_id else _find_or_create_customer_for_lead(lead)
        if not lead.customer_id and customer:
            lead.customer = customer
            lead.save(update_fields=["customer"])

        opp = Opportunity.objects.create(
            lead=lead,
            customer=customer,
            stage="Prospecting",
            product_category="Other",
            product_type="Other",
        )
        lead.lead_status = "Converted"
        lead.save(update_fields=["lead_status"])
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

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages

from .models import Lead, Customer, Opportunity


def convert_lead_to_opportunity(request, pk):
    lead = get_object_or_404(Lead, pk=pk)

    if request.method == "POST":
        customer = lead.customer if lead.customer_id else _find_or_create_customer_for_lead(lead)
        if not lead.customer_id and customer:
            lead.customer = customer
            lead.save(update_fields=["customer"])

        opp = Opportunity.objects.create(
            lead=lead,
            customer=customer,
            stage="Prospecting",
            product_category="Other",
            product_type="Other",
        )
        lead.lead_status = "Converted"
        lead.save(update_fields=["lead_status"])
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
    customers = Customer.objects.all().order_by("account_brand")
    leads = Lead.objects.all().order_by("-created_date")

    if request.method == "POST":
        customer_id = request.POST.get("customer")
        lead_id = request.POST.get("lead")

        customer = None
        lead = None

        if lead_id:
            lead = Lead.objects.filter(pk=lead_id).first()

        if customer_id:
            customer = Customer.objects.filter(pk=customer_id).first()

        if not lead and customer:
            lead = Lead.objects.create(
                account_brand=customer.account_brand,
                contact_name=customer.contact_name,
                email=customer.email,
                phone=customer.phone,
                market=customer.market,
                source="Returning Client",
            )
            lead.customer = customer
            lead.save(update_fields=["customer"])

        if not lead:
            messages.error(request, "Please select a lead or a customer.")
            return redirect("add_opportunity")

        if lead.customer_id:
            customer = lead.customer
        elif customer:
            lead.customer = customer
            lead.save(update_fields=["customer"])
        else:
            customer = _find_or_create_customer_for_lead(lead)
            lead.customer = customer
            lead.save(update_fields=["customer"])

        stage = request.POST.get("stage") or "Prospecting"
        product_type = request.POST.get("product_type") or "Other"
        product_category = request.POST.get("product_category") or "Other"

        opp = Opportunity.objects.create(
            lead=lead,
            stage=stage,
            product_type=product_type,
            product_category=product_category,
            customer=customer,
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
        "selected_customer_id": request.GET.get("customer") or "",
        "selected_lead_id": request.GET.get("lead") or "",
    }
    return render(request, "crm/add_opportunity.html", context)



@login_required
def library_home(request):
    return render(request, "crm/library_home.html")


from django.shortcuts import render
from django.core.paginator import Paginator
from django.db.models import Q
from django.utils.dateparse import parse_date
from .models import Opportunity

def opportunities_list(request):
    q = (request.GET.get("q") or "").strip()
    stage = (request.GET.get("stage") or "").strip()
    status = (request.GET.get("status") or "").strip()
    created_from_raw = (request.GET.get("created_from") or "").strip()
    created_to_raw = (request.GET.get("created_to") or "").strip()
    value_min_raw = (request.GET.get("value_min") or "").strip()
    value_max_raw = (request.GET.get("value_max") or "").strip()

    sort = (request.GET.get("sort") or "new").strip().lower()

    try:
        per_page = int(request.GET.get("per_page") or 50)
    except ValueError:
        per_page = 50

    if per_page not in (20, 50, 100):
        per_page = 50

    active_stages = _active_opportunity_stages()
    qs = (
        Opportunity.objects
        .select_related("lead")
        .exclude(stage="Production")
        .exclude(productionorder__isnull=False)
    )

    if q:
        qs = qs.filter(
            Q(opportunity_id__icontains=q)
            | Q(stage__icontains=q)
            | Q(product_type__icontains=q)
            | Q(product_category__icontains=q)
            | Q(lead__lead_id__icontains=q)
            | Q(lead__account_brand__icontains=q)
            | Q(lead__contact_name__icontains=q)
            | Q(lead__email__icontains=q)
        )

    if stage:
        qs = qs.filter(stage__iexact=stage)

    if status:
        if status == "open":
            qs = qs.filter(stage__in=active_stages)
        elif status == "closed_won":
            qs = qs.filter(stage="Closed Won")
        elif status == "closed_lost":
            qs = qs.filter(stage="Closed Lost")
        elif status == "all":
            pass
    else:
        qs = qs.filter(stage__in=active_stages)

    created_from = parse_date(created_from_raw) if created_from_raw else None
    created_to = parse_date(created_to_raw) if created_to_raw else None
    if created_from:
        qs = qs.filter(created_date__gte=created_from)
    if created_to:
        qs = qs.filter(created_date__lte=created_to)

    value_min = _parse_money_value(value_min_raw) if value_min_raw else None
    value_max = _parse_money_value(value_max_raw) if value_max_raw else None
    if value_min is not None:
        qs = qs.filter(order_value__gte=value_min)
    if value_max is not None:
        qs = qs.filter(order_value__lte=value_max)

    if sort == "old":
        qs = qs.order_by("created_date", "id")
    else:
        qs = qs.order_by("-created_date", "-id")

    paginator = Paginator(qs, per_page)
    page_number = request.GET.get("page") or 1
    page_obj = paginator.get_page(page_number)

    context = {
        "page_obj": page_obj,
        "per_page": per_page,
        "stage_choices": Opportunity.STAGE_CHOICES,
    }
    return render(request, "crm/opportunities_list.html", context)
