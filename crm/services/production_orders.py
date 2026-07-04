from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from crm.models import CostingAuditLog, CostingHeader, ProductionOrder, ProductionStage, QuickCosting
from crm.services.costing_workflow import CostingWorkflowError, get_costing_quote_amounts
from crm.services.order_lifecycle import create_lifecycle_from_production


class ProductionOrderCreationError(Exception):
    pass


def create_production_order_from_approved_quick_costing(quick_costing, user=None):
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
        if quick_costing.status != QuickCosting.STATUS_APPROVED or not quick_costing.approved_at:
            raise ProductionOrderCreationError("CEO approval is required before production can begin.")
        if quick_costing.currency != "BDT":
            raise ProductionOrderCreationError("Bangladesh Local Sewing must use BDT.")
        if not quick_costing.sewing_charge_per_piece_bdt or quick_costing.sewing_charge_per_piece_bdt <= 0:
            raise ProductionOrderCreationError("A positive sewing charge is required before production.")

        existing = ProductionOrder.objects.filter(source_quick_costing=quick_costing).first()
        if existing:
            return existing, False

        summary = quick_costing.calculation_summary()
        opportunity = quick_costing.opportunity
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
            if existing:
                return existing, False
            raise ProductionOrderCreationError("Could not create the local sewing production order.") from exc

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


def create_production_order_from_approved_quotation(costing, user=None):
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
            for field_name, value in snapshot.items():
                setattr(legacy_order, field_name, value)
            legacy_order.save(update_fields=[*snapshot.keys(), "updated_at"])
            create_lifecycle_from_production(legacy_order, user=user)
            return legacy_order, False

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
            note=order.order_code or str(order.pk),
        )
        create_lifecycle_from_production(order, user=user)
        return order, True
