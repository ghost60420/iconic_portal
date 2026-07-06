import logging

from django.db import transaction
from django.db.utils import OperationalError, ProgrammingError
from django.urls import NoReverseMatch, reverse

from crm.audit_context import get_current_actor
from crm.models import CRMAuditLog


logger = logging.getLogger(__name__)

EXCLUDED_FIELDS = {
    "created_at",
    "updated_at",
    "created_date",
    "updated_date",
    "last_login",
}
SENSITIVE_PARTS = ("password", "secret", "token", "credential", "api_key")

MODEL_CONFIG = {
    "Customer": ("customers", "customer_detail"),
    "Lead": ("leads", "lead_detail"),
    "Opportunity": ("opportunities", "opportunity_detail"),
    "CostingHeader": ("quotations", "cost_sheet_detail"),
    "ProductionOrder": ("production", "production_detail"),
    "Invoice": ("invoices", "invoice_view"),
    "InvoicePayment": ("invoices", "invoice_view"),
    "Shipment": ("shipments", "shipment_detail"),
    "QuickCosting": ("quick_costing", "quick_costing_detail"),
    "AccountingEntry": ("finance", "accounting_entry_edit"),
}

MODEL_LABEL_FIELDS = {
    "Customer": ("customer_code", "account_brand", "contact_name"),
    "Lead": ("lead_id", "account_brand", "contact_name"),
    "Opportunity": ("opportunity_id",),
    "CostingHeader": ("quotation_number", "style_code", "style_name"),
    "ProductionOrder": ("purchase_order_number", "title"),
    "Invoice": ("invoice_number",),
    "InvoicePayment": (),
    "Shipment": ("tracking_number",),
    "QuickCosting": ("quotation_number", "project_name", "buyer_name"),
    "AccountingEntry": ("description",),
}


def is_tracked_model(sender):
    return sender.__name__ in MODEL_CONFIG


def _safe_value(value):
    if value is None:
        return ""
    text = str(value)
    return text[:4000]


def _field_allowed(field_name):
    lowered = field_name.lower()
    return field_name not in EXCLUDED_FIELDS and not any(part in lowered for part in SENSITIVE_PARTS)


def model_snapshot(instance):
    snapshot = {}
    for field in instance._meta.concrete_fields:
        if not _field_allowed(field.name):
            continue
        snapshot[field.name] = _safe_value(getattr(instance, field.attname, None))
    return snapshot


def record_label(instance):
    if instance.__class__.__name__ == "InvoicePayment":
        invoice_number = getattr(getattr(instance, "invoice", None), "invoice_number", "")
        if invoice_number:
            return str(invoice_number)[:220]
    for field_name in MODEL_LABEL_FIELDS.get(instance.__class__.__name__, ("title",)):
        value = getattr(instance, field_name, None)
        if value:
            return str(value)[:220]
    return str(instance)[:220]


def target_url(instance):
    config = MODEL_CONFIG.get(instance.__class__.__name__)
    if not config:
        return ""
    _module, url_name = config
    pk = instance.pk
    if instance.__class__.__name__ == "InvoicePayment":
        pk = instance.invoice_id
    try:
        return reverse(url_name, args=[pk]) if pk else ""
    except NoReverseMatch:
        return ""


def _action_for_change(instance, field_name, old_value, new_value):
    model_name = instance.__class__.__name__
    if model_name == "InvoicePayment":
        return CRMAuditLog.ACTION_PAYMENT_RECORDED
    if model_name == "CostingHeader" and field_name == "quotation_number" and not old_value and new_value:
        return CRMAuditLog.ACTION_CONVERTED
    if field_name in {"status", "quotation_status", "operational_status", "invoice_status"}:
        lowered = (new_value or "").lower()
        if lowered in {"approved", "accepted"}:
            return CRMAuditLog.ACTION_APPROVED
        if lowered in {"rejected", "declined"}:
            return CRMAuditLog.ACTION_REJECTED
        return CRMAuditLog.ACTION_STATUS_CHANGED
    return CRMAuditLog.ACTION_UPDATED


def _write_rows(rows):
    try:
        CRMAuditLog.objects.bulk_create(rows)
    except (OperationalError, ProgrammingError):
        logger.warning("CRM audit table is not available; record save was preserved")
    except Exception:
        logger.exception("CRM audit write failed; record save was preserved")


def schedule_audit(instance, *, created=False, before=None, deleted=False):
    config = MODEL_CONFIG.get(instance.__class__.__name__)
    if not config or not instance.pk:
        return
    module, _url_name = config
    actor = get_current_actor()
    audit_record_id = instance.pk
    if instance.__class__.__name__ == "InvoicePayment":
        audit_record_id = instance.invoice_id
    common = {
        "actor": actor,
        "module": module,
        "record_id": str(audit_record_id),
        "record_label": record_label(instance),
        "target_url": target_url(instance),
    }

    if deleted:
        rows = [
            CRMAuditLog(
                action_type=CRMAuditLog.ACTION_DELETED,
                field_name="record",
                previous_value=common["record_label"] or common["record_id"],
                new_value="",
                **common,
            )
        ]
    elif created:
        action = (
            CRMAuditLog.ACTION_PAYMENT_RECORDED
            if instance.__class__.__name__ == "InvoicePayment"
            else CRMAuditLog.ACTION_CREATED
        )
        rows = [
            CRMAuditLog(
                action_type=action,
                field_name="record",
                previous_value="",
                new_value=common["record_label"] or common["record_id"],
                **common,
            )
        ]
    else:
        after = model_snapshot(instance)
        before = before or {}
        rows = []
        for field_name, new_value in after.items():
            old_value = before.get(field_name, "")
            if old_value == new_value:
                continue
            rows.append(
                CRMAuditLog(
                    action_type=_action_for_change(instance, field_name, old_value, new_value),
                    field_name=field_name,
                    previous_value=old_value,
                    new_value=new_value,
                    **common,
                )
            )
        if not rows:
            return

    transaction.on_commit(lambda audit_rows=rows: _write_rows(audit_rows))
