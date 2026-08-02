from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Prefetch, Sum
from django.utils import timezone

from crm.models import Invoice, InvoicePayment, ReceivableAllocation, ReceivableEvent


MONEY = Decimal("0.01")
SUPPORTED_CURRENCIES = {"CAD", "USD", "BDT"}
ISSUED_STATUSES = {"sent", "partial", "paid"}
BLOCKING_SEVERITIES = {"critical", "high"}


def _decimal(value) -> Decimal:
    if value in ("", None):
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _money(value) -> Decimal:
    return _decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP)


def _customer_label(invoice: Invoice) -> str:
    customer = invoice.customer
    if not customer:
        return "Missing customer"
    return customer.account_brand or customer.contact_name or customer.customer_code or f"Customer {customer.pk}"


@dataclass(frozen=True)
class LedgerException:
    severity: str
    code: str
    invoice: str
    customer: str
    amount: Decimal
    currency: str
    reason: str
    recommended_action: str
    source: str = ""

    @property
    def blocking(self) -> bool:
        return self.severity in BLOCKING_SEVERITIES

    def as_json(self) -> dict:
        row = asdict(self)
        row["amount"] = str(self.amount)
        row["blocking"] = self.blocking
        return row


@dataclass(frozen=True)
class EventSpec:
    key: str
    kind: str
    invoice: Invoice
    payment: InvoicePayment | None
    amount: Decimal
    currency: str


@dataclass(frozen=True)
class AllocationSpec:
    key: str
    event_key: str
    invoice: Invoice
    payment: InvoicePayment
    amount: Decimal


@dataclass
class HistoricalLedgerPlan:
    invoices: list[Invoice]
    event_specs: list[EventSpec] = field(default_factory=list)
    allocation_specs: list[AllocationSpec] = field(default_factory=list)
    exceptions: list[LedgerException] = field(default_factory=list)
    source_payment_totals: dict[str, Decimal] = field(default_factory=dict)
    linked_accounting_totals: dict[str, Decimal] = field(default_factory=dict)
    current_outstanding_totals: dict[str, Decimal] = field(default_factory=dict)
    projected_ledger_payment_totals: dict[str, Decimal] = field(default_factory=dict)
    projected_ledger_outstanding_totals: dict[str, Decimal] = field(default_factory=dict)

    @property
    def blocking_exception_count(self) -> int:
        return sum(row.blocking for row in self.exceptions)


@dataclass
class HistoricalLedgerResult:
    dry_run: bool
    batch_id: str
    invoices_scanned: int
    payments_scanned: int
    events_planned: dict[str, int]
    allocations_planned: int
    events_created: dict[str, int]
    events_reused: dict[str, int]
    allocations_created: int
    allocations_reused: int
    exceptions: list[LedgerException]
    reconciliation: list[dict]
    source_capabilities: dict[str, str]

    @property
    def blocking_exception_count(self) -> int:
        return sum(row.blocking for row in self.exceptions)

    @property
    def ready(self) -> bool:
        return self.blocking_exception_count == 0 and all(row["reconciled"] for row in self.reconciliation)

    def as_json(self) -> dict:
        return {
            "dry_run": self.dry_run,
            "batch_id": self.batch_id,
            "invoices_scanned": self.invoices_scanned,
            "payments_scanned": self.payments_scanned,
            "events_planned": self.events_planned,
            "allocations_planned": self.allocations_planned,
            "events_created": self.events_created,
            "events_reused": self.events_reused,
            "allocations_created": self.allocations_created,
            "allocations_reused": self.allocations_reused,
            "blocking_exception_count": self.blocking_exception_count,
            "ready": self.ready,
            "exceptions": [row.as_json() for row in self.exceptions],
            "reconciliation": [
                {
                    key: str(value) if isinstance(value, Decimal) else value
                    for key, value in row.items()
                }
                for row in self.reconciliation
            ],
            "source_capabilities": self.source_capabilities,
        }


def _add_total(totals: dict[str, Decimal], currency: str, amount) -> None:
    totals[currency] = _money(totals.get(currency, Decimal("0")) + _decimal(amount))


def _exception(
    plan: HistoricalLedgerPlan,
    *,
    severity: str,
    code: str,
    invoice: Invoice,
    amount,
    reason: str,
    recommended_action: str,
    source: str = "",
) -> None:
    plan.exceptions.append(
        LedgerException(
            severity=severity,
            code=code,
            invoice=invoice.invoice_number,
            customer=_customer_label(invoice),
            amount=_money(amount),
            currency=(invoice.currency or "").upper().strip(),
            reason=reason,
            recommended_action=recommended_action,
            source=source,
        )
    )


def _invoice_event_key(invoice: Invoice) -> str:
    return f"phase3c:invoice-issued:{invoice.pk}"


def _payment_event_key(payment: InvoicePayment) -> str:
    return f"phase3c:payment:{payment.pk}"


def _allocation_key(payment: InvoicePayment) -> str:
    return f"phase3c:payment-allocation:{payment.pk}"


def _payment_integrity_errors(
    payment: InvoicePayment,
    *,
    duplicate_accounting_entry_ids: set[int],
) -> list[str]:
    invoice = payment.invoice
    entry = payment.accounting_entry
    errors = []
    if _decimal(payment.amount) <= 0:
        errors.append("payment amount is not positive")
    if not invoice.customer_id:
        errors.append("invoice has no customer")
    if (payment.currency or "").upper().strip() != (invoice.currency or "").upper().strip():
        errors.append("payment currency does not match invoice currency")
    if (payment.currency or "").upper().strip() not in SUPPORTED_CURRENCIES:
        errors.append("payment currency is unsupported")
    if not entry:
        errors.append("payment has no linked AccountingEntry")
        return errors
    if entry.customer_id != invoice.customer_id:
        errors.append("AccountingEntry customer does not match invoice customer")
    if entry.pk in duplicate_accounting_entry_ids:
        errors.append("AccountingEntry is linked to more than one InvoicePayment")
    if (entry.currency or "").upper().strip() != (payment.currency or "").upper().strip():
        errors.append("AccountingEntry currency does not match payment currency")
    if _money(entry.amount_original) != _money(payment.amount):
        errors.append("AccountingEntry amount does not match payment amount")
    if entry.date != payment.payment_date:
        errors.append("AccountingEntry date does not match payment date")
    if entry.direction != "IN":
        errors.append("AccountingEntry direction is not IN")
    if (entry.status or "").upper() in {"CANCELLED", "VOID"}:
        errors.append("AccountingEntry is cancelled or void")
    if _decimal(entry.rate_to_cad) != _decimal(payment.rate_to_cad):
        errors.append("AccountingEntry CAD rate does not match payment")
    if _decimal(entry.rate_to_bdt) != _decimal(payment.rate_to_bdt):
        errors.append("AccountingEntry BDT rate does not match payment")
    if _money(entry.amount_cad) != _money(payment.amount_cad):
        errors.append("AccountingEntry CAD amount does not match payment")
    if _money(entry.amount_bdt) != _money(payment.amount_bdt):
        errors.append("AccountingEntry BDT amount does not match payment")
    return errors


def build_historical_ledger_plan() -> HistoricalLedgerPlan:
    payment_queryset = InvoicePayment.objects.select_related("accounting_entry").order_by("payment_date", "pk")
    invoices = list(
        Invoice.objects.select_related("customer")
        .prefetch_related(Prefetch("payments", queryset=payment_queryset))
        .order_by("pk")
    )
    plan = HistoricalLedgerPlan(invoices=invoices)
    projected_allocations = defaultdict(Decimal)
    accounting_entry_usage = Counter(
        payment.accounting_entry_id
        for invoice in invoices
        for payment in invoice.payments.all()
        if payment.accounting_entry_id
    )
    duplicate_accounting_entry_ids = {
        entry_id for entry_id, count in accounting_entry_usage.items() if count > 1
    }
    counted_accounting_entries: set[int] = set()

    for invoice in invoices:
        currency = (invoice.currency or "").upper().strip()
        total = _money(invoice.total_amount)
        paid = _money(invoice.paid_amount)
        payments = list(invoice.payments.all())
        tracked = _money(sum((_decimal(payment.amount) for payment in payments), Decimal("0")))

        current_balance = max(total - paid, Decimal("0"))
        if (
            not invoice.is_archived
            and invoice.status not in {"paid", "cancelled"}
            and current_balance > 0
        ):
            _add_total(plan.current_outstanding_totals, currency, current_balance)
        elif invoice.is_archived and invoice.status != "cancelled" and current_balance > 0:
            _exception(
                plan,
                severity="high",
                code="archived_invoice_open_balance",
                invoice=invoice,
                amount=current_balance,
                reason="The archived invoice still has a positive balance but is excluded from live Accounting receivables.",
                recommended_action="Finance must confirm whether to reopen, settle, or void the balance with supporting evidence.",
                source=f"Invoice:{invoice.pk}",
            )

        invoice_event_valid = True
        if not invoice.customer_id:
            invoice_event_valid = False
            _exception(
                plan,
                severity="critical",
                code="invoice_missing_customer",
                invoice=invoice,
                amount=total,
                reason="The invoice has no customer and cannot own a receivable event.",
                recommended_action="Link the correct customer using approved source evidence before population.",
                source=f"Invoice:{invoice.pk}",
            )
        if total <= 0:
            invoice_event_valid = False
            _exception(
                plan,
                severity="critical",
                code="invoice_nonpositive_total",
                invoice=invoice,
                amount=total,
                reason="The invoice total is not positive.",
                recommended_action="Review the invoice document and approved correction history.",
                source=f"Invoice:{invoice.pk}",
            )
        if currency not in SUPPORTED_CURRENCIES:
            invoice_event_valid = False
            _exception(
                plan,
                severity="critical",
                code="invoice_unsupported_currency",
                invoice=invoice,
                amount=total,
                reason=f"Currency {currency or 'blank'} is unsupported by the receivables ledger.",
                recommended_action="Provide an approved currency mapping without rewriting the invoice.",
                source=f"Invoice:{invoice.pk}",
            )
        if invoice.status == "draft":
            invoice_event_valid = False
            _exception(
                plan,
                severity="high",
                code="draft_invoice_not_issued",
                invoice=invoice,
                amount=total,
                reason="A draft invoice contributes to current Accounting outstanding but has no issuance evidence.",
                recommended_action="Approve/issue through the existing workflow or exclude it after Finance review.",
                source=f"Invoice:{invoice.pk}",
            )
        elif invoice.status == "cancelled":
            invoice_event_valid = False
            _exception(
                plan,
                severity="high",
                code="cancelled_invoice_issuance_unverified",
                invoice=invoice,
                amount=total,
                reason="The current record is cancelled and does not prove whether it was historically issued.",
                recommended_action="Review the original invoice and void evidence before creating issuance/reversal events.",
                source=f"Invoice:{invoice.pk}",
            )
        elif invoice.status not in ISSUED_STATUSES:
            invoice_event_valid = False
            _exception(
                plan,
                severity="critical",
                code="unknown_invoice_status",
                invoice=invoice,
                amount=total,
                reason=f"Invoice status {invoice.status!r} has no approved historical mapping.",
                recommended_action="Approve a status mapping before ledger population.",
                source=f"Invoice:{invoice.pk}",
            )

        if invoice_event_valid:
            plan.event_specs.append(
                EventSpec(
                    key=_invoice_event_key(invoice),
                    kind=ReceivableEvent.KIND_INVOICE_ISSUED,
                    invoice=invoice,
                    payment=None,
                    amount=total,
                    currency=currency,
                )
            )
            if currency in {"BDT", "USD"}:
                _exception(
                    plan,
                    severity="medium",
                    code="invoice_fx_snapshot_unavailable",
                    invoice=invoice,
                    amount=total,
                    reason="The invoice has no preserved historical CAD conversion rate; native currency remains authoritative.",
                    recommended_action="Attach an evidence-backed historical rate before using this principal for CAD reporting.",
                    source=f"Invoice:{invoice.pk}",
                )

        remaining = max(total, Decimal("0"))
        for payment in payments:
            _add_total(plan.source_payment_totals, (payment.currency or "").upper().strip(), payment.amount)
            if payment.accounting_entry and payment.accounting_entry_id not in counted_accounting_entries:
                _add_total(
                    plan.linked_accounting_totals,
                    (payment.accounting_entry.currency or "").upper().strip(),
                    payment.accounting_entry.amount_original,
                )
                counted_accounting_entries.add(payment.accounting_entry_id)

            errors = _payment_integrity_errors(
                payment,
                duplicate_accounting_entry_ids=duplicate_accounting_entry_ids,
            )
            if errors:
                _exception(
                    plan,
                    severity="critical",
                    code="payment_integrity_error",
                    invoice=invoice,
                    amount=payment.amount,
                    reason="; ".join(errors),
                    recommended_action="Reconcile the payment and AccountingEntry to bank/cash evidence, then post a reversal and corrected event if approved.",
                    source=f"InvoicePayment:{payment.pk}",
                )
                continue

            event_key = _payment_event_key(payment)
            plan.event_specs.append(
                EventSpec(
                    key=event_key,
                    kind=ReceivableEvent.KIND_CASH_RECEIPT,
                    invoice=invoice,
                    payment=payment,
                    amount=_money(payment.amount),
                    currency=(payment.currency or "").upper().strip(),
                )
            )
            _add_total(plan.projected_ledger_payment_totals, payment.currency, payment.amount)

            allocated = min(_money(payment.amount), remaining)
            if allocated > 0:
                plan.allocation_specs.append(
                    AllocationSpec(
                        key=_allocation_key(payment),
                        event_key=event_key,
                        invoice=invoice,
                        payment=payment,
                        amount=allocated,
                    )
                )
                projected_allocations[invoice.pk] += allocated
                remaining = _money(remaining - allocated)
            if _money(payment.amount) > allocated:
                _exception(
                    plan,
                    severity="high",
                    code="payment_exceeds_unallocated_balance",
                    invoice=invoice,
                    amount=_money(payment.amount) - allocated,
                    reason="The recorded payment exceeds the remaining invoice principal and cannot be fully allocated.",
                    recommended_action="Classify the excess as approved customer credit, duplicate receipt, refund, or reversal using bank evidence.",
                    source=f"InvoicePayment:{payment.pk}",
                )

        difference = _money(paid - tracked)
        if difference > 0:
            _exception(
                plan,
                severity="critical",
                code="untracked_paid_balance",
                invoice=invoice,
                amount=difference,
                reason="Stored paid amount exceeds recorded InvoicePayment history; no payment transaction proves this balance.",
                recommended_action="Provide bank/cash evidence and approve an OPENING_RECEIPT manifest entry.",
                source=f"Invoice:{invoice.pk}.paid_amount",
            )
        elif difference < 0:
            _exception(
                plan,
                severity="critical",
                code="tracked_payments_exceed_stored_paid",
                invoice=invoice,
                amount=abs(difference),
                reason="Recorded InvoicePayment history exceeds the stored paid amount.",
                recommended_action="Classify duplicate, unapplied credit, refund, or reversal rows using bank evidence.",
                source=f"Invoice:{invoice.pk}",
            )

    for invoice in invoices:
        if invoice.status == "cancelled":
            continue
        currency = (invoice.currency or "").upper().strip()
        issued_principal = sum(
            (spec.amount for spec in plan.event_specs if spec.invoice.pk == invoice.pk and spec.kind == ReceivableEvent.KIND_INVOICE_ISSUED),
            Decimal("0"),
        )
        allocated = projected_allocations[invoice.pk]
        projected = max(_money(issued_principal - allocated), Decimal("0"))
        if projected:
            _add_total(plan.projected_ledger_outstanding_totals, currency, projected)

    return plan


def _invoice_snapshot(invoice: Invoice) -> dict:
    amount = _money(invoice.total_amount)
    currency = (invoice.currency or "").upper().strip()
    return {
        "native_amount": amount,
        "currency": currency,
        "rate_to_cad": Decimal("1") if currency == "CAD" else Decimal("0"),
        "rate_to_bdt": Decimal("1") if currency == "BDT" else Decimal("0"),
        "amount_cad": amount if currency == "CAD" else Decimal("0"),
        "amount_bdt": amount if currency == "BDT" else Decimal("0"),
    }


def _posted_event(
    spec: EventSpec,
    *,
    actor,
    batch_id: str,
    posted_at,
) -> ReceivableEvent:
    invoice = spec.invoice
    if spec.kind == ReceivableEvent.KIND_INVOICE_ISSUED:
        return ReceivableEvent(
            customer=invoice.customer,
            source_invoice=invoice,
            kind=spec.kind,
            state=ReceivableEvent.STATE_POSTED,
            event_date=invoice.effective_invoice_date,
            effective_date=invoice.effective_invoice_date,
            posted_at=posted_at,
            external_reference=invoice.invoice_number,
            idempotency_key=spec.key,
            reason="Historical invoice principal imported without changing the source invoice.",
            evidence_reference=f"Invoice:{invoice.pk}",
            migration_batch=batch_id,
            created_by=actor,
            approved_by=actor,
            approved_at=posted_at,
            **_invoice_snapshot(invoice),
        )
    payment = spec.payment
    entry = payment.accounting_entry
    return ReceivableEvent(
        customer=invoice.customer,
        source_invoice=invoice,
        legacy_invoice_payment=payment,
        accounting_entry=entry,
        kind=ReceivableEvent.KIND_CASH_RECEIPT,
        state=ReceivableEvent.STATE_POSTED,
        event_date=payment.payment_date,
        effective_date=payment.payment_date,
        posted_at=posted_at,
        native_amount=_money(payment.amount),
        currency=(payment.currency or "").upper().strip(),
        rate_to_cad=payment.rate_to_cad,
        rate_to_bdt=payment.rate_to_bdt,
        amount_cad=payment.amount_cad,
        amount_bdt=payment.amount_bdt,
        external_reference=(entry.transfer_ref or f"InvoicePayment:{payment.pk}").strip(),
        idempotency_key=spec.key,
        reason="Historical payment imported from the immutable InvoicePayment and AccountingEntry pair.",
        evidence_reference=f"InvoicePayment:{payment.pk};AccountingEntry:{entry.pk}",
        migration_batch=batch_id,
        created_by=actor,
        approved_by=actor,
        approved_at=posted_at,
    )


def _posted_allocation(
    spec: AllocationSpec,
    *,
    event: ReceivableEvent,
    actor,
    posted_at,
) -> ReceivableAllocation:
    return ReceivableAllocation(
        event=event,
        invoice=spec.invoice,
        state=ReceivableAllocation.STATE_POSTED,
        signed_amount=spec.amount,
        currency=(spec.invoice.currency or "").upper().strip(),
        allocation_date=spec.payment.payment_date,
        posted_at=posted_at,
        idempotency_key=spec.key,
        reason="Historical allocation imported from InvoicePayment.",
        created_by=actor,
        approved_by=actor,
        approved_at=posted_at,
    )


def _event_matches_spec(event: ReceivableEvent, spec: EventSpec) -> bool:
    if (
        event.kind != spec.kind
        or event.source_invoice_id != spec.invoice.pk
        or event.customer_id != spec.invoice.customer_id
        or event.currency != spec.currency
        or _money(event.native_amount) != spec.amount
        or event.state != ReceivableEvent.STATE_POSTED
    ):
        return False
    if spec.payment:
        payment = spec.payment
        return (
            event.legacy_invoice_payment_id == payment.pk
            and event.accounting_entry_id == payment.accounting_entry_id
            and event.event_date == payment.payment_date
            and event.effective_date == payment.payment_date
            and _decimal(event.rate_to_cad) == _decimal(payment.rate_to_cad)
            and _decimal(event.rate_to_bdt) == _decimal(payment.rate_to_bdt)
            and _money(event.amount_cad) == _money(payment.amount_cad)
            and _money(event.amount_bdt) == _money(payment.amount_bdt)
        )
    snapshot = _invoice_snapshot(spec.invoice)
    return (
        event.event_date == spec.invoice.effective_invoice_date
        and event.effective_date == spec.invoice.effective_invoice_date
        and event.legacy_invoice_payment_id is None
        and event.accounting_entry_id is None
        and event.reverses_event_id is None
        and _decimal(event.rate_to_cad) == snapshot["rate_to_cad"]
        and _decimal(event.rate_to_bdt) == snapshot["rate_to_bdt"]
        and _money(event.amount_cad) == snapshot["amount_cad"]
        and _money(event.amount_bdt) == snapshot["amount_bdt"]
    )


def _allocation_matches_spec(allocation: ReceivableAllocation, spec: AllocationSpec, event: ReceivableEvent) -> bool:
    return (
        allocation.event_id == event.pk
        and allocation.invoice_id == spec.invoice.pk
        and allocation.currency == (spec.invoice.currency or "").upper().strip()
        and allocation.allocation_date == spec.payment.payment_date
        and _money(allocation.signed_amount) == spec.amount
        and allocation.state == ReceivableAllocation.STATE_POSTED
    )


def _actual_ledger_totals(plan: HistoricalLedgerPlan) -> tuple[dict[str, Decimal], dict[str, Decimal]]:
    payment_totals = {
        row["currency"]: _money(row["total"])
        for row in ReceivableEvent.objects.filter(
            state=ReceivableEvent.STATE_POSTED,
            kind=ReceivableEvent.KIND_CASH_RECEIPT,
        )
        .values("currency")
        .annotate(total=Sum("native_amount"))
    }
    principals = {
        row["source_invoice_id"]: _money(row["total"])
        for row in ReceivableEvent.objects.filter(
            state=ReceivableEvent.STATE_POSTED,
            kind=ReceivableEvent.KIND_INVOICE_ISSUED,
            source_invoice_id__isnull=False,
        )
        .values("source_invoice_id")
        .annotate(total=Sum("native_amount"))
    }
    allocations = {
        row["invoice_id"]: _money(row["total"])
        for row in ReceivableAllocation.objects.filter(state=ReceivableAllocation.STATE_POSTED)
        .values("invoice_id")
        .annotate(total=Sum("signed_amount"))
    }
    outstanding_totals: dict[str, Decimal] = {}
    for invoice in plan.invoices:
        if invoice.status == "cancelled":
            continue
        outstanding = max(
            _money(principals.get(invoice.pk, Decimal("0")) - allocations.get(invoice.pk, Decimal("0"))),
            Decimal("0"),
        )
        if outstanding:
            _add_total(outstanding_totals, (invoice.currency or "").upper().strip(), outstanding)
    return outstanding_totals, payment_totals


def _reconciliation_rows(
    plan: HistoricalLedgerPlan,
    *,
    ledger_outstanding_totals: dict[str, Decimal] | None = None,
    ledger_payment_totals: dict[str, Decimal] | None = None,
) -> list[dict]:
    if ledger_outstanding_totals is None:
        ledger_outstanding_totals = plan.projected_ledger_outstanding_totals
    if ledger_payment_totals is None:
        ledger_payment_totals = plan.projected_ledger_payment_totals
    currencies = sorted(
        set(plan.current_outstanding_totals)
        | set(ledger_outstanding_totals)
        | set(plan.source_payment_totals)
        | set(ledger_payment_totals)
        | set(plan.linked_accounting_totals)
    )
    rows = []
    for currency in currencies:
        current_outstanding = _money(plan.current_outstanding_totals.get(currency))
        ledger_outstanding = _money(ledger_outstanding_totals.get(currency))
        recorded_payments = _money(plan.source_payment_totals.get(currency))
        ledger_payments = _money(ledger_payment_totals.get(currency))
        accounting_receipts = _money(plan.linked_accounting_totals.get(currency))
        outstanding_difference = _money(ledger_outstanding - current_outstanding)
        payment_difference = _money(ledger_payments - recorded_payments)
        accounting_difference = _money(ledger_payments - accounting_receipts)
        rows.append(
            {
                "currency": currency,
                "current_outstanding": current_outstanding,
                "ledger_outstanding": ledger_outstanding,
                "outstanding_difference": outstanding_difference,
                "recorded_payments": recorded_payments,
                "ledger_payments": ledger_payments,
                "payment_difference": payment_difference,
                "linked_accounting_receipts": accounting_receipts,
                "accounting_difference": accounting_difference,
                "reconciled": not any((outstanding_difference, payment_difference, accounting_difference)),
            }
        )
    return rows


def _event_counts(specs: list[EventSpec]) -> dict[str, int]:
    counts = defaultdict(int)
    for spec in specs:
        counts[spec.kind] += 1
    return dict(sorted(counts.items()))


def populate_historical_ledger(
    *,
    actor=None,
    batch_id="phase3c-historical-v1",
    apply=False,
    allow_exceptions=False,
) -> HistoricalLedgerResult:
    plan = build_historical_ledger_plan()
    if apply and not actor:
        raise ValueError("An authenticated actor is required when applying historical ledger records.")
    if apply and plan.blocking_exception_count and not allow_exceptions:
        raise ValueError(
            f"Historical ledger population has {plan.blocking_exception_count} blocking exceptions. "
            "Run a dry-run report or explicitly allow the safe subset on an isolated rehearsal copy."
        )

    events_created = defaultdict(int)
    events_reused = defaultdict(int)
    allocations_created = 0
    allocations_reused = 0

    if apply:
        with transaction.atomic():
            posted_at = timezone.now()
            events_by_key = {
                event.idempotency_key: event
                for event in ReceivableEvent.objects.select_related(
                    "source_invoice", "legacy_invoice_payment", "accounting_entry"
                ).filter(idempotency_key__in=[spec.key for spec in plan.event_specs])
            }
            new_event_specs = []
            for spec in plan.event_specs:
                event = events_by_key.get(spec.key)
                if event:
                    if not _event_matches_spec(event, spec):
                        raise ValueError(f"Existing ledger event {spec.key} conflicts with its historical source.")
                    events_reused[spec.kind] += 1
                else:
                    new_event_specs.append(spec)
                    events_created[spec.kind] += 1

            ReceivableEvent.objects.bulk_create(
                [
                    _posted_event(
                        spec,
                        actor=actor,
                        batch_id=batch_id,
                        posted_at=posted_at,
                    )
                    for spec in new_event_specs
                ],
                batch_size=200,
            )
            events_by_key = {
                event.idempotency_key: event
                for event in ReceivableEvent.objects.select_related(
                    "source_invoice", "legacy_invoice_payment", "accounting_entry"
                ).filter(idempotency_key__in=[spec.key for spec in plan.event_specs])
            }
            for spec in plan.event_specs:
                event = events_by_key.get(spec.key)
                if not event or not _event_matches_spec(event, spec):
                    raise ValueError(f"Ledger event {spec.key} does not match its historical source.")

            allocations_by_key = {
                allocation.idempotency_key: allocation
                for allocation in ReceivableAllocation.objects.select_related("event").filter(
                    idempotency_key__in=[spec.key for spec in plan.allocation_specs]
                )
            }
            new_allocation_specs = []
            for spec in plan.allocation_specs:
                event = events_by_key[spec.event_key]
                allocation = allocations_by_key.get(spec.key)
                if allocation:
                    if not _allocation_matches_spec(allocation, spec, event):
                        raise ValueError(f"Existing ledger allocation {spec.key} conflicts with its historical source.")
                    allocations_reused += 1
                    continue
                new_allocation_specs.append(spec)
                allocations_created += 1

            ReceivableAllocation.objects.bulk_create(
                [
                    _posted_allocation(
                        spec,
                        event=events_by_key[spec.event_key],
                        actor=actor,
                        posted_at=posted_at,
                    )
                    for spec in new_allocation_specs
                ],
                batch_size=300,
            )
            allocations_by_key = {
                allocation.idempotency_key: allocation
                for allocation in ReceivableAllocation.objects.select_related("event").filter(
                    idempotency_key__in=[spec.key for spec in plan.allocation_specs]
                )
            }
            for spec in plan.allocation_specs:
                event = events_by_key[spec.event_key]
                allocation = allocations_by_key.get(spec.key)
                if not allocation or not _allocation_matches_spec(allocation, spec, event):
                    raise ValueError(f"Ledger allocation {spec.key} does not match its historical source.")

    if apply:
        ledger_outstanding_totals, ledger_payment_totals = _actual_ledger_totals(plan)
    else:
        ledger_outstanding_totals = plan.projected_ledger_outstanding_totals
        ledger_payment_totals = plan.projected_ledger_payment_totals

    return HistoricalLedgerResult(
        dry_run=not apply,
        batch_id=batch_id,
        invoices_scanned=len(plan.invoices),
        payments_scanned=sum(len(invoice.payments.all()) for invoice in plan.invoices),
        events_planned=_event_counts(plan.event_specs),
        allocations_planned=len(plan.allocation_specs),
        events_created=dict(events_created),
        events_reused=dict(events_reused),
        allocations_created=allocations_created,
        allocations_reused=allocations_reused,
        exceptions=plan.exceptions,
        reconciliation=_reconciliation_rows(
            plan,
            ledger_outstanding_totals=ledger_outstanding_totals,
            ledger_payment_totals=ledger_payment_totals,
        ),
        source_capabilities={
            "invoice_issued": "Invoice.status sent/partial/paid with customer and positive total",
            "payment_received": "InvoicePayment plus exactly matching AccountingEntry",
            "partial_payment": "Derived from posted receipt allocations",
            "opening_balance": "Blocked until Finance supplies evidence for paid-amount gaps",
            "credit_note": "No canonical historical source model or entries found",
            "refund": "No canonical historical source model or entries found",
            "reversal": "No canonical historical source model or entries found",
        },
    )


def render_markdown_report(result: HistoricalLedgerResult) -> str:
    def safe(value) -> str:
        return str(value or "").replace("|", "\\|").replace("\n", " ")

    lines = [
        "# Phase 3C Historical Receivables Ledger Report",
        "",
        f"- Mode: {'DRY RUN' if result.dry_run else 'APPLY'}",
        f"- Batch: `{result.batch_id}`",
        f"- Invoices scanned: {result.invoices_scanned}",
        f"- Payments scanned: {result.payments_scanned}",
        f"- Events planned: {sum(result.events_planned.values())}",
        f"- Allocations planned: {result.allocations_planned}",
        f"- Blocking exceptions: {result.blocking_exception_count}",
        f"- Ready: {'yes' if result.ready else 'no'}",
        "",
        "## Event Counts",
        "",
        "| Event | Planned | Created | Reused |",
        "| --- | ---: | ---: | ---: |",
    ]
    kinds = sorted(set(result.events_planned) | set(result.events_created) | set(result.events_reused))
    for kind in kinds:
        lines.append(
            f"| {kind} | {result.events_planned.get(kind, 0)} | "
            f"{result.events_created.get(kind, 0)} | {result.events_reused.get(kind, 0)} |"
        )
    lines.extend(
        [
            "",
            "## Reconciliation",
            "",
            "| Currency | Current Outstanding | Ledger Outstanding | Difference | Recorded Payments | Ledger Payments | Payment Difference | Accounting Receipts | Accounting Difference |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in result.reconciliation:
        lines.append(
            "| {currency} | {current_outstanding} | {ledger_outstanding} | {outstanding_difference} | "
            "{recorded_payments} | {ledger_payments} | {payment_difference} | "
            "{linked_accounting_receipts} | {accounting_difference} |".format(**row)
        )
    lines.extend(
        [
            "",
            "## Exceptions",
            "",
            "| Severity | Code | Invoice | Customer | Amount | Reason | Recommended Action | Source |",
            "| --- | --- | --- | --- | ---: | --- | --- | --- |",
        ]
    )
    for row in result.exceptions:
        lines.append(
            f"| {safe(row.severity.upper())} | {safe(row.code)} | {safe(row.invoice)} | {safe(row.customer)} | "
            f"{safe(row.currency)} {row.amount} | {safe(row.reason)} | "
            f"{safe(row.recommended_action)} | {safe(row.source)} |"
        )
    if not result.exceptions:
        lines.append("| - | - | - | - | 0.00 | No exceptions. | - | - |")
    lines.extend(["", "## Source Capabilities", ""])
    for key, value in result.source_capabilities.items():
        lines.append(f"- `{key}`: {value}")
    return "\n".join(lines) + "\n"
