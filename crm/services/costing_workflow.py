from decimal import Decimal, ROUND_HALF_UP

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from crm.models import ActualCostEntry, CostingAuditLog, CostingHeader, Invoice, ProductionOrder
from crm.services.costing_engine import compute_costing
from crm.services.order_lifecycle import (
    create_lifecycle_from_invoice,
    create_lifecycle_from_production,
    create_lifecycle_from_quotation,
)


DISPLAY_QUANT = Decimal("0.01")


class CostingWorkflowError(Exception):
    pass


def _d(value):
    if value in ("", None):
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _money(value):
    return _d(value).quantize(DISPLAY_QUANT, rounding=ROUND_HALF_UP)


def _user_or_none(user):
    return user if user and getattr(user, "is_authenticated", False) else None


def _next_quotation_number():
    prefix = f"QT{timezone.now():%Y}"
    latest = (
        CostingHeader.objects.filter(quotation_number__startswith=prefix)
        .exclude(quotation_number="")
        .order_by("-quotation_number")
        .first()
    )
    next_num = 1
    if latest and latest.quotation_number:
        try:
            next_num = int(latest.quotation_number.replace(prefix, "")) + 1
        except ValueError:
            next_num = 1

    for offset in range(1000):
        candidate = f"{prefix}{next_num + offset:04}"
        if not CostingHeader.objects.filter(quotation_number=candidate).exists():
            return candidate

    return f"{prefix}{timezone.now():%m%d%H%M%S}"


def _next_invoice_number():
    prefix = "INV"
    latest = Invoice.objects.filter(invoice_number__startswith=prefix).order_by("-invoice_number").first()
    next_num = 1
    if latest and latest.invoice_number:
        raw = latest.invoice_number.replace(prefix, "").strip()
        try:
            next_num = int(raw) + 1
        except ValueError:
            next_num = 1

    for offset in range(1000):
        candidate = f"{prefix}{next_num + offset:05}"
        if not Invoice.objects.filter(invoice_number=candidate).exists():
            return candidate

    return f"{prefix}{timezone.now():%y%m%d%H%M%S}"


def _next_order_code():
    prefix = "PO"
    latest = ProductionOrder.objects.filter(order_code__startswith=prefix).order_by("-order_code").first()
    next_num = 1
    if latest and latest.order_code:
        raw = latest.order_code.replace(prefix, "").strip()
        try:
            next_num = int(raw) + 1
        except ValueError:
            next_num = 1

    for offset in range(1000):
        candidate = f"{prefix}{next_num + offset:04}"
        if not ProductionOrder.objects.filter(order_code=candidate).exists():
            return candidate

    return f"{prefix}{timezone.now():%y%m%d%H%M%S}"


def _invoice_region_for_costing(costing):
    currency = (costing.currency or "").upper()
    if currency == "BDT" or costing.factory_location == "bd":
        return "BD"
    return "CA"


def get_costing_quote_amounts(costing):
    calc = compute_costing(costing.id)
    if not calc:
        raise CostingWorkflowError("Costing calculation is not available.")

    quantity = int(costing.order_quantity or 0)
    if quantity <= 0:
        raise CostingWorkflowError("Order quantity must be greater than 0 before conversion.")

    unit_price = _d(calc.get("final_offer_fob_per_piece")) or _d(calc.get("fob_per_piece"))
    order_total = _d(calc.get("total_final_offer_order")) or _d(calc.get("total_sales_order"))
    standard_cost_total = _d(calc.get("total_cost_order"))

    if unit_price <= 0 or order_total <= 0:
        raise CostingWorkflowError("FOB price must be set before conversion.")

    labor_total = _d(calc.get("breakdown_order", {}).get("labor"))
    other_cost_total = standard_cost_total - labor_total
    if other_cost_total < 0:
        other_cost_total = Decimal("0")

    return {
        "calc": calc,
        "quantity": quantity,
        "unit_price": unit_price,
        "order_total": order_total,
        "standard_cost_total": standard_cost_total,
        "labor_total": labor_total,
        "other_cost_total": other_cost_total,
    }


def convert_costing_to_quotation(costing, user=None):
    if costing.status != "approved":
        raise CostingWorkflowError("Approve the costing before converting it to a quotation.")

    get_costing_quote_amounts(costing)
    if costing.quotation_number and costing.quoted_at:
        create_lifecycle_from_quotation(costing, user=user)
        return costing

    costing.quotation_number = costing.quotation_number or _next_quotation_number()
    costing.quoted_at = costing.quoted_at or timezone.now()
    costing.quoted_by = costing.quoted_by or _user_or_none(user)
    costing.save(update_fields=["quotation_number", "quoted_at", "quoted_by", "updated_at"])
    CostingAuditLog.objects.create(
        costing=costing,
        action="quoted",
        changed_by=_user_or_none(user),
        note=costing.quotation_number,
    )
    create_lifecycle_from_quotation(costing, user=user)
    return costing


def create_invoice_from_costing(costing, user=None):
    if costing.status != "approved":
        raise CostingWorkflowError("Approve the costing before converting it to an invoice.")

    with transaction.atomic():
        costing = CostingHeader.objects.select_for_update().get(pk=costing.pk)
        convert_costing_to_quotation(costing, user=user)

        existing = Invoice.objects.filter(costing_header=costing).order_by("-created_at", "-id").first()
        if existing:
            create_lifecycle_from_invoice(existing, user=user)
            return existing, False

        amounts = get_costing_quote_amounts(costing)
        today = timezone.localdate()
        invoice = Invoice.objects.create(
            costing_header=costing,
            customer=costing.customer,
            invoice_number=_next_invoice_number(),
            issue_date=today,
            due_date=today + timezone.timedelta(days=14),
            currency=costing.currency or "CAD",
            invoice_region=_invoice_region_for_costing(costing),
            subtotal=_money(amounts["order_total"]),
            shipping_amount=Decimal("0"),
            discount_amount=Decimal("0"),
            tax_amount=Decimal("0"),
            total_amount=_money(amounts["order_total"]),
            paid_amount=Decimal("0"),
            status="sent",
            notes=f"Converted from quotation {costing.quotation_number or 'COST-' + str(costing.pk)}.",
            sewing_charge=_money(amounts["labor_total"]),
            other_internal_cost=_money(amounts["other_cost_total"]),
            internal_cost_note=f"Auto-filled from approved costing COST-{costing.pk}.",
        )
        CostingAuditLog.objects.create(
            costing=costing,
            action="invoice_created",
            changed_by=_user_or_none(user),
            note=invoice.invoice_number,
        )
        create_lifecycle_from_invoice(invoice, user=user)
        return invoice, True


def create_or_link_production_order_from_invoice(invoice, user=None):
    costing = invoice.costing_header
    if not costing:
        raise CostingWorkflowError("This invoice is not linked to an approved costing.")
    if costing.status != "approved":
        raise CostingWorkflowError("The linked costing must be approved before production conversion.")

    with transaction.atomic():
        invoice = Invoice.objects.select_for_update().select_related("costing_header", "order").get(pk=invoice.pk)
        costing = invoice.costing_header

        order = invoice.order
        if not order:
            order = ProductionOrder.objects.filter(costing_header=costing).order_by("-created_at", "-id").first()
        if not order and costing.opportunity_id:
            order = ProductionOrder.objects.filter(opportunity=costing.opportunity).order_by("-created_at", "-id").first()

        created = False
        if not order:
            title = costing.style_name or costing.style_code or f"{costing.opportunity.opportunity_id} production"
            for _attempt in range(5):
                try:
                    order = ProductionOrder.objects.create(
                        opportunity=costing.opportunity,
                        lead=costing.opportunity.lead if costing.opportunity_id else None,
                        customer=invoice.customer or costing.customer,
                        costing_header=costing,
                        title=title,
                        order_code=_next_order_code(),
                        factory_location="ca" if costing.factory_location == "ca" else "bd",
                        order_type="fob",
                        qty_total=costing.order_quantity or 0,
                        style_name=costing.style_name or "",
                        color_info="",
                        notes=f"Created from invoice {invoice.invoice_number} and quotation {costing.quotation_number or 'COST-' + str(costing.pk)}.",
                    )
                    created = True
                    break
                except IntegrityError:
                    continue
            if not order:
                raise CostingWorkflowError("Could not create a unique production order number.")
        elif not order.costing_header_id:
            order.costing_header = costing
            order.save(update_fields=["costing_header", "updated_at"])

        if invoice.order_id != order.pk:
            invoice.order = order
            invoice.save(update_fields=["order", "updated_at"])

        opportunity = costing.opportunity
        if opportunity and opportunity.stage != "Production":
            opportunity.stage = "Production"
            opportunity.save(update_fields=["stage", "updated_at"])

        CostingAuditLog.objects.create(
            costing=costing,
            action="production_created",
            changed_by=_user_or_none(user),
            note=order.order_code or str(order.pk),
        )
        create_lifecycle_from_production(order, user=user)
        return order, created


def build_production_profit_snapshot(order):
    invoices = list(order.invoices.all().order_by("-issue_date", "-created_at", "-id"))
    invoice_total = sum((_d(invoice.total_amount) for invoice in invoices), Decimal("0"))
    paid_total = sum((_d(invoice.paid_amount) for invoice in invoices), Decimal("0"))
    balance_total = sum((_d(invoice.balance) for invoice in invoices), Decimal("0"))

    standard_cost = Decimal("0")
    costing = getattr(order, "costing_header", None)
    if costing:
        calc = compute_costing(costing.id)
        if calc:
            standard_cost = _d(calc.get("total_cost_order"))

    actual_cost = _d(
        ActualCostEntry.objects.filter(production_order=order).aggregate(total=Sum("actual_total_cost")).get("total")
    )
    estimated_profit = invoice_total - standard_cost if standard_cost > 0 else Decimal("0")
    currency = invoices[0].currency if invoices else (costing.currency if costing else "")
    can_compare_actuals = (currency or "").upper() == "BDT"
    actual_profit = invoice_total - actual_cost if actual_cost > 0 and can_compare_actuals else None
    margin_basis = actual_profit if actual_profit is not None else estimated_profit
    margin = (margin_basis / invoice_total) * Decimal("100") if invoice_total > 0 else Decimal("0")

    return {
        "invoices": invoices,
        "invoice_total": invoice_total,
        "paid_total": paid_total,
        "balance_total": balance_total,
        "standard_cost": standard_cost,
        "actual_cost": actual_cost,
        "estimated_profit": estimated_profit,
        "actual_profit": actual_profit,
        "has_actual_profit": actual_profit is not None,
        "can_compare_actuals": can_compare_actuals,
        "margin": margin,
        "currency": currency,
        "actual_cost_currency": "BDT",
        "display": {
            "invoice_total": _money(invoice_total),
            "paid_total": _money(paid_total),
            "balance_total": _money(balance_total),
            "standard_cost": _money(standard_cost),
            "actual_cost": _money(actual_cost),
            "estimated_profit": _money(estimated_profit),
            "actual_profit": _money(actual_profit) if actual_profit is not None else None,
            "margin": _money(margin),
        },
    }
