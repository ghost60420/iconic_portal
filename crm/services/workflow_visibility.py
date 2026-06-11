from decimal import Decimal

from django.db.models import Q
from django.urls import NoReverseMatch, reverse

from crm.models import CostingHeader, Invoice, OrderLifecycle, ProductionOrder, QuickCosting, Shipment
from crm.permissions import can_view_internal_costing
from crm.services.order_lifecycle import lifecycle_timeline_steps


LIFECYCLE_SELECT_RELATED = (
    "customer",
    "lead",
    "opportunity",
    "costing",
    "quotation",
    "invoice",
    "production_order",
    "shipping_record",
)


def _safe_url(url_name, record):
    pk = getattr(record, "pk", None)
    if not pk:
        return ""
    try:
        return reverse(url_name, args=[pk])
    except NoReverseMatch:
        return ""


def _money(currency, value):
    if value in (None, ""):
        return ""
    try:
        amount = Decimal(str(value))
    except Exception:
        return ""
    prefix = f"{currency} " if currency else ""
    return f"{prefix}{amount:,.2f}"


def _display_name(user):
    if not user:
        return ""
    return user.get_full_name() or user.username


def _customer_label(customer, lead=None):
    if customer:
        return (
            getattr(customer, "account_brand", "")
            or getattr(customer, "contact_name", "")
            or str(customer)
        )
    if lead:
        return (
            getattr(lead, "account_brand", "")
            or getattr(lead, "contact_name", "")
            or str(lead)
        )
    return "Customer not linked"


def _is_quotation(costing):
    return bool(
        costing
        and getattr(costing, "quotation_number", "")
        and getattr(costing, "quoted_at", None)
    )


def _is_quick_costing(costing):
    return bool(costing and getattr(costing, "costing_type", "") == "quick")


def _costing_url_name(costing):
    return "quick_costing_detail" if _is_quick_costing(costing) else "cost_sheet_detail"


def _costing_type_label(costing):
    return "Quick Costing" if _is_quick_costing(costing) else "Advanced Costing"


def _latest_by_updated_at(*records):
    records = [record for record in records if record]
    if not records:
        return None
    return max(records, key=lambda record: getattr(record, "updated_at", None) or getattr(record, "created_at", None))


def _workflow_costing_record(links):
    return _latest_by_updated_at(links.get("costing"), links.get("quick_costing"))


def _workflow_costing_count(links):
    return int(links.get("advanced_costing_count") or 0) + int(links.get("quick_costing_count") or 0)


def _workflow_costing_notes(costing, links):
    if not costing:
        return ""
    count = _workflow_costing_count(links)
    label = _costing_type_label(costing)
    if count > 1:
        return f"{label} · {count} total costings"
    return label


def _first_or_none(queryset):
    try:
        return queryset.first()
    except Exception:
        return None


def find_workflow_lifecycle(
    *,
    lead=None,
    opportunity=None,
    costing=None,
    quotation=None,
    invoice=None,
    production_order=None,
    shipment=None,
):
    query = Q()
    has_query = False

    def add(condition):
        nonlocal query, has_query
        query |= condition
        has_query = True

    if lead and getattr(lead, "pk", None):
        add(Q(lead=lead))
    if opportunity and getattr(opportunity, "pk", None):
        add(Q(opportunity=opportunity))
        if getattr(opportunity, "lead_id", None):
            add(Q(lead_id=opportunity.lead_id))
    if costing and getattr(costing, "pk", None):
        add(Q(costing=costing) | Q(quotation=costing))
        if getattr(costing, "opportunity_id", None):
            add(Q(opportunity_id=costing.opportunity_id))
    if quotation and getattr(quotation, "pk", None):
        add(Q(quotation=quotation) | Q(costing=quotation))
        if getattr(quotation, "opportunity_id", None):
            add(Q(opportunity_id=quotation.opportunity_id))
    if invoice and getattr(invoice, "pk", None):
        add(Q(invoice=invoice))
        if getattr(invoice, "order_id", None):
            add(Q(production_order_id=invoice.order_id))
        if getattr(invoice, "costing_header_id", None):
            add(Q(costing_id=invoice.costing_header_id) | Q(quotation_id=invoice.costing_header_id))
    if production_order and getattr(production_order, "pk", None):
        add(Q(production_order=production_order))
        if getattr(production_order, "opportunity_id", None):
            add(Q(opportunity_id=production_order.opportunity_id))
        if getattr(production_order, "lead_id", None):
            add(Q(lead_id=production_order.lead_id))
        if getattr(production_order, "costing_header_id", None):
            add(
                Q(costing_id=production_order.costing_header_id)
                | Q(quotation_id=production_order.costing_header_id)
            )
    if shipment and getattr(shipment, "pk", None):
        add(Q(shipping_record=shipment))
        if getattr(shipment, "order_id", None):
            add(Q(production_order_id=shipment.order_id))
        if getattr(shipment, "opportunity_id", None):
            add(Q(opportunity_id=shipment.opportunity_id))

    if not has_query:
        return None

    return (
        OrderLifecycle.objects.select_related(*LIFECYCLE_SELECT_RELATED)
        .filter(query)
        .order_by("-updated_at", "-id")
        .first()
    )


def _hydrate_links(
    *,
    lifecycle=None,
    lead=None,
    opportunity=None,
    costing=None,
    quotation=None,
    quick_costing=None,
    invoice=None,
    production_order=None,
    shipment=None,
):
    advanced_costing_count = 1 if costing else 0
    quick_costing_count = 1 if quick_costing else 0

    if lifecycle:
        lead = lead or lifecycle.lead
        opportunity = opportunity or lifecycle.opportunity
        costing = costing or lifecycle.costing or lifecycle.quotation
        quotation = quotation or lifecycle.quotation
        invoice = invoice or lifecycle.invoice
        production_order = production_order or lifecycle.production_order
        shipment = shipment or lifecycle.shipping_record

    if opportunity and not lead:
        lead = getattr(opportunity, "lead", None)
    if costing:
        opportunity = opportunity or getattr(costing, "opportunity", None)
        quotation = quotation or (costing if _is_quotation(costing) else None)
    if quick_costing:
        opportunity = opportunity or getattr(quick_costing, "opportunity", None)
    if invoice:
        production_order = production_order or getattr(invoice, "order", None)
        costing = costing or getattr(invoice, "costing_header", None)
        quotation = quotation or (costing if _is_quotation(costing) else None)
    if production_order:
        lead = lead or getattr(production_order, "lead", None)
        opportunity = opportunity or getattr(production_order, "opportunity", None)
        costing = costing or getattr(production_order, "costing_header", None)
        quotation = quotation or (costing if _is_quotation(costing) else None)
    if shipment:
        production_order = production_order or getattr(shipment, "order", None)
        opportunity = opportunity or getattr(shipment, "opportunity", None)

    if lead and not opportunity:
        opportunity = _first_or_none(lead.opportunities.order_by("-created_date", "-id"))
    if opportunity and not costing:
        costing = _first_or_none(
            CostingHeader.objects.select_related("opportunity", "customer")
            .filter(opportunity=opportunity)
            .order_by("-updated_at", "-id")
        )
        quotation = quotation or (costing if _is_quotation(costing) else None)
    if opportunity:
        advanced_costing_count = CostingHeader.objects.filter(opportunity=opportunity).count()
        if not quick_costing:
            quick_costing = _first_or_none(
                QuickCosting.objects.select_related("opportunity", "created_by")
                .filter(opportunity=opportunity)
                .order_by("-updated_at", "-id")
            )
        quick_costing_count = QuickCosting.objects.filter(opportunity=opportunity).count()
    if costing and not invoice:
        invoice = _first_or_none(
            costing.invoices.select_related("order", "customer", "costing_header")
            .order_by("-created_at", "-id")
        )
    if opportunity and not production_order:
        production_order = _first_or_none(
            ProductionOrder.objects.select_related("lead", "opportunity", "customer", "costing_header")
            .filter(opportunity=opportunity)
            .order_by("-created_at", "-id")
        )
    if invoice and not production_order:
        production_order = getattr(invoice, "order", None)
    if production_order and not invoice:
        invoice = _first_or_none(
            production_order.invoices.select_related("customer", "order", "costing_header")
            .order_by("-created_at", "-id")
        )
    if production_order and not shipment:
        shipment = _first_or_none(
            production_order.shipments.select_related("order", "opportunity", "customer")
            .order_by("-ship_date", "-created_at", "-id")
        )
    if opportunity and not shipment:
        shipment = _first_or_none(
            Shipment.objects.select_related("order", "opportunity", "customer")
            .filter(opportunity=opportunity)
            .order_by("-ship_date", "-created_at", "-id")
        )

    return {
        "lead": lead,
        "opportunity": opportunity,
        "costing": costing,
        "quick_costing": quick_costing,
        "advanced_costing_count": advanced_costing_count,
        "quick_costing_count": quick_costing_count,
        "quotation": quotation,
        "invoice": invoice,
        "production_order": production_order,
        "shipment": shipment,
    }


def _record_label(key, record):
    if not record:
        return ""
    if key == "lead":
        return getattr(record, "lead_id", "") or f"Lead {record.pk}"
    if key == "opportunity":
        return getattr(record, "opportunity_id", "") or f"Opportunity {record.pk}"
    if key == "costing":
        if _is_quick_costing(record):
            return f"QC-{record.pk}"
        return f"COST-{record.pk}"
    if key == "quotation":
        return getattr(record, "quotation_number", "") or f"Quote {record.pk}"
    if key == "invoice":
        return getattr(record, "invoice_number", "") or f"Invoice {record.pk}"
    if key == "production":
        return getattr(record, "order_code", "") or f"PO-{record.pk}"
    if key == "shipping":
        return f"SHP-{record.pk:05d}" if isinstance(record.pk, int) else f"Shipment {record.pk}"
    return str(record)


def _status_label(record, fallback=""):
    if not record:
        return fallback
    get_display = getattr(record, "get_status_display", None)
    if callable(get_display):
        return get_display()
    get_stage_display = getattr(record, "get_stage_display", None)
    if callable(get_stage_display):
        return get_stage_display()
    return (
        getattr(record, "lead_status", "")
        or getattr(record, "status", "")
        or getattr(record, "stage", "")
        or fallback
    )


def _product_label(lead=None, opportunity=None, costing=None, invoice=None, production_order=None, shipment=None):
    order = production_order or getattr(invoice, "order", None) or getattr(shipment, "order", None)
    if order:
        product = getattr(order, "product", None)
        return (
            getattr(product, "name", "")
            or getattr(order, "style_name", "")
            or getattr(order, "title", "")
            or "Product not set"
        )
    if costing:
        return (
            getattr(costing, "style_name", "")
            or getattr(costing, "style_code", "")
            or getattr(costing, "product_type", "")
            or "Product not set"
        )
    if opportunity:
        return (
            getattr(opportunity, "product_type", "")
            or getattr(opportunity, "product_category", "")
            or "Product not set"
        )
    if lead:
        return (
            getattr(lead, "primary_product_type", "")
            or getattr(lead, "product_interest", "")
            or getattr(lead, "product_category", "")
            or "Product not set"
        )
    return "Product not set"


def _quantity_label(lead=None, opportunity=None, costing=None, invoice=None, production_order=None, shipment=None):
    order = production_order or getattr(invoice, "order", None) or getattr(shipment, "order", None)
    qty = getattr(order, "qty_total", None) if order else None
    if qty:
        return f"{qty} units"
    qty = getattr(costing, "order_quantity", None) if costing else None
    if qty:
        return f"{qty} units"
    qty = getattr(opportunity, "moq_units", None) if opportunity else None
    if qty:
        return f"{qty} units"
    qty = getattr(lead, "order_quantity", None) if lead else None
    return str(qty) if qty else "Quantity not set"


def _due_label(invoice=None, production_order=None, shipment=None, opportunity=None, lead=None):
    if shipment and getattr(shipment, "ship_date", None):
        return f"Ship date {shipment.ship_date:%Y-%m-%d}"
    if production_order and getattr(production_order, "bulk_deadline", None):
        return f"Production due {production_order.bulk_deadline:%Y-%m-%d}"
    if invoice and getattr(invoice, "due_date", None):
        return f"Invoice due {invoice.due_date:%Y-%m-%d}"
    if opportunity and getattr(opportunity, "next_followup", None):
        return f"Follow up {opportunity.next_followup:%Y-%m-%d}"
    followup = getattr(lead, "next_follow_up_date", None) or getattr(lead, "next_followup", None) if lead else None
    if followup:
        return f"Follow up {followup:%Y-%m-%d}"
    return "No date set"


def _summary_status(record_type, links, lifecycle):
    if record_type == "lead":
        return _status_label(links["lead"], "Lead")
    if record_type == "opportunity":
        return _status_label(links["opportunity"], "Opportunity")
    if record_type in {"costing", "quotation"}:
        return _status_label(links["costing"], "Costing")
    if record_type == "invoice":
        invoice = links["invoice"]
        if invoice and hasattr(invoice, "payment_status_label"):
            return invoice.payment_status_label
        return _status_label(invoice, "Invoice")
    if record_type == "production":
        return _status_label(links["production_order"], "Production")
    if record_type == "shipping":
        return _status_label(links["shipment"], "Shipping")
    return lifecycle.get_status_display() if lifecycle else "Workflow"


def _summary_money(links, can_view_costing=False):
    invoice = links["invoice"]
    if invoice:
        return _money(getattr(invoice, "currency", ""), getattr(invoice, "total_amount", None))
    opportunity = links["opportunity"]
    if opportunity and getattr(opportunity, "order_value", None):
        return _money(getattr(opportunity, "order_currency", "") or "BDT", opportunity.order_value)
    quotation = links["quotation"]
    if can_view_costing and quotation and getattr(quotation, "quotation_number", ""):
        return f"Quote {quotation.quotation_number}"
    return "Value not set"


def _timeline_from_lifecycle(lifecycle, active_key, links=None):
    if not lifecycle:
        return []
    links = links or {}
    workflow_costing = _workflow_costing_record(links)
    rows = []
    for step in lifecycle_timeline_steps(lifecycle, include_amounts=False):
        record = step.get("record")
        url_name = step.get("url_name")
        step_date = step.get("date")
        notes = step.get("notes", "")
        if step.get("key") == "costing" and workflow_costing:
            record = workflow_costing
            url_name = _costing_url_name(workflow_costing)
            step_date = getattr(workflow_costing, "updated_at", None) or getattr(workflow_costing, "created_at", None)
            notes = _workflow_costing_notes(workflow_costing, links)
        url = _safe_url(step.get("url_name"), record) if record else ""
        if step.get("key") == "costing":
            url = _safe_url(url_name, record) if record else ""
        rows.append(
            {
                "key": step.get("key", ""),
                "label": step.get("label", ""),
                "date": step_date,
                "is_done": bool(record) if step.get("key") == "costing" else step.get("is_done", False),
                "is_active": step.get("key") == active_key,
                "url": url,
                "record_label": _record_label(step.get("key", ""), record) if record else "",
                "notes": notes,
            }
        )
    return rows


def _fallback_timeline(links, active_key):
    workflow_costing = _workflow_costing_record(links)
    stages = [
        ("lead", "Lead", links["lead"], "lead_detail", getattr(links["lead"], "created_date", None), _status_label(links["lead"])),
        (
            "costing",
            "Costing",
            workflow_costing,
            _costing_url_name(workflow_costing) if workflow_costing else "cost_sheet_detail",
            getattr(workflow_costing, "updated_at", None) or getattr(workflow_costing, "created_at", None),
            _workflow_costing_notes(workflow_costing, links),
        ),
        (
            "quotation",
            "Quotation",
            links["quotation"],
            "cost_sheet_client_quotation",
            getattr(links["quotation"], "quoted_at", None),
            getattr(links["quotation"], "quotation_number", ""),
        ),
        (
            "invoice",
            "Invoice",
            links["invoice"],
            "invoice_view",
            getattr(links["invoice"], "issue_date", None),
            getattr(links["invoice"], "payment_status_label", "") if links["invoice"] else "",
        ),
        (
            "production",
            "Production",
            links["production_order"],
            "production_detail",
            getattr(links["production_order"], "created_at", None),
            _status_label(links["production_order"]),
        ),
        (
            "shipping",
            "Shipping",
            links["shipment"],
            "shipment_detail",
            getattr(links["shipment"], "ship_date", None),
            _status_label(links["shipment"]),
        ),
    ]
    rows = []
    for key, label, record, url_name, step_date, notes in stages:
        if key == "quotation" and not _is_quotation(record):
            record = None
        rows.append(
            {
                "key": key,
                "label": label,
                "date": step_date,
                "is_done": bool(record),
                "is_active": key == active_key,
                "url": _safe_url(url_name, record) if record else "",
                "record_label": _record_label(key, record) if record else "",
                "notes": notes,
            }
        )
    return rows


def build_workflow_visibility_context(
    record_type,
    *,
    user=None,
    lead=None,
    opportunity=None,
    costing=None,
    quotation=None,
    invoice=None,
    production_order=None,
    shipment=None,
    lifecycle=None,
):
    if not lifecycle:
        lifecycle = find_workflow_lifecycle(
            lead=lead,
            opportunity=opportunity,
            costing=costing,
            quotation=quotation,
            invoice=invoice,
            production_order=production_order,
            shipment=shipment,
        )

    links = _hydrate_links(
        lifecycle=lifecycle,
        lead=lead,
        opportunity=opportunity,
        costing=costing,
        quotation=quotation,
        invoice=invoice,
        production_order=production_order,
        shipment=shipment,
    )
    can_view_costing = can_view_internal_costing(user)
    workflow_costing = _workflow_costing_record(links)

    customer = (
        getattr(links["invoice"], "customer", None)
        or getattr(links["shipment"], "customer", None)
        or getattr(links["production_order"], "customer", None)
        or getattr(links["costing"], "customer", None)
        or getattr(links["opportunity"], "customer", None)
        or getattr(links["lead"], "customer", None)
    )

    nav_records = [
        ("lead", "Lead", links["lead"], "lead_detail"),
        ("opportunity", "Opportunity", links["opportunity"], "opportunity_detail"),
        ("costing", "Costing", workflow_costing, _costing_url_name(workflow_costing) if workflow_costing else "cost_sheet_detail"),
        ("quotation", "Quotation", links["quotation"], "cost_sheet_client_quotation"),
        ("invoice", "Invoice", links["invoice"], "invoice_view"),
        ("production", "Production", links["production_order"], "production_detail"),
        ("shipping", "Shipping", links["shipment"], "shipment_detail"),
    ]

    nav_items = []
    for key, label, record, url_name in nav_records:
        if key in {"costing", "quotation"} and not can_view_costing:
            continue
        if key == "quotation" and not _is_quotation(record):
            continue
        url = _safe_url(url_name, record)
        if not record or not url:
            continue
        nav_items.append(
            {
                "key": key,
                "label": label,
                "record_label": _record_label(key, record),
                "url": url,
                "is_active": record_type == key,
            }
        )

    if lifecycle and lifecycle.pk:
        nav_items.append(
            {
                "key": "lifecycle",
                "label": "Lifecycle",
                "record_label": f"#{lifecycle.pk}",
                "url": _safe_url("order_lifecycle_detail", lifecycle),
                "is_active": record_type == "lifecycle",
            }
        )

    timeline = _timeline_from_lifecycle(lifecycle, record_type, links) or _fallback_timeline(links, record_type)
    if not can_view_costing:
        timeline = [step for step in timeline if step.get("key") not in {"costing", "quotation"}]
    current_owner = (
        getattr(links["lead"], "assigned_to", None)
        or getattr(links["lead"], "owner", "")
        or getattr(links["opportunity"], "assigned_to", None)
    )

    summary = {
        "title": _product_label(
            lead=links["lead"],
            opportunity=links["opportunity"],
            costing=links["costing"],
            invoice=links["invoice"],
            production_order=links["production_order"],
            shipment=links["shipment"],
        ),
        "customer": _customer_label(customer, links["lead"]),
        "quantity": _quantity_label(
            lead=links["lead"],
            opportunity=links["opportunity"],
            costing=links["costing"],
            invoice=links["invoice"],
            production_order=links["production_order"],
            shipment=links["shipment"],
        ),
        "status": _summary_status(record_type, links, lifecycle),
        "value": _summary_money(links, can_view_costing=can_view_costing),
        "date": _due_label(
            invoice=links["invoice"],
            production_order=links["production_order"],
            shipment=links["shipment"],
            opportunity=links["opportunity"],
            lead=links["lead"],
        ),
        "owner": _display_name(current_owner) if hasattr(current_owner, "username") else (current_owner or "Unassigned"),
        "stage": record_type.replace("_", " ").title(),
    }

    return {
        "workflow_nav_items": nav_items,
        "workflow_order_summary": summary,
        "workflow_timeline_steps": timeline,
        "workflow_lifecycle": lifecycle,
    }
