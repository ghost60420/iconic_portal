from decimal import Decimal

from django.db import migrations


def _entry_snapshot(entry):
    return {
        "id": entry.pk,
        "date": entry.date.isoformat(),
        "side": entry.side,
        "direction": entry.direction,
        "main_type": entry.main_type,
        "currency": entry.currency,
        "amount_original": str(entry.amount_original),
        "description": entry.description,
        "production_order_id": entry.production_order_id,
    }


def repair_approved_financial_relationships(apps, schema_editor):
    AccountingEntry = apps.get_model("crm", "AccountingEntry")
    AccountingEntryAudit = apps.get_model("crm", "AccountingEntryAudit")
    Invoice = apps.get_model("crm", "Invoice")
    InvoicePayment = apps.get_model("crm", "InvoicePayment")

    duplicate = InvoicePayment.objects.filter(pk=12).first()
    duplicate_entry = AccountingEntry.objects.filter(pk=143).first()
    if duplicate or duplicate_entry:
        invoice = Invoice.objects.filter(pk=29, invoice_number="INV00019").first()
        retained = InvoicePayment.objects.filter(pk=11).first()
        retained_entry = AccountingEntry.objects.filter(pk=142).first()
        if not all((invoice, retained, retained_entry, duplicate, duplicate_entry)):
            raise RuntimeError("Approved CAD 350 duplicate repair is only partially present.")
        payment_signature = (
            duplicate.invoice_id == invoice.pk
            and retained.invoice_id == invoice.pk
            and duplicate.payment_date.isoformat() == "2026-07-03"
            and retained.payment_date.isoformat() == "2026-07-03"
            and duplicate.amount == Decimal("350.00")
            and retained.amount == Decimal("350.00")
            and duplicate.currency == retained.currency == "CAD"
            and duplicate.side == retained.side == "CA"
            and duplicate.payment_method == retained.payment_method == "bank"
            and not (duplicate.notes or "").strip()
            and not (retained.notes or "").strip()
            and duplicate.accounting_entry_id == duplicate_entry.pk
            and retained.accounting_entry_id == retained_entry.pk
            and invoice.total_amount == invoice.paid_amount == Decimal("350.00")
        )
        entry_signature = (
            duplicate_entry.date.isoformat() == "2026-07-03"
            and duplicate_entry.amount_original == Decimal("350.00")
            and duplicate_entry.currency == "CAD"
            and duplicate_entry.direction == "IN"
            and duplicate_entry.main_type == "INCOME"
            and duplicate_entry.description == "Payment received for invoice INV00019"
        )
        if not payment_signature or not entry_signature:
            raise RuntimeError("Approved CAD 350 duplicate signature changed; migration stopped.")
        AccountingEntryAudit.objects.create(
            entry=duplicate_entry,
            action="DELETE",
            before_data={
                "entry": _entry_snapshot(duplicate_entry),
                "payment_id": duplicate.pk,
                "invoice_id": invoice.pk,
                "retained_payment_id": retained.pk,
            },
            after_data={
                "removed_payment_id": duplicate.pk,
                "removed_entry_id": duplicate_entry.pk,
                "retained_payment_id": retained.pk,
                "invoice_paid_amount": "350.00",
            },
            note="CEO-approved duplicate Payment 12 and AccountingEntry 143 relationship repair.",
        )
        duplicate.delete()
        duplicate_entry.delete()

    payment = InvoicePayment.objects.filter(pk=21).first()
    entry = AccountingEntry.objects.filter(pk=168).first()
    invoice = Invoice.objects.filter(pk=52, invoice_number="INV00040").first()
    if payment or entry or invoice:
        if not all((payment, entry, invoice)):
            raise RuntimeError("Approved PO122/PO123 relationship repair is only partially present.")
        signature = (
            invoice.order_id == 123
            and payment.invoice_id == invoice.pk
            and payment.accounting_entry_id == entry.pk
            and payment.amount == Decimal("1850.00")
            and payment.currency == "CAD"
            and entry.amount_original == Decimal("1850.00")
            and entry.currency == "CAD"
            and entry.description == "Payment received for invoice INV00040"
        )
        if not signature:
            raise RuntimeError("Approved PO122/PO123 relationship signature changed; migration stopped.")
        if payment.production_order_id == 123 and entry.production_order_id == 123:
            return
        if payment.production_order_id != 122 or entry.production_order_id != 122:
            raise RuntimeError("Approved PO122/PO123 relationship is in an unexpected state.")
        before = _entry_snapshot(entry)
        AccountingEntry.objects.filter(pk=entry.pk).update(production_order_id=123)
        InvoicePayment.objects.filter(pk=payment.pk).update(production_order_id=123)
        entry.production_order_id = 123
        AccountingEntryAudit.objects.create(
            entry=entry,
            action="UPDATE",
            before_data={
                "entry": before,
                "payment_id": payment.pk,
                "payment_production_order_id": 122,
                "invoice_order_id": invoice.order_id,
            },
            after_data={
                "entry": _entry_snapshot(entry),
                "payment_id": payment.pk,
                "payment_production_order_id": 123,
                "invoice_order_id": invoice.order_id,
            },
            note="CEO-approved Payment 21 and AccountingEntry 168 relationship repair to PO123.",
        )


def refuse_live_data_reverse(apps, schema_editor):
    AccountingEntry = apps.get_model("crm", "AccountingEntry")
    AccountingEntryAudit = apps.get_model("crm", "AccountingEntryAudit")
    InvoicePayment = apps.get_model("crm", "InvoicePayment")

    repair_evidence_exists = AccountingEntryAudit.objects.filter(
        note__startswith="CEO-approved ",
        note__contains="relationship repair",
    ).exists()
    repaired_link_exists = (
        InvoicePayment.objects.filter(pk=21, production_order_id=123).exists()
        or AccountingEntry.objects.filter(pk=168, production_order_id=123).exists()
    )
    duplicate_was_removed = (
        InvoicePayment.objects.filter(pk=11).exists()
        and not InvoicePayment.objects.filter(pk=12).exists()
    )
    if repair_evidence_exists or repaired_link_exists or duplicate_was_removed:
        raise RuntimeError(
            "Migration 0198 changed approved production relationships. "
            "Restore the pre-deployment database backup instead of recreating historical records."
        )


class Migration(migrations.Migration):
    dependencies = [("crm", "0197_finance_operations_layer")]

    operations = [
        migrations.RunPython(
            repair_approved_financial_relationships,
            reverse_code=refuse_live_data_reverse,
        )
    ]
