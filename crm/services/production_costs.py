from django.db import transaction
from django.utils import timezone

from crm.models import ExpenseRecord, ProductionCostRecord, SupplierBill
from crm.services.financial_currency import money


class ProductionCostError(ValueError):
    pass


def _actor(actor):
    if not actor or not getattr(actor, "is_authenticated", False):
        raise ProductionCostError("An authenticated actor is required.")
    return actor


@transaction.atomic
def approve_production_cost(cost, *, actor):
    approver = _actor(actor)
    locked = ProductionCostRecord.objects.select_for_update().select_related(
        "production_order__customer", "production_order__opportunity", "supplier_bill", "expense"
    ).get(pk=cost.pk)
    if locked.approval_status == SupplierBill.APPROVAL_APPROVED:
        return locked
    if locked.approval_status not in (SupplierBill.APPROVAL_DRAFT, SupplierBill.APPROVAL_PENDING):
        raise ProductionCostError("Only a draft or pending production cost can be approved.")
    if locked.actual_amount <= 0:
        raise ProductionCostError("Approved production actual cost must be greater than zero.")
    source = locked.supplier_bill or locked.expense
    if not source:
        raise ProductionCostError("Production actual cost requires an approved supplier bill or expense source.")
    if locked.supplier_bill_id:
        if locked.supplier_bill.approval_status != SupplierBill.APPROVAL_APPROVED:
            raise ProductionCostError("Linked supplier bill is not approved.")
        source_total = locked.supplier_bill.total_amount
        source_currency = locked.supplier_bill.currency
        rate_to_cad = locked.supplier_bill.rate_to_cad
        rate_to_bdt = locked.supplier_bill.rate_to_bdt
    else:
        if locked.expense.approval_status != ExpenseRecord.APPROVAL_APPROVED:
            raise ProductionCostError("Linked expense is not approved.")
        source_total = locked.expense.total_amount
        source_currency = locked.expense.currency
        rate_to_cad = locked.expense.rate_to_cad
        rate_to_bdt = locked.expense.rate_to_bdt
    if locked.currency != source_currency:
        raise ProductionCostError("Production cost and supporting source must use the same original currency.")
    if money(locked.actual_amount) > money(source_total):
        raise ProductionCostError("Production cost cannot exceed its supporting bill or expense amount.")
    now = timezone.now()
    ProductionCostRecord.objects.filter(pk=locked.pk).update(
        customer=locked.customer or locked.production_order.customer,
        opportunity=locked.opportunity or locked.production_order.opportunity,
        rate_to_cad=rate_to_cad,
        rate_to_bdt=rate_to_bdt,
        approval_status=SupplierBill.APPROVAL_APPROVED,
        submitted_by=locked.submitted_by or locked.created_by or approver,
        submitted_at=locked.submitted_at or now,
        approved_by=approver,
        approved_at=now,
        modified_by=approver,
        modified_at=now,
    )
    locked.refresh_from_db()
    return locked
