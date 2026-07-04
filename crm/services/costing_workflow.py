from decimal import Decimal, ROUND_HALF_UP

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from crm.models import ActualCostEntry, CostingAuditLog, CostingHeader, Invoice, ProductionOrder, QuickCosting
from crm.services.costing_currency import (
    CurrencyConversionError,
    convert_currency,
    normalize_costing_currency,
)
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


def _invoice_region_for_costing(costing):
    currency = (costing.currency or "").upper()
    if currency == "BDT" or costing.factory_location == "bd":
        return "BD"
    return "CA"


def _quick_costing_market_and_currency(quick_costing):
    if quick_costing.is_bangladesh_local_sewing:
        return "bangladesh", "BDT"
    opportunity = getattr(quick_costing, "opportunity", None)
    lead = getattr(opportunity, "lead", None) if opportunity else None
    customer = None
    if opportunity:
        customer = getattr(opportunity, "customer", None)
    if not customer and lead:
        customer = getattr(lead, "customer", None)
    market_hint = (getattr(lead, "market", "") or "").upper()
    country = (getattr(customer, "country", "") or "").lower().strip() if customer else ""
    if market_hint == "BD" or "bangladesh" in country:
        return "bangladesh", "BDT"
    source_currency = normalize_costing_currency(quick_costing.currency)
    return "north_america", "USD" if source_currency == "USD" else "CAD"


def _quick_money_for_invoice(value, source_currency, target_currency, exchange_rate):
    value = _d(value)
    source_currency = normalize_costing_currency(source_currency)
    target_currency = normalize_costing_currency(target_currency)
    try:
        return convert_currency(
            value,
            source_currency,
            target_currency,
            bdt_per_cad=exchange_rate,
        )
    except CurrencyConversionError as exc:
        raise CostingWorkflowError("Currency conversion rate is required before creating this invoice.") from exc


def get_costing_quote_amounts(costing):
    calc = compute_costing(costing)
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
            invoice_market="bangladesh" if _invoice_region_for_costing(costing) == "BD" else "north_america",
            invoice_type="bulk",
            deposit_percentage=Decimal("50.00"),
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


def create_invoice_from_quick_costing(quick_costing, user=None):
    with transaction.atomic():
        quick_costing = (
            QuickCosting.objects.select_for_update()
            .select_related(
                "opportunity", "opportunity__lead", "opportunity__customer",
                "opportunity__lead__customer", "production_order",
            )
            .get(pk=quick_costing.pk)
        )

        existing = Invoice.objects.filter(quick_costing=quick_costing).order_by("-created_at", "-id").first()
        if existing:
            create_lifecycle_from_invoice(existing, user=user)
            return existing, False
        if quick_costing.status not in {QuickCosting.STATUS_APPROVED, QuickCosting.STATUS_QUOTED}:
            raise CostingWorkflowError("Approve the quick costing quotation before creating an invoice.")
        if not quick_costing.quotation_number or not quick_costing.quoted_at:
            raise CostingWorkflowError("Create a quotation before creating an invoice.")

        summary = quick_costing.calculation_summary()
        market, currency = _quick_costing_market_and_currency(quick_costing)
        region = "BD" if market == "bangladesh" else "CA"
        source_currency = normalize_costing_currency(summary.get("currency"))
        exchange_rate = summary.get("exchange_rate")
        subtotal = _quick_money_for_invoice(
            summary.get("revenue"), source_currency, currency, exchange_rate
        )
        shipping = Decimal("0") if quick_costing.is_bangladesh_local_sewing else _quick_money_for_invoice(
            summary.get("shipping_cost_total"), source_currency, currency, exchange_rate
        )
        total = _money(subtotal + shipping)
        invoice_type = (
            "sewing_charge"
            if quick_costing.is_bangladesh_local_sewing
            else ("sample" if quick_costing.costing_purpose == QuickCosting.PURPOSE_SAMPLE else "bulk")
        )
        deposit_percentage = (
            Decimal("0")
            if invoice_type == "sewing_charge"
            else (Decimal("100.00") if invoice_type == "sample" else Decimal("50.00"))
        )
        opportunity = quick_costing.opportunity
        lead = getattr(opportunity, "lead", None) if opportunity else None
        customer = None
        if opportunity:
            customer = getattr(opportunity, "customer", None)
        if not customer and lead:
            customer = getattr(lead, "customer", None)
        production_order = getattr(quick_costing, "production_order", None)
        if quick_costing.is_bangladesh_local_sewing and not production_order:
            raise CostingWorkflowError(
                "Create the approved Bangladesh Local Sewing production order before invoicing."
            )
        if not customer and production_order:
            customer = production_order.customer

        today = timezone.localdate()
        invoice = Invoice.objects.create(
            quick_costing=quick_costing,
            order=production_order,
            customer=customer,
            invoice_number=_next_invoice_number(),
            issue_date=today,
            due_date=today + timezone.timedelta(days=14),
            currency=currency,
            invoice_region=region,
            invoice_market=market,
            invoice_type=invoice_type,
            deposit_percentage=deposit_percentage,
            subtotal=subtotal,
            shipping_amount=shipping,
            discount_amount=Decimal("0"),
            tax_amount=Decimal("0"),
            total_amount=total,
            paid_amount=Decimal("0"),
            status="sent",
            notes=(
                f"Bangladesh Local Sewing · CMT / Sewing Charge · {quick_costing.quotation_number}"
                if quick_costing.is_bangladesh_local_sewing
                else f"Converted from quick quotation {quick_costing.quotation_number or 'QC-' + str(quick_costing.pk)}."
            ),
            sewing_charge=Decimal("0"),
            other_internal_cost=Decimal("0"),
            internal_cost_note="",
        )
        quick_costing.status = QuickCosting.STATUS_INVOICED
        quick_costing.save(update_fields=["status", "updated_at"])
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
            try:
                order = ProductionOrder.objects.create(
                    opportunity=costing.opportunity,
                    lead=costing.opportunity.lead if costing.opportunity_id else None,
                    customer=invoice.customer or costing.customer,
                    costing_header=costing,
                    title=title,
                    factory_location="ca" if costing.factory_location == "ca" else "bd",
                    order_type="fob",
                    qty_total=costing.order_quantity or 0,
                    style_name=costing.style_name or "",
                    color_info="",
                    notes=f"Created from invoice {invoice.invoice_number} and quotation {costing.quotation_number or 'COST-' + str(costing.pk)}.",
                )
                created = True
            except IntegrityError as exc:
                raise CostingWorkflowError("Could not create a unique production order number.") from exc
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
    invoices = list(
        order.invoices.select_related("quick_costing").order_by("-issue_date", "-created_at", "-id")
    )
    totals_by_currency = {}
    for invoice in invoices:
        code = (invoice.currency or "CAD").upper().strip()
        totals = totals_by_currency.setdefault(
            code,
            {"currency": code, "invoice_total": Decimal("0"), "paid_total": Decimal("0"), "balance_total": Decimal("0")},
        )
        totals["invoice_total"] += _d(invoice.total_amount)
        totals["paid_total"] += _d(invoice.paid_amount)
        totals["balance_total"] += _d(invoice.balance)
    invoice_currency_rows = [
        totals_by_currency[code]
        for code in ("CAD", "USD", "BDT")
        if code in totals_by_currency
    ]
    single_currency = invoice_currency_rows[0] if len(invoice_currency_rows) == 1 else None
    invoice_total = single_currency["invoice_total"] if single_currency else Decimal("0")
    paid_total = single_currency["paid_total"] if single_currency else Decimal("0")
    balance_total = single_currency["balance_total"] if single_currency else Decimal("0")
    currency = single_currency["currency"] if single_currency else ""

    standard_cost = Decimal("0")
    standard_cost_currency = (order.approved_currency or "").upper().strip()
    approved_summary = order.approved_costing_summary or {}
    if approved_summary.get("total_cost_order") not in (None, ""):
        standard_cost = _d(approved_summary.get("total_cost_order"))
    costing = getattr(order, "costing_header", None)
    if costing:
        calc = compute_costing(costing.id)
        if calc:
            if standard_cost <= 0:
                standard_cost = _d(calc.get("total_cost_order"))
            standard_cost += max(
                _d(calc.get("total_final_offer_order")) - _d(calc.get("total_sales_order")),
                Decimal("0"),
            )
            standard_cost_currency = (costing.currency or "BDT").upper().strip()

    quick_invoice = next((invoice for invoice in invoices if invoice.quick_costing_id), None)
    if quick_invoice and len(invoices) == 1:
        summary = quick_invoice.quick_costing.calculation_summary()
        source_currency = normalize_costing_currency(summary.get("currency"))
        try:
            converted_cost = _quick_money_for_invoice(
                _d(summary.get("total_cost")) + _d(summary.get("commission_total")),
                source_currency,
                currency,
                summary.get("exchange_rate"),
            ) if currency else None
        except CostingWorkflowError:
            converted_cost = None
        if converted_cost is not None:
            standard_cost = converted_cost
            standard_cost_currency = currency
        else:
            standard_cost = _d(summary.get("total_cost")) + _d(summary.get("commission_total"))
            standard_cost_currency = source_currency

    actual_cost = _d(
        ActualCostEntry.objects.filter(production_order=order).aggregate(total=Sum("actual_total_cost")).get("total")
    )
    comparison_reason = ""
    can_compare_standard = bool(
        single_currency and standard_cost > 0 and standard_cost_currency == currency
    )
    if len(invoice_currency_rows) > 1:
        comparison_reason = "Not comparable: linked invoices use multiple currencies."
    elif standard_cost > 0 and standard_cost_currency != currency:
        comparison_reason = "Not comparable: approved costing and invoice currencies differ."
    elif standard_cost <= 0:
        comparison_reason = "Not comparable: no approved costing snapshot is available."
    estimated_profit = invoice_total - standard_cost if can_compare_standard else None
    can_compare_actuals = bool(single_currency and currency == "BDT")
    actual_profit = invoice_total - actual_cost if actual_cost > 0 and can_compare_actuals else None
    margin_basis = actual_profit if actual_profit is not None else estimated_profit
    margin = (margin_basis / invoice_total) * Decimal("100") if margin_basis is not None and invoice_total > 0 else None

    return {
        "invoices": invoices,
        "invoice_currency_rows": invoice_currency_rows,
        "invoice_total": invoice_total,
        "paid_total": paid_total,
        "balance_total": balance_total,
        "standard_cost": standard_cost,
        "actual_cost": actual_cost,
        "estimated_profit": estimated_profit,
        "actual_profit": actual_profit,
        "has_actual_profit": actual_profit is not None,
        "can_compare_standard": can_compare_standard,
        "can_compare_actuals": can_compare_actuals,
        "comparison_reason": comparison_reason,
        "margin": margin,
        "currency": currency,
        "actual_cost_currency": "BDT",
        "display": {
            "invoice_total": _money(invoice_total),
            "paid_total": _money(paid_total),
            "balance_total": _money(balance_total),
            "standard_cost": _money(standard_cost),
            "actual_cost": _money(actual_cost),
            "estimated_profit": _money(estimated_profit) if estimated_profit is not None else None,
            "actual_profit": _money(actual_profit) if actual_profit is not None else None,
            "margin": _money(margin) if margin is not None else None,
        },
    }
