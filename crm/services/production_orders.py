from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from crm.models import CostingAuditLog, CostingHeader, Invoice, ProductionOrder, ProductionStage, QuickCosting
from crm.services.costing_workflow import CostingWorkflowError, get_costing_quote_amounts
from crm.services.order_lifecycle import create_lifecycle_from_production
from crm.services.production_payment import select_production_payment_invoice


class ProductionOrderCreationError(Exception):
    pass


QUICK_COSTING_PRODUCTION_SOURCE_STATUSES = (
    QuickCosting.STATUS_APPROVED,
    QuickCosting.STATUS_QUOTED,
    QuickCosting.STATUS_INVOICED,
    QuickCosting.STATUS_PRODUCTION,
)


def is_quick_costing_production_source(quick_costing):
    return bool(
        quick_costing
        and quick_costing.status in QUICK_COSTING_PRODUCTION_SOURCE_STATUSES
        and quick_costing.approved_at
    )


def _invoice_candidates_for_quick_costing(quick_costing, invoice=None):
    invoice_pk = getattr(invoice, "pk", None)
    invoices = Invoice.objects.select_for_update().filter(quick_costing=quick_costing)
    if invoice_pk:
        invoices = invoices.filter(pk=invoice_pk)
    return invoices.order_by("-issue_date", "-created_at", "-id")


def _invoice_candidates_for_costing_header(costing, invoice=None):
    invoice_pk = getattr(invoice, "pk", None)
    query = Q(costing_header=costing)
    if getattr(costing, "opportunity_id", None):
        query |= Q(opportunity_id=costing.opportunity_id)
    invoices = Invoice.objects.select_for_update().filter(query)
    if invoice_pk:
        invoices = invoices.filter(pk=invoice_pk)
    return invoices.distinct().order_by("-issue_date", "-created_at", "-id")


def _production_invoice_for_costing_header(costing, invoice=None):
    return select_production_payment_invoice(_invoice_candidates_for_costing_header(costing, invoice=invoice))


def _invoice_candidates_for_cmt_quick_costing(quick_costing, invoice=None):
    invoice_pk = getattr(invoice, "pk", None)
    query = Q(quick_costing=quick_costing)
    if getattr(quick_costing, "opportunity_id", None):
        query |= Q(opportunity_id=quick_costing.opportunity_id)
    invoices = Invoice.objects.select_for_update().filter(query)
    if invoice_pk:
        invoices = invoices.filter(pk=invoice_pk)
    return invoices.distinct().order_by("-issue_date", "-created_at", "-id")


def _production_invoice_for_cmt_quick_costing(quick_costing, invoice=None):
    return select_production_payment_invoice(_invoice_candidates_for_cmt_quick_costing(quick_costing, invoice=invoice))


def full_package_quick_costing_source_for_opportunity(opportunity):
    """Return the newest Full Package Quick Costing source and newest invoice for an opportunity."""
    if not opportunity or getattr(opportunity, "is_archived", False):
        return None, None
    quick_costings = (
        QuickCosting.objects.filter(
            opportunity=opportunity,
            approved_at__isnull=False,
            status__in=QUICK_COSTING_PRODUCTION_SOURCE_STATUSES,
        )
        .select_related("opportunity", "opportunity__lead", "opportunity__customer", "opportunity__lead__customer")
        .prefetch_related("invoices")
        .order_by("-updated_at", "-id")
    )
    for quick_costing in quick_costings:
        if quick_costing.effective_pricing_type != QuickCosting.PRICING_FULL_PACKAGE:
            continue
        if not quick_costing.is_latest_revision:
            continue
        invoice = quick_costing.invoices.order_by("-issue_date", "-created_at", "-id").first()
        return quick_costing, invoice
    return None, None


def paid_full_package_quick_costing_source_for_opportunity(opportunity):
    """Return the newest invoice-backed Full Package Quick Costing source for an opportunity."""
    quick_costing, invoice = full_package_quick_costing_source_for_opportunity(opportunity)
    if quick_costing and select_production_payment_invoice([invoice])[0]:
        return quick_costing, invoice
    return None, None


def _quick_costing_customer(quick_costing):
    opportunity = quick_costing.opportunity
    lead = getattr(opportunity, "lead", None) if opportunity else None
    customer = getattr(opportunity, "customer", None) if opportunity else None
    if not customer and lead:
        customer = getattr(lead, "customer", None)
    return customer


def _quick_costing_approved_summary(quick_costing, summary, invoice):
    return {
        "pricing_type": quick_costing.effective_pricing_type,
        "service_type": quick_costing.service_type_label,
        "currency": quick_costing.currency or "",
        "quantity": quick_costing.quantity,
        "selling_price_per_piece": _decimal_text(quick_costing.selling_price_per_piece),
        "sales_value": _decimal_text(summary.get("sales_value")),
        "net_revenue": _decimal_text(summary.get("net_revenue")),
        "product_production_cost_total": _decimal_text(summary.get("product_production_cost_total")),
        "shipping_cost_total": _decimal_text(summary.get("shipping_cost_total")),
        "other_expenses_total": _decimal_text(summary.get("other_expenses_total")),
        "gross_profit_total": _decimal_text(summary.get("gross_profit_total")),
        "commission_total": _decimal_text(summary.get("commission_total")),
        "net_profit_total": _decimal_text(summary.get("net_profit_total")),
        "invoice_number": getattr(invoice, "invoice_number", ""),
        "invoice_total": _decimal_text(getattr(invoice, "total_amount", Decimal("0"))),
    }


def create_production_order_from_paid_full_package_quick_costing(
    quick_costing,
    invoice=None,
    user=None,
):
    """Create or link one ProductionOrder for an invoice-backed Full Package Quick Costing."""
    if not quick_costing.pk:
        raise ProductionOrderCreationError("The Quick Costing must be saved before production can begin.")

    with transaction.atomic():
        quick_costing = (
            QuickCosting.objects.select_for_update()
            .select_related("opportunity", "opportunity__lead", "opportunity__customer", "opportunity__lead__customer", "salesperson")
            .get(pk=quick_costing.pk)
        )
        if quick_costing.effective_pricing_type != QuickCosting.PRICING_FULL_PACKAGE:
            raise ProductionOrderCreationError("Only Full Package Quick Costing can use this production workflow.")
        if not is_quick_costing_production_source(quick_costing):
            raise ProductionOrderCreationError("CEO-approved Quick Costing is required before production conversion.")
        if not quick_costing.is_latest_revision:
            raise ProductionOrderCreationError("Only the latest Quick Costing revision can move to Production.")

        opportunity = quick_costing.opportunity
        if not opportunity:
            raise ProductionOrderCreationError("The Quick Costing must be linked to an opportunity.")
        if getattr(opportunity, "is_archived", False):
            raise ProductionOrderCreationError("Archived opportunities cannot move to Production.")

        existing = ProductionOrder.objects.select_for_update().filter(source_quick_costing=quick_costing).first()
        if not existing:
            existing = (
                ProductionOrder.objects.select_for_update()
                .filter(opportunity=opportunity)
                .order_by("created_at", "id")
                .first()
            )
        if existing:
            production_invoice, _payment_check = select_production_payment_invoice(
                _invoice_candidates_for_quick_costing(quick_costing, invoice=invoice)
            )
            if production_invoice and production_invoice.order_id != existing.pk:
                production_invoice.order = existing
                production_invoice.save(update_fields=["order", "updated_at"])
            create_lifecycle_from_production(existing, user=user)
            return existing, False

        production_invoice, payment_check = select_production_payment_invoice(
            _invoice_candidates_for_quick_costing(quick_costing, invoice=invoice)
        )
        if not production_invoice:
            raise ProductionOrderCreationError(payment_check["message"])

        summary = quick_costing.calculation_summary()
        lead = getattr(opportunity, "lead", None)
        customer = _quick_costing_customer(quick_costing) or getattr(production_invoice, "customer", None)
        total_value = getattr(production_invoice, "total_amount", None) or summary.get("sales_value") or summary.get("revenue")
        title = quick_costing.project_name or quick_costing.quotation_number or f"Quick Costing {quick_costing.pk}"
        quantity = int(quick_costing.quantity or 0)
        try:
            order = ProductionOrder.objects.create(
                source_quick_costing=quick_costing,
                opportunity=opportunity,
                lead=lead,
                customer=customer,
                title=title,
                factory_location="bd",
                order_type="fob",
                production_order_type="sampling" if quantity <= 5 else "bulk",
                qty_total=quantity,
                style_name=quick_costing.project_name or "",
                completed_quantity=0,
                quotation_number_snapshot=quick_costing.quotation_number or "",
                client_name_snapshot=quick_costing.buyer_name,
                brand_name_snapshot=quick_costing.account_brand,
                product_name_snapshot=quick_costing.project_name,
                product_type_snapshot=quick_costing.product_type,
                approved_currency=quick_costing.currency or getattr(production_invoice, "currency", "") or "CAD",
                approved_selling_price=quick_costing.selling_price_per_piece,
                approved_total_value=total_value,
                approved_costing_summary=_quick_costing_approved_summary(quick_costing, summary, production_invoice),
                approved_price_locked_at=quick_costing.approved_at,
                assigned_production_manager=quick_costing.salesperson or getattr(opportunity, "assigned_to", None),
                created_by=_user_or_none(user),
                operational_status="planning",
                notes=f"Automatically created from Quick Costing invoice {production_invoice.invoice_number}.",
            )
        except (IntegrityError, ValueError) as exc:
            existing = ProductionOrder.objects.filter(source_quick_costing=quick_costing).first()
            if not existing:
                existing = ProductionOrder.objects.filter(opportunity=opportunity).order_by("created_at", "id").first()
            if existing:
                if production_invoice.order_id != existing.pk:
                    production_invoice.order = existing
                    production_invoice.save(update_fields=["order", "updated_at"])
                create_lifecycle_from_production(existing, user=user)
                return existing, False
            raise ProductionOrderCreationError("Could not create the Full Package production order.") from exc

        if production_invoice.order_id != order.pk:
            production_invoice.order = order
            production_invoice.save(update_fields=["order", "updated_at"])
        create_lifecycle_from_production(order, user=user)
        return order, True


def create_production_order_from_approved_quick_costing(
    quick_costing,
    user=None,
    *,
    invoice=None,
):
    """Create the single Bangladesh Local Sewing order for an approved CMT Quick Costing."""
    if not quick_costing.pk:
        raise ProductionOrderCreationError("The Quick Costing must be saved before production can begin.")

    with transaction.atomic():
        quick_costing = (
            QuickCosting.objects.select_for_update()
            .select_related("opportunity", "opportunity__lead", "opportunity__customer", "opportunity__lead__customer")
            .get(pk=quick_costing.pk)
        )
        if quick_costing.effective_pricing_type != QuickCosting.PRICING_CMT:
            raise ProductionOrderCreationError("Only CMT / Sewing Only Quick Costing creates local sewing production.")
        if not is_quick_costing_production_source(quick_costing):
            raise ProductionOrderCreationError("CEO approval is required before production can begin.")
        if not quick_costing.is_latest_revision:
            raise ProductionOrderCreationError("Only the latest Quick Costing revision can move to Production.")
        opportunity = quick_costing.opportunity
        if opportunity and getattr(opportunity, "is_archived", False):
            raise ProductionOrderCreationError("Archived opportunities cannot move to Production.")
        if quick_costing.currency != "BDT":
            raise ProductionOrderCreationError("Bangladesh Local Sewing must use BDT.")
        if not quick_costing.sewing_charge_per_piece_bdt or quick_costing.sewing_charge_per_piece_bdt <= 0:
            raise ProductionOrderCreationError("A positive sewing charge is required before production.")

        existing = ProductionOrder.objects.filter(source_quick_costing=quick_costing).first()
        if not existing and opportunity:
            existing = ProductionOrder.objects.filter(opportunity=opportunity).order_by("created_at", "id").first()
        if existing:
            production_invoice, _payment_check = _production_invoice_for_cmt_quick_costing(quick_costing, invoice=invoice)
            if production_invoice and production_invoice.order_id != existing.pk:
                production_invoice.order = existing
                production_invoice.save(update_fields=["order", "updated_at"])
            create_lifecycle_from_production(existing, user=user)
            return existing, False

        production_invoice, payment_check = _production_invoice_for_cmt_quick_costing(quick_costing, invoice=invoice)
        if not production_invoice:
            raise ProductionOrderCreationError(payment_check["message"])

        summary = quick_costing.calculation_summary()
        lead = getattr(opportunity, "lead", None) if opportunity else None
        customer = getattr(opportunity, "customer", None) if opportunity else None
        if not customer and lead:
            customer = getattr(lead, "customer", None)
        approved_summary = {
            "pricing_type": quick_costing.effective_pricing_type,
            "service_type": quick_costing.service_type_label,
            "sewing_charge_per_piece_bdt": str(quick_costing.sewing_charge_per_piece_bdt or Decimal("0")),
            "sewing_cost_per_piece_bdt": (
                str(quick_costing.sewing_cost_per_piece_bdt)
                if quick_costing.sewing_cost_per_piece_bdt is not None
                else None
            ),
            "extra_local_cost_bdt": str(quick_costing.extra_local_cost_bdt or Decimal("0")),
            "total_sewing_revenue_bdt": str(summary["revenue"]),
            "total_sewing_cost_bdt": str(summary["total_cost"]) if summary["cost_available"] else None,
        }
        try:
            order = ProductionOrder.objects.create(
                source_quick_costing=quick_costing,
                opportunity=opportunity,
                lead=lead,
                customer=customer,
                title=quick_costing.project_name,
                factory_location="bd",
                order_type="sewing_charge",
                production_order_type="bulk",
                qty_total=quick_costing.quantity,
                style_name=quick_costing.project_name,
                sewing_charge_per_piece_bdt=quick_costing.sewing_charge_per_piece_bdt,
                sewing_cost_per_piece_bdt=quick_costing.sewing_cost_per_piece_bdt,
                extra_local_cost_bdt=quick_costing.extra_local_cost_bdt,
                completed_quantity=0,
                quotation_number_snapshot=quick_costing.quotation_number or "",
                client_name_snapshot=quick_costing.buyer_name,
                brand_name_snapshot=quick_costing.account_brand,
                product_name_snapshot=quick_costing.project_name,
                product_type_snapshot=quick_costing.product_type,
                approved_currency="BDT",
                approved_selling_price=quick_costing.sewing_charge_per_piece_bdt,
                approved_total_value=summary["revenue"],
                approved_costing_summary=approved_summary,
                approved_price_locked_at=quick_costing.approved_at,
                created_by=_user_or_none(user),
            )
        except (IntegrityError, ValueError) as exc:
            existing = ProductionOrder.objects.filter(source_quick_costing=quick_costing).first()
            if not existing and opportunity:
                existing = ProductionOrder.objects.filter(opportunity=opportunity).order_by("created_at", "id").first()
            if existing:
                if production_invoice and production_invoice.order_id != existing.pk:
                    production_invoice.order = existing
                    production_invoice.save(update_fields=["order", "updated_at"])
                create_lifecycle_from_production(existing, user=user)
                return existing, False
            raise ProductionOrderCreationError("Could not create the local sewing production order.") from exc

        if production_invoice and production_invoice.order_id != order.pk:
            production_invoice.order = order
            production_invoice.save(update_fields=["order", "updated_at"])
        ProductionStage.objects.get_or_create(
            order=order,
            stage_key="sewing",
            defaults={"display_name": "Sewing"},
        )
        create_lifecycle_from_production(order, user=user)
        return order, True


def _user_or_none(user):
    return user if user and getattr(user, "is_authenticated", False) else None


def _decimal_text(value):
    return str(value if value is not None else Decimal("0"))


def _customer_name(costing):
    customer = costing.customer
    lead = costing.opportunity.lead if costing.opportunity_id else None
    return (
        costing.buyer
        or getattr(customer, "contact_name", "")
        or getattr(lead, "contact_name", "")
        or "Client"
    )


def _brand_name(costing):
    customer = costing.customer
    lead = costing.opportunity.lead if costing.opportunity_id else None
    return (
        costing.brand
        or getattr(customer, "account_brand", "")
        or getattr(lead, "account_brand", "")
        or ""
    )


def _product_name(costing):
    return costing.style_name or costing.style_code or costing.get_product_type_display()


def _approved_costing_summary(amounts):
    calc = amounts["calc"]
    return {
        "total_cost_per_piece": _decimal_text(calc.get("total_cost_per_piece")),
        "total_cost_order": _decimal_text(amounts["standard_cost_total"]),
        "selling_price_per_piece": _decimal_text(amounts["unit_price"]),
        "total_order_value": _decimal_text(amounts["order_total"]),
        "breakdown_per_piece": {
            key: _decimal_text(value)
            for key, value in (calc.get("breakdown") or {}).items()
        },
    }


def _snapshot_values(costing, amounts, user):
    opportunity = costing.opportunity
    lead = opportunity.lead if opportunity else None
    return {
        "source_quotation": costing,
        "costing_header": costing,
        "quotation_number_snapshot": costing.quotation_number,
        "client_name_snapshot": _customer_name(costing),
        "brand_name_snapshot": _brand_name(costing),
        "product_name_snapshot": _product_name(costing),
        "product_type_snapshot": costing.product_type or "",
        "approved_currency": costing.currency or "BDT",
        "approved_selling_price": amounts["unit_price"],
        "approved_total_value": amounts["order_total"],
        "approved_costing_summary": _approved_costing_summary(amounts),
        "approved_price_locked_at": costing.quotation_approved_at or timezone.now(),
        "assigned_production_manager": getattr(lead, "assigned_to", None),
        "created_by": _user_or_none(user),
    }


def create_production_order_from_approved_quotation(costing, user=None, *, invoice=None):
    """Create or link one ProductionOrder for an approved quotation."""
    if not costing.pk:
        raise ProductionOrderCreationError("The quotation must be saved before production can begin.")

    with transaction.atomic():
        costing = (
            CostingHeader.objects.select_for_update()
            .select_related("opportunity", "opportunity__lead", "customer")
            .get(pk=costing.pk)
        )
        if costing.quotation_status != CostingHeader.QUOTATION_STATUS_APPROVED:
            raise ProductionOrderCreationError("Approve the quotation before creating a production order.")
        if not costing.quotation_number:
            raise ProductionOrderCreationError("The approved quotation does not have a quotation number.")

        existing = ProductionOrder.objects.select_for_update().filter(source_quotation=costing).first()
        if existing:
            production_invoice, _payment_check = _production_invoice_for_costing_header(costing, invoice=invoice)
            if production_invoice and production_invoice.order_id != existing.pk:
                production_invoice.order = existing
                production_invoice.save(update_fields=["order", "updated_at"])
            create_lifecycle_from_production(existing, user=user)
            return existing, False

        try:
            amounts = get_costing_quote_amounts(costing)
        except CostingWorkflowError as exc:
            raise ProductionOrderCreationError(str(exc)) from exc

        snapshot = _snapshot_values(costing, amounts, user)
        legacy_order = (
            ProductionOrder.objects.select_for_update()
            .filter(costing_header=costing, source_quotation__isnull=True)
            .order_by("created_at", "id")
            .first()
        )
        if legacy_order:
            production_invoice, _payment_check = _production_invoice_for_costing_header(costing, invoice=invoice)
            for field_name, value in snapshot.items():
                setattr(legacy_order, field_name, value)
            legacy_order.save(update_fields=[*snapshot.keys(), "updated_at"])
            if production_invoice and production_invoice.order_id != legacy_order.pk:
                production_invoice.order = legacy_order
                production_invoice.save(update_fields=["order", "updated_at"])
            create_lifecycle_from_production(legacy_order, user=user)
            return legacy_order, False

        production_invoice, payment_check = _production_invoice_for_costing_header(costing, invoice=invoice)
        if not production_invoice:
            raise ProductionOrderCreationError(payment_check["message"])

        opportunity = costing.opportunity
        lead = opportunity.lead if opportunity else None
        title = costing.style_name or costing.style_code or f"{costing.quotation_number} production"
        try:
            with transaction.atomic():
                order = ProductionOrder.objects.create(
                    **snapshot,
                    opportunity=opportunity,
                    lead=lead,
                    customer=costing.customer or getattr(opportunity, "customer", None),
                    title=title,
                    factory_location="ca" if costing.factory_location == "ca" else "bd",
                    order_type="fob",
                    production_order_type="sampling" if amounts["quantity"] <= 5 else "bulk",
                    qty_total=amounts["quantity"],
                    style_name=costing.style_name or "",
                    operational_status="planning",
                    notes=f"Automatically created from approved quotation {costing.quotation_number}.",
                )
        except IntegrityError:
            order = ProductionOrder.objects.filter(source_quotation=costing).first()
            if not order:
                raise ProductionOrderCreationError(
                    "A production order could not be created for this approved quotation."
                )
            return order, False

        CostingAuditLog.objects.create(
            costing=costing,
            action="production_created",
            changed_by=_user_or_none(user),
            note=order.purchase_order_number or str(order.pk),
        )
        if production_invoice.order_id != order.pk:
            production_invoice.order = order
            production_invoice.save(update_fields=["order", "updated_at"])
        create_lifecycle_from_production(order, user=user)
        return order, True
