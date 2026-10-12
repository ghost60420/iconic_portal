"""One release gate for existing production entry points.

This service reads existing costing/invoice balances and stores explicit release
decisions in the existing audit history. It never writes payment or ledger data.
"""
import hashlib
import json

from django.db import transaction
from django.urls import reverse

from crm.models import CRMAuditLog, CostingHeader, CostingSMV, Invoice, Opportunity, ProductionOrder, QuickCosting
from crm.services.operations_permissions import ROLE_CEO, can_approve_costing, has_operations_role
from crm.services.production_payment import invoice_queryset_for_opportunity, production_payment_requirement


APPROVAL_FIELD = "production_release_approval"
WAIVABLE_REQUIREMENTS = frozenset({"costing", "quotation", "payment"})
QUICK_APPROVED_STATUSES = {
    QuickCosting.STATUS_APPROVED, QuickCosting.STATUS_QUOTED,
    QuickCosting.STATUS_INVOICED, QuickCosting.STATUS_PRODUCTION,
}


class ProductionApprovalError(ValueError):
    pass


def can_approve_production_exception(user):
    return bool(user and getattr(user, "is_active", False) and has_operations_role(user, ROLE_CEO))


def _can_release(user):
    return bool(user and getattr(user, "is_active", False) and can_approve_costing(user))


def _source_for_invoice(invoice):
    if not invoice:
        return None
    if invoice.quick_costing_id:
        return invoice.quick_costing
    if invoice.costing_header_id:
        return invoice.costing_header
    return None


def _select_records(opportunity, source, invoice):
    if invoice is None:
        invoices = invoice_queryset_for_opportunity(opportunity)
        if isinstance(source, QuickCosting):
            invoices = invoices.filter(quick_costing_id=source.pk)
        elif isinstance(source, CostingHeader):
            invoices = invoices.filter(costing_header_id=source.pk)
        invoice = invoices.first()
    if source is None:
        source = _source_for_invoice(invoice)
    if source is None and opportunity and opportunity.pk:
        quick = QuickCosting.objects.filter(opportunity=opportunity).order_by("-updated_at", "-pk").first()
        advanced = CostingHeader.objects.filter(opportunity=opportunity).order_by("-updated_at", "-pk").first()
        source = max([item for item in (quick, advanced) if item], key=lambda item: item.updated_at, default=None)
    return source, invoice


def _approval_snapshot(opportunity, source, invoice, is_sample):
    # Approval/status timestamps are deliberate: recall, revision, reapproval or
    # changed commercial terms require a fresh production decision. Lifecycle-only
    # status changes such as invoiced -> production do not invalidate a release.
    source_values = {}
    if source:
        excluded = {"updated_at", "status"}
        source_values = {
            field.attname: str(getattr(source, field.attname))
            for field in source._meta.concrete_fields if field.name not in excluded
        }
        active_statuses = QUICK_APPROVED_STATUSES if isinstance(source, QuickCosting) else {"approved"}
        source_values["approval_state"] = "approved" if source.status in active_statuses else source.status
    source_lines = []
    source_smv = {}
    if isinstance(source, CostingHeader):
        source_lines = [{key: str(value) for key, value in row.items()} for row in source.line_items.order_by("pk").values()]
        smv = CostingSMV.objects.filter(costing_id=source.pk).values().first()
        source_smv = {key: str(value) for key, value in (smv or {}).items()}
    return {
        "opportunity_id": opportunity.pk if opportunity else None,
        "source_kind": "quick" if isinstance(source, QuickCosting) else "advanced" if source else "",
        "source": source_values,
        "source_lines": source_lines,
        "source_smv": source_smv,
        "invoice_id": invoice.pk if invoice else None,
        "invoice_total": str(invoice.total_amount) if invoice else "",
        "invoice_currency": invoice.currency if invoice else "",
        "invoice_deposit_percentage": str(invoice.deposit_percentage) if invoice else "",
        "invoice_customer_id": invoice.customer_id if invoice else None,
        "invoice_source_ids": [invoice.quick_costing_id, invoice.costing_header_id] if invoice else [],
        "is_sample": is_sample,
    }


def _fingerprint(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def production_readiness(opportunity, *, source=None, invoice=None, user=None):
    invoice_selected_here = invoice is None
    source, invoice = _select_records(opportunity, source, invoice)
    is_quick = isinstance(source, QuickCosting)
    is_sample = bool((is_quick and source.is_sampling) or (invoice and invoice.invoice_type == "sample"))
    payment = production_payment_requirement(invoice, is_sample=is_sample)
    costing_approved = bool(source and (
        source.status in QUICK_APPROVED_STATUSES and source.approved_at
        if is_quick else source.status == "approved"
    ))
    quotation_approved = bool(source and source.quotation_number and source.quoted_at and (
        costing_approved and not source.quotation_revision_required
        if is_quick else source.quotation_status in {
            CostingHeader.QUOTATION_STATUS_APPROVED, CostingHeader.QUOTATION_STATUS_SENT,
            CostingHeader.QUOTATION_STATUS_ACCEPTED,
        } and source.quotation_approved_at
    ))
    blockers = []

    def block(key, label):
        blockers.append({"key": key, "label": label})

    if not opportunity or not opportunity.pk or opportunity.is_archived:
        block("opportunity", "An active opportunity is required.")
    if not source:
        block("source", "Create the costing and quotation for this opportunity.")
    elif source.opportunity_id != getattr(opportunity, "pk", None):
        block("source", "The costing must belong to this opportunity.")
    elif getattr(source, "is_archived", False):
        block("source", "Archived costing cannot be released to production.")
    elif is_quick and (source.superseded_by_id or not source.is_latest_revision):
        block("source", "Use the latest Quick Costing revision and its invoice.")
    if not invoice:
        block("invoice", payment["message"])
    elif source and (
        (is_quick and invoice.quick_costing_id != source.pk)
        or (not is_quick and invoice.costing_header_id != source.pk)
        or (invoice.opportunity_id and invoice.opportunity_id != opportunity.pk)
    ):
        block("invoice", "The invoice must belong to the selected costing and opportunity.")
    elif payment.get("reason") in {"archived_invoice", "cancelled_invoice", "zero_invoice_total"}:
        block("invoice", payment["message"])
    elif invoice and not invoice_selected_here:
        source_filter = {"quick_costing_id": source.pk} if is_quick else {"costing_header_id": source.pk} if source else {}
        if source_filter:
            newest_id = Invoice.objects.filter(**source_filter).order_by("-issue_date", "-created_at", "-pk").values_list("pk", flat=True).first()
            if newest_id != invoice.pk:
                block("invoice", "Use the latest invoice for this costing; older invoices cannot release production.")
    if not costing_approved:
        block("costing", "Costing approval is pending.")
    if not quotation_approved:
        block("quotation", "Quotation approval is pending.")
    if not payment.get("allowed", False):
        block("payment", "Full sample payment must be verified." if is_sample else "The invoice's required customer deposit must be verified.")

    snapshot = _approval_snapshot(opportunity, source, invoice, is_sample)
    fingerprint = _fingerprint(snapshot)
    release = None
    release_reason = ""
    waived = set()
    production_order = None
    if opportunity and opportunity.pk:
        production_order = ProductionOrder.objects.filter(opportunity=opportunity).order_by("pk").first()
        candidate = CRMAuditLog.objects.filter(
            module="opportunities", record_id=str(opportunity.pk),
            action_type=CRMAuditLog.ACTION_APPROVED, field_name=APPROVAL_FIELD,
        ).select_related("actor").first()
        if candidate:
            try:
                payload = json.loads(candidate.new_value)
            except (TypeError, ValueError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            raw_waivers = payload.get("waived_requirements", [])
            candidate_waivers = set(raw_waivers) if isinstance(raw_waivers, list) and all(isinstance(value, str) for value in raw_waivers) else {"invalid"}
            if (
                payload.get("fingerprint") == fingerprint
                and payload.get("opportunity_id") == opportunity.pk
                and _can_release(candidate.actor)
                and candidate_waivers <= WAIVABLE_REQUIREMENTS
                and (not candidate_waivers or (payload.get("reason", "").strip() and can_approve_production_exception(candidate.actor)))
            ):
                release, waived = candidate, candidate_waivers
                release_reason = payload.get("reason", "")
    unmet = list(blockers)
    blockers = [item for item in blockers if item["key"] not in waived]
    production_approved = bool(release or production_order)
    if not production_approved:
        block("production_approval", "Production release approval is pending.")
    return {
        **payment,
        "source": source,
        "source_kind": "quick" if is_quick else "advanced" if source else "",
        "invoice": invoice,
        "is_sample": is_sample,
        "costing_approved": costing_approved,
        "quotation_approved": quotation_approved,
        "payment_verified": bool(payment.get("allowed", False)),
        "production_approved": production_approved,
        "can_release": _can_release(user),
        "can_exception": can_approve_production_exception(user),
        "blockers": blockers,
        "waivable_blockers": [item for item in unmet if item["key"] in WAIVABLE_REQUIREMENTS],
        "waived_requirements": sorted(waived),
        "production_order": production_order,
        "release_approval": release,
        "release_reason": release_reason,
        "ready": not blockers,
        "snapshot": snapshot,
        "fingerprint": fingerprint,
        "payment_required_percentage_display": payment.get("required_percentage_display", "—"),
    }


@transaction.atomic
def approve_production_release(opportunity, *, user, reason="", waived_requirements=()):
    if not _can_release(user):
        raise ProductionApprovalError("You do not have permission to approve production release.")
    opportunity = Opportunity.objects.select_for_update().get(pk=opportunity.pk)
    check = production_readiness(opportunity, user=user)
    waivers = set(waived_requirements)
    if not waivers <= WAIVABLE_REQUIREMENTS:
        raise ProductionApprovalError("Only costing approval, quotation approval, and payment requirements may be waived.")
    if waivers and not can_approve_production_exception(user):
        raise ProductionApprovalError("Only CEO or Super Admin may approve a production exception.")
    reason = str(reason or "").strip()
    if waivers and not reason:
        raise ProductionApprovalError("A reason is required for every production exception.")
    missing = {item["key"] for item in check["waivable_blockers"]}
    if waivers - missing:
        raise ProductionApprovalError("Select only requirements that are currently pending.")
    pending_requirements = {item["key"]: item["label"] for item in check["blockers"] + check["waivable_blockers"]}
    remaining = [label for key, label in pending_requirements.items() if key != "production_approval" and key not in waivers]
    if remaining:
        raise ProductionApprovalError(" ".join(remaining))
    if check["release_approval"] and set(check["waived_requirements"]) == waivers:
        return check
    payload = {
        "version": 1, "opportunity_id": opportunity.pk,
        "fingerprint": check["fingerprint"], "snapshot": check["snapshot"],
        "reason": reason, "waived_requirements": sorted(waivers),
    }
    # Approval must fail if its audit cannot be persisted. The generic audit
    # scheduler intentionally tolerates failures, so it is unsuitable here.
    CRMAuditLog.objects.create(
        actor=user, module="opportunities", record_id=str(opportunity.pk),
        record_label=opportunity.opportunity_id,
        action_type=CRMAuditLog.ACTION_APPROVED, field_name=APPROVAL_FIELD,
        new_value=json.dumps(payload, sort_keys=True),
        target_url=reverse("opportunity_detail", args=[opportunity.pk]),
    )
    return production_readiness(opportunity, user=user)


@transaction.atomic
def assert_production_release(opportunity, *, source=None, invoice=None, user=None):
    if not _can_release(user):
        raise ProductionApprovalError("CEO/Admin production release permission is required.")
    if not opportunity or not opportunity.pk:
        raise ProductionApprovalError("An active opportunity is required.")
    opportunity = Opportunity.objects.select_for_update().get(pk=opportunity.pk)
    if source and source.pk:
        source = type(source).objects.select_for_update().get(pk=source.pk)
    if invoice and invoice.pk:
        invoice = Invoice.objects.select_for_update().select_related("quick_costing", "costing_header").get(pk=invoice.pk)
    check = production_readiness(opportunity, source=source, invoice=invoice, user=user)
    if check["blockers"]:
        raise ProductionApprovalError(" ".join(item["label"] for item in check["blockers"]))
    return check


def has_production_requirement_waiver(opportunity, *, source, requirement):
    """Model-level validation may recognize an existing durable CEO exception."""
    if requirement not in WAIVABLE_REQUIREMENTS:
        return False
    check = production_readiness(opportunity, source=source)
    return bool(check["release_approval"] and requirement in check["waived_requirements"] and not check["blockers"])
