from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from crm.models import (
    AccountingEntry,
    Customer,
    Invoice,
    InvoicePayment,
    ReceivableAllocation,
    ReceivableEvent,
)
from crm.services.payment_reconciliation import record_invoice_payment
from crm.services.receivables_ledger import (
    DuplicateLedgerWrite,
    ReceivablesLedgerError,
    create_draft_allocation,
    create_draft_event,
    post_allocation,
    posted_allocation_totals,
    post_event,
)


@override_settings(FINANCIAL_CORE_WRITES_ENABLED=False)
class ReceivablesLedgerPhase3BTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="phase3b-finance",
            password="test-password",
            is_staff=True,
        )
        self.customer = Customer.objects.create(
            customer_code="CUS-PHASE3B",
            account_brand="Phase 3B Customer",
        )
        self.other_customer = Customer.objects.create(
            customer_code="CUS-PHASE3B-OTHER",
            account_brand="Other Customer",
        )
        self.invoice = Invoice.objects.create(
            invoice_number="INV-PHASE3B-1",
            customer=self.customer,
            currency="CAD",
            invoice_region="CA",
            total_amount=Decimal("500.00"),
            paid_amount=Decimal("0.00"),
            status="sent",
            invoice_status="APPROVED",
            approved_at=timezone.now(),
            approved_by=self.user,
        )

    def _accounting_entry(self, *, amount=Decimal("100.00"), direction=AccountingEntry.DIR_IN):
        return AccountingEntry.objects.create(
            date=timezone.localdate(),
            side=AccountingEntry.SIDE_CA,
            direction=direction,
            status="PAID",
            main_type="INCOME" if direction == AccountingEntry.DIR_IN else "EXPENSE",
            sub_type="Phase 3B test",
            customer=self.customer,
            currency="CAD",
            amount_original=amount,
            rate_to_cad=Decimal("1"),
            rate_to_bdt=Decimal("90"),
            created_by=self.user,
        )

    def _draft_event(self, *, key="phase3b-event-1", amount=Decimal("100.00"), **overrides):
        values = {
            "customer": self.customer,
            "kind": ReceivableEvent.KIND_CASH_RECEIPT,
            "event_date": timezone.localdate(),
            "effective_date": timezone.localdate(),
            "native_amount": amount,
            "currency": "CAD",
            "rate_to_bdt": Decimal("90"),
            "idempotency_key": key,
            "external_reference": f"BANK-{key}",
            "accounting_entry": self._accounting_entry(amount=amount),
            "actor": self.user,
        }
        values.update(overrides)
        return create_draft_event(**values)

    def test_schema_starts_dormant_and_phase3a_payment_does_not_dual_write(self):
        self.assertFalse(ReceivableEvent.objects.exists())
        self.assertFalse(ReceivableAllocation.objects.exists())

        record_invoice_payment(
            self.invoice,
            InvoicePayment(
                payment_date=timezone.localdate(),
                amount=Decimal("125.00"),
                currency="CAD",
                side="CA",
                rate_to_cad=Decimal("1"),
                rate_to_bdt=Decimal("90"),
            ),
            actor=self.user,
        )

        self.assertFalse(ReceivableEvent.objects.exists())
        self.assertFalse(ReceivableAllocation.objects.exists())

    def test_currency_snapshots_are_explicit_and_immutable(self):
        cases = [
            ("CAD", Decimal("100.00"), Decimal("0"), Decimal("90"), Decimal("100.00"), Decimal("9000.00")),
            ("BDT", Decimal("9000.00"), Decimal("90"), Decimal("0"), Decimal("100.00"), Decimal("9000.00")),
            ("USD", Decimal("100.00"), Decimal("1.35"), Decimal("121.50"), Decimal("135.00"), Decimal("12150.00")),
        ]
        for index, (currency, amount, cad_rate, bdt_rate, expected_cad, expected_bdt) in enumerate(cases):
            with self.subTest(currency=currency):
                event = create_draft_event(
                    customer=self.customer,
                    kind=ReceivableEvent.KIND_ADJUSTMENT,
                    event_date=timezone.localdate(),
                    effective_date=timezone.localdate(),
                    native_amount=amount,
                    currency=currency,
                    rate_to_cad=cad_rate,
                    rate_to_bdt=bdt_rate,
                    idempotency_key=f"currency-{index}",
                    evidence_reference=f"FX-{index}",
                    actor=self.user,
                )
                self.assertEqual(event.amount_cad, expected_cad)
                self.assertEqual(event.amount_bdt, expected_bdt)

        posted = post_event(ReceivableEvent.objects.get(idempotency_key="currency-0"), actor=self.user)
        posted.native_amount = Decimal("101.00")
        with self.assertRaisesMessage(ValidationError, "immutable"):
            posted.save()

    def test_event_idempotency_blocks_duplicate_submission(self):
        event = self._draft_event(key="duplicate-event")
        with self.assertRaises(DuplicateLedgerWrite):
            create_draft_event(
                customer=self.customer,
                kind=ReceivableEvent.KIND_CASH_RECEIPT,
                event_date=timezone.localdate(),
                effective_date=timezone.localdate(),
                native_amount=Decimal("100.00"),
                currency="CAD",
                rate_to_bdt=Decimal("90"),
                idempotency_key="duplicate-event",
                accounting_entry=event.accounting_entry,
                actor=self.user,
            )
        self.assertEqual(ReceivableEvent.objects.filter(idempotency_key="duplicate-event").count(), 1)

    def test_cash_event_requires_matching_accounting_entry(self):
        event = self._draft_event(key="mismatch-event")
        AccountingEntry.objects.filter(pk=event.accounting_entry_id).update(amount_original=Decimal("99.00"))
        event.refresh_from_db()

        with self.assertRaisesMessage(ReceivablesLedgerError, "amount must match"):
            post_event(event, actor=self.user)
        event.refresh_from_db()
        self.assertEqual(event.state, ReceivableEvent.STATE_DRAFT)

    def test_legacy_payment_link_requires_an_exact_snapshot(self):
        recorded = record_invoice_payment(
            self.invoice,
            InvoicePayment(
                payment_date=timezone.localdate(),
                amount=Decimal("75.00"),
                currency="CAD",
                side="CA",
                rate_to_cad=Decimal("1"),
                rate_to_bdt=Decimal("90"),
            ),
            actor=self.user,
        )
        event = create_draft_event(
            customer=self.customer,
            kind=ReceivableEvent.KIND_CASH_RECEIPT,
            event_date=recorded.payment.payment_date,
            effective_date=recorded.payment.payment_date,
            native_amount=recorded.payment.amount,
            currency=recorded.payment.currency,
            rate_to_cad=recorded.payment.rate_to_cad,
            rate_to_bdt=recorded.payment.rate_to_bdt,
            idempotency_key="legacy-payment-link",
            external_reference="BANK-LEGACY-LINK",
            legacy_invoice_payment=recorded.payment,
            accounting_entry=recorded.accounting_entry,
            actor=self.user,
        )

        posted = post_event(event, actor=self.user)
        self.assertEqual(posted.legacy_invoice_payment_id, recorded.payment.pk)
        self.assertEqual(posted.accounting_entry_id, recorded.accounting_entry.pk)

    def test_opening_receipt_requires_evidence_and_migration_batch(self):
        event = create_draft_event(
            customer=self.customer,
            kind=ReceivableEvent.KIND_OPENING_RECEIPT,
            event_date=timezone.localdate(),
            effective_date=timezone.localdate(),
            native_amount=Decimal("50.00"),
            currency="CAD",
            rate_to_bdt=Decimal("90"),
            idempotency_key="opening-without-evidence",
            actor=self.user,
        )
        with self.assertRaisesMessage(ReceivablesLedgerError, "evidence reference"):
            post_event(event, actor=self.user)

        event.evidence_reference = "BANK-STATEMENT-ARCHIVE"
        event.save(update_fields=["evidence_reference", "updated_at"])
        with self.assertRaisesMessage(ReceivablesLedgerError, "migration batch"):
            post_event(event, actor=self.user)

    def test_posted_event_and_allocation_are_append_only(self):
        event = post_event(self._draft_event(key="immutable-event"), actor=self.user)
        allocation = post_allocation(
            create_draft_allocation(
                event=event,
                invoice=self.invoice,
                signed_amount=Decimal("100.00"),
                allocation_date=timezone.localdate(),
                idempotency_key="immutable-allocation",
                actor=self.user,
            ),
            actor=self.user,
        )

        event.reason = "Edited after posting"
        with self.assertRaisesMessage(ValidationError, "immutable"):
            event.save()
        with self.assertRaisesMessage(ValidationError, "cannot be deleted"):
            event.delete()

        allocation.signed_amount = Decimal("90.00")
        with self.assertRaisesMessage(ValidationError, "immutable"):
            allocation.save()
        with self.assertRaisesMessage(ValidationError, "cannot be deleted"):
            allocation.delete()

    def test_allocation_requires_matching_customer_and_currency(self):
        event = post_event(self._draft_event(key="customer-match-event"), actor=self.user)
        other_invoice = Invoice.objects.create(
            invoice_number="INV-PHASE3B-OTHER",
            customer=self.other_customer,
            currency="CAD",
            invoice_region="CA",
            total_amount=Decimal("100.00"),
            status="sent",
        )
        allocation = create_draft_allocation(
            event=event,
            invoice=other_invoice,
            signed_amount=Decimal("50.00"),
            allocation_date=timezone.localdate(),
            idempotency_key="wrong-customer-allocation",
            actor=self.user,
        )
        with self.assertRaisesMessage(ReceivablesLedgerError, "customer must match"):
            post_allocation(allocation, actor=self.user)

    def test_posted_allocations_cannot_exceed_event_amount(self):
        event = post_event(self._draft_event(key="capacity-event"), actor=self.user)
        first = create_draft_allocation(
            event=event,
            invoice=self.invoice,
            signed_amount=Decimal("80.00"),
            allocation_date=timezone.localdate(),
            idempotency_key="capacity-first",
            actor=self.user,
        )
        post_allocation(first, actor=self.user)

        second = create_draft_allocation(
            event=event,
            invoice=self.invoice,
            signed_amount=Decimal("21.00"),
            allocation_date=timezone.localdate(),
            idempotency_key="capacity-second",
            actor=self.user,
        )
        with self.assertRaisesMessage(ReceivablesLedgerError, "cannot exceed"):
            post_allocation(second, actor=self.user)

    def test_exact_reversal_links_preserve_original_records(self):
        original_event = post_event(self._draft_event(key="reversal-original"), actor=self.user)
        original_allocation = post_allocation(
            create_draft_allocation(
                event=original_event,
                invoice=self.invoice,
                signed_amount=Decimal("100.00"),
                allocation_date=timezone.localdate(),
                idempotency_key="reversal-original-allocation",
                actor=self.user,
            ),
            actor=self.user,
        )
        reversal_event = post_event(
            create_draft_event(
                customer=self.customer,
                kind=ReceivableEvent.KIND_REVERSAL,
                event_date=timezone.localdate(),
                effective_date=timezone.localdate(),
                native_amount=Decimal("100.00"),
                currency="CAD",
                rate_to_bdt=Decimal("90"),
                idempotency_key="reversal-event",
                evidence_reference="REVERSAL-APPROVAL-1",
                reverses_event=original_event,
                actor=self.user,
            ),
            actor=self.user,
        )
        reversal_allocation = post_allocation(
            create_draft_allocation(
                event=reversal_event,
                invoice=self.invoice,
                signed_amount=Decimal("-100.00"),
                allocation_date=timezone.localdate(),
                idempotency_key="reversal-allocation",
                reverses_allocation=original_allocation,
                actor=self.user,
            ),
            actor=self.user,
        )

        self.assertEqual(reversal_event.reverses_event_id, original_event.pk)
        self.assertEqual(reversal_allocation.reverses_allocation_id, original_allocation.pk)
        self.assertTrue(ReceivableEvent.objects.filter(pk=original_event.pk).exists())
        self.assertTrue(ReceivableAllocation.objects.filter(pk=original_allocation.pk).exists())

    def test_grouped_shadow_totals_use_one_query(self):
        event = post_event(self._draft_event(key="grouped-event"), actor=self.user)
        post_allocation(
            create_draft_allocation(
                event=event,
                invoice=self.invoice,
                signed_amount=Decimal("40.00"),
                allocation_date=timezone.localdate(),
                idempotency_key="grouped-allocation",
                actor=self.user,
            ),
            actor=self.user,
        )

        with self.assertNumQueries(1):
            totals = posted_allocation_totals([self.invoice.pk])
        self.assertEqual(totals, {self.invoice.pk: Decimal("40.00")})

    def test_database_constraints_reject_zero_amounts(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ReceivableEvent.objects.bulk_create(
                [
                    ReceivableEvent(
                        customer=self.customer,
                        kind=ReceivableEvent.KIND_ADJUSTMENT,
                        event_date=timezone.localdate(),
                        effective_date=timezone.localdate(),
                        native_amount=Decimal("0.00"),
                        currency="CAD",
                        rate_to_cad=Decimal("1"),
                        rate_to_bdt=Decimal("90"),
                        amount_cad=Decimal("0.00"),
                        amount_bdt=Decimal("0.00"),
                        idempotency_key="zero-event",
                    )
                ]
            )
