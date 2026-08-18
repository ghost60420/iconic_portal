from decimal import Decimal

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.signals import m2m_changed
from django.dispatch import receiver


SUPPORTED_CURRENCIES = (("CAD", "CAD"), ("USD", "USD"), ("BDT", "BDT"))
BUSINESS_SIDES = (("CA", "Canada"), ("BD", "Bangladesh"))


def _assert_fields_unchanged(instance, field_names, message):
    if not instance.pk:
        return
    previous = type(instance).objects.filter(pk=instance.pk).values(*field_names).first()
    if previous and any(getattr(instance, name) != previous[name] for name in field_names):
        raise ValidationError(message)


class FinancialAuditFields(models.Model):
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    modified_at = models.DateTimeField(auto_now=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    change_reason = models.TextField(blank=True, default="")

    class Meta:
        abstract = True


class FinancialAccount(FinancialAuditFields):
    TYPE_ASSET = "ASSET"
    TYPE_LIABILITY = "LIABILITY"
    TYPE_EQUITY = "EQUITY"
    TYPE_REVENUE = "REVENUE"
    TYPE_COGS = "COGS"
    TYPE_OPERATING_EXPENSE = "OPERATING_EXPENSE"
    TYPE_OTHER_INCOME = "OTHER_INCOME"
    TYPE_OTHER_EXPENSE = "OTHER_EXPENSE"
    TYPE_CHOICES = [
        (TYPE_ASSET, "Asset"),
        (TYPE_LIABILITY, "Liability"),
        (TYPE_EQUITY, "Equity"),
        (TYPE_REVENUE, "Revenue"),
        (TYPE_COGS, "Cost of goods sold"),
        (TYPE_OPERATING_EXPENSE, "Operating expense"),
        (TYPE_OTHER_INCOME, "Other income"),
        (TYPE_OTHER_EXPENSE, "Other expense"),
    ]
    NORMAL_DEBIT = "DEBIT"
    NORMAL_CREDIT = "CREDIT"
    NORMAL_CHOICES = ((NORMAL_DEBIT, "Debit"), (NORMAL_CREDIT, "Credit"))

    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=160)
    account_type = models.CharField(max_length=24, choices=TYPE_CHOICES, db_index=True)
    subtype = models.CharField(max_length=60, blank=True, default="", db_index=True)
    normal_balance = models.CharField(max_length=6, choices=NORMAL_CHOICES)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
    )
    is_active = models.BooleanField(default=True, db_index=True)
    is_control_account = models.BooleanField(default=False)
    allow_manual_posting = models.BooleanField(default=True)
    is_sensitive = models.BooleanField(default=False)
    system_key = models.CharField(max_length=60, blank=True, default="", unique=True, null=True)
    description = models.TextField(blank=True, default="")

    class Meta:
        ordering = ("code",)

    def save(self, *args, **kwargs):
        self.code = (self.code or "").upper().strip()
        self.system_key = (self.system_key or "").upper().strip() or None
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} - {self.name}"


class FinancialPeriod(FinancialAuditFields):
    STATE_OPEN = "OPEN"
    STATE_CLOSED = "CLOSED"
    STATE_LOCKED = "LOCKED"
    STATE_CHOICES = ((STATE_OPEN, "Open"), (STATE_CLOSED, "Closed"), (STATE_LOCKED, "Locked"))

    name = models.CharField(max_length=80)
    start_date = models.DateField()
    end_date = models.DateField()
    side = models.CharField(max_length=2, choices=BUSINESS_SIDES, blank=True, default="", db_index=True)
    state = models.CharField(max_length=8, choices=STATE_CHOICES, default=STATE_OPEN, db_index=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="closed_financial_periods",
    )
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-start_date", "side")
        constraints = [
            models.UniqueConstraint(fields=("start_date", "end_date", "side"), name="unique_fin_period_side"),
            models.CheckConstraint(condition=models.Q(end_date__gte=models.F("start_date")), name="fin_period_valid_dates"),
        ]

    def clean(self):
        if self.end_date and self.start_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "End date cannot precede start date."})

    def __str__(self):
        return f"{self.name} {self.side or 'ALL'} ({self.state})"


class HistoricalExchangeRate(FinancialAuditFields):
    rate_date = models.DateField(db_index=True)
    source_currency = models.CharField(max_length=3, choices=SUPPORTED_CURRENCIES)
    target_currency = models.CharField(max_length=3, choices=SUPPORTED_CURRENCIES)
    rate = models.DecimalField(max_digits=20, decimal_places=10)
    source_name = models.CharField(max_length=120)
    evidence_reference = models.CharField(max_length=255)
    is_approved = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ("-rate_date", "source_currency", "target_currency")
        constraints = [
            models.UniqueConstraint(
                fields=("rate_date", "source_currency", "target_currency"),
                name="unique_historical_exchange_rate",
            ),
            models.CheckConstraint(condition=models.Q(rate__gt=0), name="historical_rate_positive"),
            models.CheckConstraint(
                condition=~models.Q(source_currency=models.F("target_currency")),
                name="historical_rate_distinct_currency",
            ),
        ]

    def save(self, *args, **kwargs):
        self.source_currency = (self.source_currency or "").upper().strip()
        self.target_currency = (self.target_currency or "").upper().strip()
        if self.pk and type(self).objects.filter(pk=self.pk, is_approved=True).exists():
            raise ValidationError("Approved historical exchange rates are immutable; add a new evidenced rate.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.is_approved:
            raise ValidationError("Approved historical exchange rates cannot be deleted.")
        return super().delete(*args, **kwargs)


class CurrencyReviewItem(FinancialAuditFields):
    STATE_OPEN = "OPEN"
    STATE_RESOLVED = "RESOLVED"
    STATE_WAIVED = "WAIVED"
    STATE_CHOICES = ((STATE_OPEN, "Open"), (STATE_RESOLVED, "Resolved"), (STATE_WAIVED, "Waived"))

    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    object_id = models.PositiveBigIntegerField()
    source_record = GenericForeignKey("content_type", "object_id")
    transaction_date = models.DateField(db_index=True)
    source_currency = models.CharField(max_length=3, choices=SUPPORTED_CURRENCIES)
    required_currency = models.CharField(max_length=3, choices=SUPPORTED_CURRENCIES)
    native_amount = models.DecimalField(max_digits=18, decimal_places=2)
    state = models.CharField(max_length=10, choices=STATE_CHOICES, default=STATE_OPEN, db_index=True)
    reason = models.TextField()
    evidence_available = models.TextField(blank=True, default="")
    resolution = models.TextField(blank=True, default="")
    resolved_rate = models.ForeignKey(
        HistoricalExchangeRate,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="review_items",
    )

    class Meta:
        ordering = ("state", "transaction_date", "id")
        indexes = [models.Index(fields=("content_type", "object_id"), name="currency_review_source_idx")]


class CashBankAccount(FinancialAuditFields):
    KIND_BANK = "BANK"
    KIND_CASH = "CASH"
    KIND_PAYPAL = "PAYPAL"
    KIND_MOBILE = "MOBILE"
    KIND_OTHER = "OTHER"
    KIND_CHOICES = (
        (KIND_BANK, "Bank"),
        (KIND_CASH, "Cash in hand"),
        (KIND_PAYPAL, "PayPal"),
        (KIND_MOBILE, "Mobile payment"),
        (KIND_OTHER, "Other"),
    )

    name = models.CharField(max_length=160)
    kind = models.CharField(max_length=12, choices=KIND_CHOICES, db_index=True)
    side = models.CharField(max_length=2, choices=BUSINESS_SIDES, db_index=True)
    currency = models.CharField(max_length=3, choices=SUPPORTED_CURRENCIES, db_index=True)
    gl_account = models.OneToOneField(FinancialAccount, on_delete=models.PROTECT, related_name="cash_instrument")
    institution_name = models.CharField(max_length=160, blank=True, default="")
    masked_reference = models.CharField(max_length=80, blank=True, default="")
    is_active = models.BooleanField(default=True, db_index=True)
    is_sensitive = models.BooleanField(default=True)

    class Meta:
        ordering = ("side", "name")

    def __str__(self):
        return f"{self.name} ({self.currency})"


class JournalEntry(FinancialAuditFields):
    STATE_DRAFT = "DRAFT"
    STATE_POSTED = "POSTED"
    STATE_REVERSED = "REVERSED"
    STATE_CHOICES = ((STATE_DRAFT, "Draft"), (STATE_POSTED, "Posted"), (STATE_REVERSED, "Reversed"))

    journal_date = models.DateField(db_index=True)
    reference = models.CharField(max_length=100, unique=True)
    description = models.TextField()
    state = models.CharField(max_length=10, choices=STATE_CHOICES, default=STATE_DRAFT, db_index=True)
    side = models.CharField(max_length=2, choices=BUSINESS_SIDES, db_index=True)
    currency = models.CharField(max_length=3, choices=SUPPORTED_CURRENCIES, db_index=True)
    rate_to_cad = models.DecimalField(max_digits=20, decimal_places=10)
    rate_to_bdt = models.DecimalField(max_digits=20, decimal_places=10)
    period = models.ForeignKey(
        FinancialPeriod,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="journals",
    )
    source_content_type = models.ForeignKey(
        ContentType,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="financial_journal_sources",
    )
    source_object_id = models.PositiveBigIntegerField(null=True, blank=True)
    source_record = GenericForeignKey("source_content_type", "source_object_id")
    source_key = models.CharField(max_length=160, unique=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    reverses = models.OneToOneField(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reversal",
    )
    migration_batch = models.CharField(max_length=100, blank=True, default="", db_index=True)

    class Meta:
        ordering = ("-journal_date", "-id")
        indexes = [
            models.Index(fields=("state", "journal_date"), name="journal_state_date_idx"),
            models.Index(
                fields=("source_content_type", "source_object_id"),
                name="journal_source_idx",
            ),
        ]

    def save(self, *args, **kwargs):
        self.currency = (self.currency or "").upper().strip()
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values(
                "journal_date",
                "reference",
                "description",
                "side",
                "currency",
                "rate_to_cad",
                "rate_to_bdt",
                "period_id",
                "source_content_type_id",
                "source_object_id",
                "source_key",
                "reverses_id",
            ).first()
            if previous and type(self).objects.filter(pk=self.pk, state__in=(self.STATE_POSTED, self.STATE_REVERSED)).exists():
                current = {name: getattr(self, name) for name in previous}
                if current != previous:
                    raise ValidationError("Posted journal entries are immutable; use a reversal or adjustment.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.state != self.STATE_DRAFT:
            raise ValidationError("Posted journal entries cannot be deleted.")
        return super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.reference} ({self.state})"


class JournalLine(models.Model):
    journal = models.ForeignKey(JournalEntry, on_delete=models.PROTECT, related_name="lines")
    line_number = models.PositiveSmallIntegerField()
    account = models.ForeignKey(FinancialAccount, on_delete=models.PROTECT, related_name="journal_lines")
    description = models.CharField(max_length=255, blank=True, default="")
    native_debit = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    native_credit = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    cad_debit = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    cad_credit = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    bdt_debit = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0"))
    bdt_credit = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0"))
    customer = models.ForeignKey(
        "Customer", null=True, blank=True, on_delete=models.PROTECT, related_name="financial_journal_lines"
    )
    supplier = models.ForeignKey(
        "Supplier", null=True, blank=True, on_delete=models.PROTECT, related_name="financial_journal_lines"
    )
    production_order = models.ForeignKey(
        "ProductionOrder", null=True, blank=True, on_delete=models.PROTECT, related_name="financial_journal_lines"
    )
    department = models.ForeignKey(
        "Department", null=True, blank=True, on_delete=models.PROTECT, related_name="financial_journal_lines"
    )

    class Meta:
        ordering = ("journal_id", "line_number")
        constraints = [
            models.UniqueConstraint(fields=("journal", "line_number"), name="unique_journal_line_number"),
            models.CheckConstraint(
                condition=(
                    models.Q(native_debit__gt=0, native_credit=0)
                    | models.Q(native_credit__gt=0, native_debit=0)
                ),
                name="journal_line_one_native_side",
            ),
        ]
        indexes = [models.Index(fields=("account", "journal"), name="journal_line_account_idx")]

    def save(self, *args, **kwargs):
        if self.journal_id and JournalEntry.objects.filter(
            pk=self.journal_id, state__in=(JournalEntry.STATE_POSTED, JournalEntry.STATE_REVERSED)
        ).exists():
            raise ValidationError("Lines on a posted journal are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.journal_id and self.journal.state != JournalEntry.STATE_DRAFT:
            raise ValidationError("Lines on a posted journal cannot be deleted.")
        return super().delete(*args, **kwargs)


class FinancialAuditEvent(models.Model):
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    object_id = models.PositiveBigIntegerField()
    source_record = GenericForeignKey("content_type", "object_id")
    action = models.CharField(max_length=50, db_index=True)
    reason = models.TextField(blank=True, default="")
    before_value = models.JSONField(default=dict, blank=True)
    after_value = models.JSONField(default=dict, blank=True)
    source_reference = models.CharField(max_length=160, blank=True, default="")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [models.Index(fields=("content_type", "object_id"), name="fin_audit_source_idx")]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Financial audit history is immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Financial audit history cannot be deleted.")


class FinancialDocument(FinancialAuditFields):
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    object_id = models.PositiveBigIntegerField()
    source_record = GenericForeignKey("content_type", "object_id")
    file = models.FileField(upload_to="financial_core/%Y/%m/")
    document_type = models.CharField(max_length=50, blank=True, default="")
    description = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        indexes = [models.Index(fields=("content_type", "object_id"), name="fin_document_source_idx")]

    def save(self, *args, **kwargs):
        _assert_fields_unchanged(
            self,
            ("content_type_id", "object_id", "file", "document_type"),
            "Financial evidence is immutable; attach a superseding document instead.",
        )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Financial supporting documents cannot be deleted; attach superseding evidence.")


class InvoiceFinancialState(FinancialAuditFields):
    DOCUMENT_DRAFT = "DRAFT"
    DOCUMENT_ISSUED = "ISSUED"
    DOCUMENT_VOIDED = "VOIDED"
    DOCUMENT_CHOICES = ((DOCUMENT_DRAFT, "Draft"), (DOCUMENT_ISSUED, "Issued"), (DOCUMENT_VOIDED, "Voided"))
    APPROVAL_PENDING = "PENDING"
    APPROVAL_APPROVED = "APPROVED"
    APPROVAL_REJECTED = "REJECTED"
    APPROVAL_CHOICES = (
        (APPROVAL_PENDING, "Pending"),
        (APPROVAL_APPROVED, "Approved"),
        (APPROVAL_REJECTED, "Rejected"),
    )
    PAYMENT_UNPAID = "UNPAID"
    PAYMENT_PARTIAL = "PARTIAL"
    PAYMENT_SETTLED = "SETTLED"
    PAYMENT_CREDIT = "CREDIT"
    PAYMENT_REFUNDED = "REFUNDED"
    PAYMENT_CHOICES = (
        (PAYMENT_UNPAID, "Unpaid"),
        (PAYMENT_PARTIAL, "Partial"),
        (PAYMENT_SETTLED, "Settled"),
        (PAYMENT_CREDIT, "Credit"),
        (PAYMENT_REFUNDED, "Refunded"),
    )

    invoice = models.OneToOneField("Invoice", on_delete=models.PROTECT, related_name="financial_state")
    document_status = models.CharField(max_length=8, choices=DOCUMENT_CHOICES, default=DOCUMENT_DRAFT, db_index=True)
    approval_status = models.CharField(max_length=8, choices=APPROVAL_CHOICES, default=APPROVAL_PENDING, db_index=True)
    payment_status = models.CharField(max_length=8, choices=PAYMENT_CHOICES, default=PAYMENT_UNPAID, db_index=True)
    issued_date = models.DateField(null=True, blank=True, db_index=True)
    currency = models.CharField(max_length=3, choices=SUPPORTED_CURRENCIES)
    issued_native_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    rate_to_cad = models.DecimalField(max_digits=20, decimal_places=10, default=Decimal("0"))
    rate_to_bdt = models.DecimalField(max_digits=20, decimal_places=10, default=Decimal("0"))
    issued_amount_cad = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    issued_amount_bdt = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0"))
    receivable_journal = models.OneToOneField(
        JournalEntry,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="invoice_financial_state",
    )
    receivable_event = models.OneToOneField(
        "ReceivableEvent",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="invoice_financial_state",
    )


class Supplier(FinancialAuditFields):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=200, db_index=True)
    side = models.CharField(max_length=2, choices=BUSINESS_SIDES, db_index=True)
    default_currency = models.CharField(max_length=3, choices=SUPPORTED_CURRENCIES)
    contact_name = models.CharField(max_length=160, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    phone = models.CharField(max_length=80, blank=True, default="")
    tax_identifier = models.CharField(max_length=80, blank=True, default="")
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ("name", "code")

    def save(self, *args, **kwargs):
        self.code = (self.code or "").upper().strip()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} - {self.name}"


class SupplierBill(FinancialAuditFields):
    APPROVAL_DRAFT = "DRAFT"
    APPROVAL_PENDING = "PENDING"
    APPROVAL_APPROVED = "APPROVED"
    APPROVAL_REJECTED = "REJECTED"
    APPROVAL_REVERSED = "REVERSED"
    APPROVAL_CHOICES = (
        (APPROVAL_DRAFT, "Draft"),
        (APPROVAL_PENDING, "Pending"),
        (APPROVAL_APPROVED, "Approved"),
        (APPROVAL_REJECTED, "Rejected"),
        (APPROVAL_REVERSED, "Reversed"),
    )
    PAYMENT_UNPAID = "UNPAID"
    PAYMENT_PARTIAL = "PARTIAL"
    PAYMENT_PAID = "PAID"
    PAYMENT_CREDIT = "CREDIT"
    PAYMENT_REFUNDED = "REFUNDED"
    PAYMENT_CHOICES = (
        (PAYMENT_UNPAID, "Unpaid"),
        (PAYMENT_PARTIAL, "Partial"),
        (PAYMENT_PAID, "Paid"),
        (PAYMENT_CREDIT, "Credit"),
        (PAYMENT_REFUNDED, "Refunded"),
    )

    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="bills")
    bill_number = models.CharField(max_length=100)
    bill_date = models.DateField(db_index=True)
    due_date = models.DateField(db_index=True)
    currency = models.CharField(max_length=3, choices=SUPPORTED_CURRENCIES, db_index=True)
    amount_before_tax = models.DecimalField(max_digits=18, decimal_places=2)
    tax_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    total_amount = models.DecimalField(max_digits=18, decimal_places=2)
    rate_to_cad = models.DecimalField(max_digits=20, decimal_places=10)
    rate_to_bdt = models.DecimalField(max_digits=20, decimal_places=10)
    amount_cad = models.DecimalField(max_digits=18, decimal_places=2)
    amount_bdt = models.DecimalField(max_digits=20, decimal_places=2)
    expense_account = models.ForeignKey(FinancialAccount, on_delete=models.PROTECT, related_name="supplier_bills")
    production_order = models.ForeignKey(
        "ProductionOrder", null=True, blank=True, on_delete=models.PROTECT, related_name="supplier_bills"
    )
    department = models.ForeignKey(
        "Department", null=True, blank=True, on_delete=models.PROTECT, related_name="supplier_bills"
    )
    side = models.CharField(max_length=2, choices=BUSINESS_SIDES, db_index=True)
    description = models.TextField(blank=True, default="")
    approval_status = models.CharField(max_length=10, choices=APPROVAL_CHOICES, default=APPROVAL_DRAFT, db_index=True)
    payment_status = models.CharField(max_length=10, choices=PAYMENT_CHOICES, default=PAYMENT_UNPAID, db_index=True)
    paid_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    remaining_amount = models.DecimalField(max_digits=18, decimal_places=2)
    payable_journal = models.OneToOneField(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="supplier_bill"
    )

    class Meta:
        ordering = ("-bill_date", "-id")
        constraints = [
            models.UniqueConstraint(fields=("supplier", "bill_number"), name="unique_supplier_bill_number"),
            models.CheckConstraint(condition=models.Q(total_amount__gte=0), name="supplier_bill_total_nonnegative"),
            models.CheckConstraint(condition=models.Q(remaining_amount__gte=0), name="supplier_bill_remaining_nonnegative"),
        ]

    def clean(self):
        expected = (self.amount_before_tax or Decimal("0")) + (self.tax_amount or Decimal("0"))
        if self.total_amount != expected:
            raise ValidationError({"total_amount": "Total must equal amount before tax plus tax."})
        if self.due_date and self.bill_date and self.due_date < self.bill_date:
            raise ValidationError({"due_date": "Due date cannot precede bill date."})

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(
            pk=self.pk, approval_status__in=(self.APPROVAL_APPROVED, self.APPROVAL_REVERSED)
        ).exists():
            _assert_fields_unchanged(
                self,
                (
                    "supplier_id", "bill_number", "bill_date", "due_date", "currency",
                    "amount_before_tax", "tax_amount", "total_amount", "rate_to_cad",
                    "rate_to_bdt", "amount_cad", "amount_bdt", "expense_account_id",
                    "production_order_id", "department_id", "side", "description",
                    "payable_journal_id",
                ),
                "Approved supplier bill values are immutable; use a credit, refund, or reversal.",
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.approval_status in (self.APPROVAL_APPROVED, self.APPROVAL_REVERSED):
            raise ValidationError("Approved supplier bills cannot be deleted; use a reversal or credit.")
        return super().delete(*args, **kwargs)


class PayableEvent(FinancialAuditFields):
    KIND_BILL = "BILL"
    KIND_PAYMENT = "PAYMENT"
    KIND_CREDIT = "CREDIT"
    KIND_REFUND = "REFUND"
    KIND_OPENING = "OPENING"
    KIND_ADJUSTMENT = "ADJUSTMENT"
    KIND_REVERSAL = "REVERSAL"
    KIND_CHOICES = (
        (KIND_BILL, "Supplier bill"),
        (KIND_PAYMENT, "Supplier payment"),
        (KIND_CREDIT, "Supplier credit"),
        (KIND_REFUND, "Supplier refund"),
        (KIND_OPENING, "Opening balance"),
        (KIND_ADJUSTMENT, "Adjustment"),
        (KIND_REVERSAL, "Reversal"),
    )
    STATE_DRAFT = "DRAFT"
    STATE_POSTED = "POSTED"
    STATE_REVERSED = "REVERSED"
    STATE_CHOICES = ((STATE_DRAFT, "Draft"), (STATE_POSTED, "Posted"), (STATE_REVERSED, "Reversed"))

    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="payable_events")
    source_bill = models.ForeignKey(
        SupplierBill, null=True, blank=True, on_delete=models.PROTECT, related_name="payable_events"
    )
    kind = models.CharField(max_length=12, choices=KIND_CHOICES, db_index=True)
    state = models.CharField(max_length=10, choices=STATE_CHOICES, default=STATE_DRAFT, db_index=True)
    event_date = models.DateField(db_index=True)
    currency = models.CharField(max_length=3, choices=SUPPORTED_CURRENCIES, db_index=True)
    native_amount = models.DecimalField(max_digits=18, decimal_places=2)
    rate_to_cad = models.DecimalField(max_digits=20, decimal_places=10)
    rate_to_bdt = models.DecimalField(max_digits=20, decimal_places=10)
    amount_cad = models.DecimalField(max_digits=18, decimal_places=2)
    amount_bdt = models.DecimalField(max_digits=20, decimal_places=2)
    payment_account = models.ForeignKey(
        CashBankAccount, null=True, blank=True, on_delete=models.PROTECT, related_name="payable_events"
    )
    payment_method = models.CharField(max_length=30, blank=True, default="")
    reference = models.CharField(max_length=120)
    source_key = models.CharField(max_length=180, unique=True)
    journal = models.OneToOneField(JournalEntry, on_delete=models.PROTECT, related_name="payable_event")
    reverses_event = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversal_event"
    )
    evidence_reference = models.CharField(max_length=255, blank=True, default="")
    migration_batch = models.CharField(max_length=100, blank=True, default="", db_index=True)

    class Meta:
        ordering = ("-event_date", "-id")
        constraints = [models.CheckConstraint(condition=models.Q(native_amount__gt=0), name="payable_event_amount_positive")]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(
            pk=self.pk, state__in=(self.STATE_POSTED, self.STATE_REVERSED)
        ).exists():
            raise ValidationError("Posted payable events are immutable; create a reversal event.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.state != self.STATE_DRAFT:
            raise ValidationError("Posted payable events cannot be deleted.")
        return super().delete(*args, **kwargs)


class PayableAllocation(FinancialAuditFields):
    STATE_DRAFT = "DRAFT"
    STATE_POSTED = "POSTED"
    STATE_REVERSED = "REVERSED"
    STATE_CHOICES = ((STATE_DRAFT, "Draft"), (STATE_POSTED, "Posted"), (STATE_REVERSED, "Reversed"))

    event = models.ForeignKey(PayableEvent, on_delete=models.PROTECT, related_name="allocations")
    bill = models.ForeignKey(SupplierBill, on_delete=models.PROTECT, related_name="allocations")
    state = models.CharField(max_length=10, choices=STATE_CHOICES, default=STATE_DRAFT, db_index=True)
    allocation_date = models.DateField(db_index=True)
    native_amount = models.DecimalField(max_digits=18, decimal_places=2)
    source_key = models.CharField(max_length=180, unique=True)

    class Meta:
        ordering = ("allocation_date", "id")
        constraints = [models.CheckConstraint(condition=models.Q(native_amount__gt=0), name="payable_allocation_positive")]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(
            pk=self.pk, state__in=(self.STATE_POSTED, self.STATE_REVERSED)
        ).exists():
            raise ValidationError("Posted payable allocations are immutable; use a reversal.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.state != self.STATE_DRAFT:
            raise ValidationError("Posted payable allocations cannot be deleted.")
        return super().delete(*args, **kwargs)


class ExpenseCategory(FinancialAuditFields):
    name = models.CharField(max_length=120)
    subcategory = models.CharField(max_length=120, blank=True, default="")
    code = models.CharField(max_length=40, unique=True)
    default_account = models.ForeignKey(FinancialAccount, on_delete=models.PROTECT, related_name="expense_categories")
    is_production_cost = models.BooleanField(default=False, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ("name", "subcategory")
        constraints = [models.UniqueConstraint(fields=("name", "subcategory"), name="unique_expense_category_name")]

    def save(self, *args, **kwargs):
        self.code = (self.code or "").upper().strip()
        super().save(*args, **kwargs)

    def __str__(self):
        label = self.name
        if self.subcategory:
            label = f"{label} / {self.subcategory}"
        return f"{label} ({self.code})"


class ExpenseRecord(FinancialAuditFields):
    APPROVAL_DRAFT = "DRAFT"
    APPROVAL_PENDING = "PENDING"
    APPROVAL_APPROVED = "APPROVED"
    APPROVAL_REJECTED = "REJECTED"
    APPROVAL_REVERSED = "REVERSED"
    APPROVAL_CHOICES = SupplierBill.APPROVAL_CHOICES
    PAYMENT_UNPAID = "UNPAID"
    PAYMENT_PAID = "PAID"
    PAYMENT_PARTIAL = "PARTIAL"
    PAYMENT_CHOICES = (
        (PAYMENT_UNPAID, "Unpaid"),
        (PAYMENT_PARTIAL, "Partial"),
        (PAYMENT_PAID, "Paid"),
    )

    expense_number = models.CharField(max_length=50, unique=True)
    expense_date = models.DateField(db_index=True)
    vendor = models.ForeignKey(Supplier, null=True, blank=True, on_delete=models.PROTECT, related_name="expenses")
    vendor_name = models.CharField(max_length=200, blank=True, default="")
    category = models.ForeignKey(ExpenseCategory, on_delete=models.PROTECT, related_name="expenses")
    department = models.ForeignKey(
        "Department", null=True, blank=True, on_delete=models.PROTECT, related_name="expenses"
    )
    side = models.CharField(max_length=2, choices=BUSINESS_SIDES, db_index=True)
    currency = models.CharField(max_length=3, choices=SUPPORTED_CURRENCIES, db_index=True)
    amount_before_tax = models.DecimalField(max_digits=18, decimal_places=2)
    tax_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    total_amount = models.DecimalField(max_digits=18, decimal_places=2)
    rate_to_cad = models.DecimalField(max_digits=20, decimal_places=10)
    rate_to_bdt = models.DecimalField(max_digits=20, decimal_places=10)
    amount_cad = models.DecimalField(max_digits=18, decimal_places=2)
    amount_bdt = models.DecimalField(max_digits=20, decimal_places=2)
    payment_method = models.CharField(max_length=30, blank=True, default="")
    payment_account = models.ForeignKey(
        CashBankAccount, null=True, blank=True, on_delete=models.PROTECT, related_name="expenses"
    )
    payment_status = models.CharField(max_length=10, choices=PAYMENT_CHOICES, default=PAYMENT_UNPAID, db_index=True)
    due_date = models.DateField(null=True, blank=True, db_index=True)
    description = models.TextField()
    business_purpose = models.TextField(blank=True, default="")
    approval_status = models.CharField(max_length=10, choices=APPROVAL_CHOICES, default=APPROVAL_DRAFT, db_index=True)
    is_recurring_instance = models.BooleanField(default=False, db_index=True)
    recurring_template = models.ForeignKey(
        "RecurringExpenseTemplate", null=True, blank=True, on_delete=models.PROTECT, related_name="generated_expenses"
    )
    production_order = models.ForeignKey(
        "ProductionOrder", null=True, blank=True, on_delete=models.PROTECT, related_name="expenses"
    )
    supplier_bill = models.OneToOneField(
        SupplierBill, null=True, blank=True, on_delete=models.PROTECT, related_name="expense_record"
    )
    journal = models.OneToOneField(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="expense_record"
    )

    class Meta:
        ordering = ("-expense_date", "-id")

    def clean(self):
        expected = (self.amount_before_tax or Decimal("0")) + (self.tax_amount or Decimal("0"))
        if self.total_amount != expected:
            raise ValidationError({"total_amount": "Total must equal amount before tax plus tax."})
        if self.payment_status == self.PAYMENT_PAID and not self.payment_account_id:
            raise ValidationError({"payment_account": "Paid expenses require a cash or bank account."})

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(
            pk=self.pk, approval_status__in=(self.APPROVAL_APPROVED, self.APPROVAL_REVERSED)
        ).exists():
            _assert_fields_unchanged(
                self,
                (
                    "expense_number", "expense_date", "vendor_id", "vendor_name", "category_id",
                    "department_id", "side", "currency", "amount_before_tax", "tax_amount",
                    "total_amount", "rate_to_cad", "rate_to_bdt", "amount_cad", "amount_bdt",
                    "payment_method", "payment_account_id", "payment_status", "due_date",
                    "description", "business_purpose", "production_order_id", "supplier_bill_id",
                    "journal_id",
                ),
                "Approved expense values are immutable; use a reversal.",
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.approval_status in (self.APPROVAL_APPROVED, self.APPROVAL_REVERSED):
            raise ValidationError("Approved expenses cannot be deleted; use a reversal.")
        return super().delete(*args, **kwargs)


class RecurringExpenseTemplate(FinancialAuditFields):
    FREQ_WEEKLY = "WEEKLY"
    FREQ_MONTHLY = "MONTHLY"
    FREQ_QUARTERLY = "QUARTERLY"
    FREQ_ANNUAL = "ANNUAL"
    FREQ_CHOICES = (
        (FREQ_WEEKLY, "Weekly"),
        (FREQ_MONTHLY, "Monthly"),
        (FREQ_QUARTERLY, "Quarterly"),
        (FREQ_ANNUAL, "Annual"),
    )

    name = models.CharField(max_length=160)
    frequency = models.CharField(max_length=12, choices=FREQ_CHOICES, db_index=True)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    expected_amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3, choices=SUPPORTED_CURRENCIES)
    vendor = models.ForeignKey(Supplier, null=True, blank=True, on_delete=models.PROTECT, related_name="recurring_expenses")
    vendor_name = models.CharField(max_length=200, blank=True, default="")
    category = models.ForeignKey(ExpenseCategory, on_delete=models.PROTECT, related_name="recurring_templates")
    department = models.ForeignKey(
        "Department", null=True, blank=True, on_delete=models.PROTECT, related_name="recurring_expense_templates"
    )
    side = models.CharField(max_length=2, choices=BUSINESS_SIDES)
    reminder_days_before = models.PositiveSmallIntegerField(default=7)
    due_day = models.PositiveSmallIntegerField(default=1)
    last_generated_for = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    description = models.TextField(blank=True, default="")

    class Meta:
        ordering = ("name",)

    def clean(self):
        if not 1 <= self.due_day <= 28:
            raise ValidationError({"due_day": "Due day must be between 1 and 28."})
        if self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "End date cannot precede start date."})


class PayrollBatch(FinancialAuditFields):
    STATE_DRAFT = "DRAFT"
    STATE_PENDING = "PENDING"
    STATE_APPROVED = "APPROVED"
    STATE_PAID = "PAID"
    STATE_REVERSED = "REVERSED"
    STATE_CHOICES = (
        (STATE_DRAFT, "Draft"),
        (STATE_PENDING, "Pending"),
        (STATE_APPROVED, "Approved"),
        (STATE_PAID, "Paid"),
        (STATE_REVERSED, "Reversed"),
    )

    reference = models.CharField(max_length=80, unique=True)
    period_start = models.DateField()
    period_end = models.DateField()
    payment_date = models.DateField(null=True, blank=True, db_index=True)
    side = models.CharField(max_length=2, choices=BUSINESS_SIDES, db_index=True)
    currency = models.CharField(max_length=3, choices=SUPPORTED_CURRENCIES)
    department = models.ForeignKey(
        "Department", null=True, blank=True, on_delete=models.PROTECT, related_name="payroll_batches"
    )
    payment_account = models.ForeignKey(
        CashBankAccount, null=True, blank=True, on_delete=models.PROTECT, related_name="payroll_batches"
    )
    state = models.CharField(max_length=10, choices=STATE_CHOICES, default=STATE_DRAFT, db_index=True)
    journal = models.OneToOneField(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="payroll_batch"
    )

    class Meta:
        ordering = ("-period_end", "-id")

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.exclude(state__in=(self.STATE_DRAFT, self.STATE_PENDING)).filter(pk=self.pk).exists():
            raise ValidationError("Approved payroll is immutable; use controlled payment or reversal services.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.state not in (self.STATE_DRAFT, self.STATE_PENDING):
            raise ValidationError("Approved payroll cannot be deleted; use a reversal.")
        return super().delete(*args, **kwargs)


class PayrollLine(FinancialAuditFields):
    batch = models.ForeignKey(PayrollBatch, on_delete=models.PROTECT, related_name="private_lines")
    employee = models.ForeignKey(
        "EmployeeProfile", null=True, blank=True, on_delete=models.PROTECT, related_name="private_payroll_lines"
    )
    employee_reference = models.CharField(max_length=80)
    base_salary = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    overtime = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    bonus = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    commission = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    deductions = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    employer_cost = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    net_pay = models.DecimalField(max_digits=18, decimal_places=2)
    department = models.ForeignKey(
        "Department", null=True, blank=True, on_delete=models.PROTECT, related_name="private_payroll_lines"
    )

    class Meta:
        ordering = ("batch_id", "employee_reference")
        constraints = [models.UniqueConstraint(fields=("batch", "employee_reference"), name="unique_payroll_batch_employee")]

    def save(self, *args, **kwargs):
        if self.batch_id and PayrollBatch.objects.exclude(
            state__in=(PayrollBatch.STATE_DRAFT, PayrollBatch.STATE_PENDING)
        ).filter(pk=self.batch_id).exists():
            raise ValidationError("Lines on approved payroll are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.batch.state not in (PayrollBatch.STATE_DRAFT, PayrollBatch.STATE_PENDING):
            raise ValidationError("Lines on approved payroll cannot be deleted.")
        return super().delete(*args, **kwargs)


class ProductionCostRecord(FinancialAuditFields):
    CATEGORY_CHOICES = (
        ("FABRIC", "Fabric"),
        ("TRIMS", "Trims and accessories"),
        ("CUTTING", "Cutting"),
        ("SEWING", "Sewing"),
        ("PRINTING", "Printing"),
        ("EMBROIDERY", "Embroidery"),
        ("WASHING", "Washing"),
        ("PACKING", "Packing"),
        ("QUALITY_CONTROL", "Quality control"),
        ("SHIPPING", "Shipping"),
        ("DUTY", "Duty"),
        ("PRODUCTION_LABOR", "Production labor"),
        ("REWORK", "Rework"),
        ("WASTE", "Waste"),
        ("FACTORY_OVERHEAD", "Factory overhead"),
        ("OTHER_DIRECT", "Other direct cost"),
    )
    APPROVAL_CHOICES = SupplierBill.APPROVAL_CHOICES
    PAYMENT_CHOICES = SupplierBill.PAYMENT_CHOICES

    production_order = models.ForeignKey("ProductionOrder", on_delete=models.PROTECT, related_name="financial_costs")
    customer = models.ForeignKey(
        "Customer", null=True, blank=True, on_delete=models.PROTECT, related_name="production_financial_costs"
    )
    opportunity = models.ForeignKey(
        "Opportunity", null=True, blank=True, on_delete=models.PROTECT, related_name="production_financial_costs"
    )
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES, db_index=True)
    estimated_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    actual_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    currency = models.CharField(max_length=3, choices=SUPPORTED_CURRENCIES, db_index=True)
    rate_to_cad = models.DecimalField(max_digits=20, decimal_places=10, default=Decimal("0"))
    rate_to_bdt = models.DecimalField(max_digits=20, decimal_places=10, default=Decimal("0"))
    supplier = models.ForeignKey(Supplier, null=True, blank=True, on_delete=models.PROTECT, related_name="production_costs")
    supplier_bill = models.ForeignKey(
        SupplierBill, null=True, blank=True, on_delete=models.PROTECT, related_name="production_costs"
    )
    expense = models.ForeignKey(
        ExpenseRecord, null=True, blank=True, on_delete=models.PROTECT, related_name="production_costs"
    )
    approval_status = models.CharField(max_length=10, choices=APPROVAL_CHOICES, default=SupplierBill.APPROVAL_DRAFT, db_index=True)
    payment_status = models.CharField(max_length=10, choices=PAYMENT_CHOICES, default=SupplierBill.PAYMENT_UNPAID, db_index=True)
    cost_date = models.DateField(db_index=True)
    description = models.TextField(blank=True, default="")

    class Meta:
        ordering = ("production_order_id", "category", "cost_date", "id")

    @property
    def variance(self):
        return (self.actual_amount or Decimal("0")) - (self.estimated_amount or Decimal("0"))

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(
            pk=self.pk, approval_status__in=(SupplierBill.APPROVAL_APPROVED, SupplierBill.APPROVAL_REVERSED)
        ).exists():
            raise ValidationError("Approved production cost records are immutable; use a reversal.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.approval_status in (SupplierBill.APPROVAL_APPROVED, SupplierBill.APPROVAL_REVERSED):
            raise ValidationError("Approved production costs cannot be deleted; use a reversal.")
        return super().delete(*args, **kwargs)


class FactoryRunningCostDefault(FinancialAuditFields):
    name = models.CharField(max_length=120)
    side = models.CharField(max_length=2, choices=BUSINESS_SIDES, db_index=True)
    currency = models.CharField(max_length=3, choices=SUPPORTED_CURRENCIES)
    daily_amount = models.DecimalField(max_digits=18, decimal_places=2)
    effective_from = models.DateField(db_index=True)
    effective_to = models.DateField(null=True, blank=True)
    includes = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ("-effective_from", "side", "id")

    def clean(self):
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError({"effective_to": "Effective-to cannot precede effective-from."})

    def __str__(self):
        return f"{self.name} | {self.currency} {self.daily_amount:,.2f} per day"


class QuickCostingTimelineSnapshot(FinancialAuditFields):
    PRICING_CHOICES = (
        ("FOB", "FOB"),
        ("DOOR_TO_DOOR", "Door to Door"),
        ("CMT", "CMT or Sewing Only"),
        ("FULL_PACKAGE", "Full Package"),
        ("SAMPLE", "Sample"),
        ("OTHER", "Other"),
    )
    STATUS_GREEN = "GREEN"
    STATUS_YELLOW = "YELLOW"
    STATUS_RED = "RED"
    STATUS_CHOICES = ((STATUS_GREEN, "Green"), (STATUS_YELLOW, "Yellow"), (STATUS_RED, "Red"))

    quick_costing = models.OneToOneField(
        "QuickCosting", on_delete=models.PROTECT, related_name="factory_timeline"
    )
    pricing_type = models.CharField(max_length=20, choices=PRICING_CHOICES)
    estimated_production_days = models.PositiveIntegerField(default=0)
    actual_production_days = models.PositiveIntegerField(null=True, blank=True)
    daily_factory_cost = models.DecimalField(max_digits=18, decimal_places=2)
    daily_cost_currency = models.CharField(max_length=3, choices=SUPPORTED_CURRENCIES)
    estimated_timeline_cost = models.DecimalField(max_digits=18, decimal_places=2)
    actual_timeline_cost = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    estimated_profit = models.DecimalField(max_digits=18, decimal_places=2)
    actual_profit = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    target_margin_percent = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    approved_minimum_margin_percent = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    status = models.CharField(max_length=8, choices=STATUS_CHOICES, default=STATUS_GREEN, db_index=True)
    source_default = models.ForeignKey(
        FactoryRunningCostDefault, null=True, blank=True, on_delete=models.PROTECT, related_name="costing_snapshots"
    )
    locked_at = models.DateTimeField(null=True, blank=True)

    @property
    def timeline_variance(self):
        if self.actual_production_days is None:
            return None
        return self.actual_production_days - self.estimated_production_days

    @property
    def profit_variance(self):
        if self.actual_profit is None:
            return None
        return self.actual_profit - self.estimated_profit

    def save(self, *args, **kwargs):
        if self.pk:
            locked = type(self).objects.filter(pk=self.pk, locked_at__isnull=False).values(
                "daily_factory_cost", "daily_cost_currency", "estimated_production_days", "estimated_timeline_cost"
            ).first()
            if locked:
                current = {name: getattr(self, name) for name in locked}
                if current != locked:
                    raise ValidationError("Approved factory timeline snapshots cannot change their estimated rate or cost.")
        super().save(*args, **kwargs)


class FinancialBudget(FinancialAuditFields):
    year = models.PositiveIntegerField()
    month = models.PositiveSmallIntegerField()
    side = models.CharField(max_length=2, choices=BUSINESS_SIDES, blank=True, default="", db_index=True)
    department = models.ForeignKey(
        "Department", null=True, blank=True, on_delete=models.PROTECT, related_name="financial_budgets"
    )
    account = models.ForeignKey(FinancialAccount, on_delete=models.PROTECT, related_name="budgets")
    currency = models.CharField(max_length=3, choices=SUPPORTED_CURRENCIES)
    amount = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta:
        ordering = ("-year", "-month", "side", "account__code")
        constraints = [
            models.UniqueConstraint(
                fields=("year", "month", "side", "department", "account", "currency"),
                name="unique_financial_budget",
            ),
            models.CheckConstraint(condition=models.Q(month__gte=1, month__lte=12), name="financial_budget_valid_month"),
        ]


class BankReconciliation(FinancialAuditFields):
    STATE_DRAFT = "DRAFT"
    STATE_SUBMITTED = "SUBMITTED"
    STATE_APPROVED = "APPROVED"
    STATE_REVERSED = "REVERSED"
    STATE_CHOICES = (
        (STATE_DRAFT, "Draft"),
        (STATE_SUBMITTED, "Submitted"),
        (STATE_APPROVED, "Approved"),
        (STATE_REVERSED, "Reversed"),
    )

    account = models.ForeignKey(CashBankAccount, on_delete=models.PROTECT, related_name="reconciliations")
    statement_start_date = models.DateField()
    statement_end_date = models.DateField(db_index=True)
    statement_opening_balance = models.DecimalField(max_digits=18, decimal_places=2)
    statement_closing_balance = models.DecimalField(max_digits=18, decimal_places=2)
    book_closing_balance = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    unexplained_difference = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    state = models.CharField(max_length=12, choices=STATE_CHOICES, default=STATE_DRAFT, db_index=True)
    reconciliation_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ("-statement_end_date", "account_id")
        constraints = [
            models.UniqueConstraint(fields=("account", "statement_end_date"), name="unique_bank_reconciliation_period"),
            models.CheckConstraint(
                condition=models.Q(statement_end_date__gte=models.F("statement_start_date")),
                name="bank_reconciliation_valid_dates",
            ),
        ]

    def delete(self, *args, **kwargs):
        if self.state in (self.STATE_APPROVED, self.STATE_REVERSED):
            raise ValidationError("Approved reconciliations cannot be deleted.")
        return super().delete(*args, **kwargs)


class BankStatementLine(FinancialAuditFields):
    reconciliation = models.ForeignKey(BankReconciliation, on_delete=models.PROTECT, related_name="statement_lines")
    transaction_date = models.DateField(db_index=True)
    reference = models.CharField(max_length=160)
    description = models.CharField(max_length=255, blank=True, default="")
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    is_imported = models.BooleanField(default=False)
    is_matched = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ("transaction_date", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("reconciliation", "transaction_date", "reference", "amount"),
                name="unique_bank_statement_line",
            )
        ]


class BankReconciliationMatch(FinancialAuditFields):
    statement_line = models.ForeignKey(BankStatementLine, on_delete=models.PROTECT, related_name="matches")
    journal_line = models.ForeignKey(JournalLine, on_delete=models.PROTECT, related_name="bank_matches")
    matched_amount = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta:
        ordering = ("statement_line_id", "id")
        constraints = [
            models.UniqueConstraint(fields=("statement_line", "journal_line"), name="unique_bank_reconciliation_match"),
            models.CheckConstraint(condition=~models.Q(matched_amount=0), name="bank_match_nonzero"),
        ]


class FinanceOperation(FinancialAuditFields):
    TYPE_CUSTOMER_PAYMENT = "CUSTOMER_PAYMENT"
    TYPE_CUSTOMER_REFUND = "CUSTOMER_REFUND"
    TYPE_CUSTOMER_CREDIT_NOTE = "CUSTOMER_CREDIT_NOTE"
    TYPE_CUSTOMER_CREDIT = "CUSTOMER_CREDIT"
    TYPE_SUPPLIER_BILL = "SUPPLIER_BILL"
    TYPE_SUPPLIER_PAYMENT = "SUPPLIER_PAYMENT"
    TYPE_COMPANY_EXPENSE = "COMPANY_EXPENSE"
    TYPE_UTILITY_BILL = "UTILITY_BILL"
    TYPE_PAYROLL = "PAYROLL"
    TYPE_PRODUCTION_COST = "PRODUCTION_COST"
    TYPE_FACTORY_DAILY_COST = "FACTORY_DAILY_COST"
    TYPE_BANK_DEPOSIT = "BANK_DEPOSIT"
    TYPE_BANK_WITHDRAWAL = "BANK_WITHDRAWAL"
    TYPE_CASH_DEPOSIT = "CASH_DEPOSIT"
    TYPE_CASH_WITHDRAWAL = "CASH_WITHDRAWAL"
    TYPE_ACCOUNT_TRANSFER = "ACCOUNT_TRANSFER"
    TYPE_BANK_FEE = "BANK_FEE"
    TYPE_PROCESSOR_FEE = "PROCESSOR_FEE"
    TYPE_OWNER_INVESTMENT = "OWNER_INVESTMENT"
    TYPE_OWNER_WITHDRAWAL = "OWNER_WITHDRAWAL"
    TYPE_LOAN_RECEIVED = "LOAN_RECEIVED"
    TYPE_LOAN_PRINCIPAL = "LOAN_PRINCIPAL"
    TYPE_LOAN_INTEREST = "LOAN_INTEREST"
    TYPE_SHAREHOLDER_ADVANCE = "SHAREHOLDER_ADVANCE"
    TYPE_SHAREHOLDER_REPAYMENT = "SHAREHOLDER_REPAYMENT"
    TYPE_ASSET_PURCHASE = "ASSET_PURCHASE"
    TYPE_INVENTORY_ADJUSTMENT = "INVENTORY_ADJUSTMENT"
    TYPE_CHOICES = (
        (TYPE_CUSTOMER_PAYMENT, "Customer payment"),
        (TYPE_CUSTOMER_REFUND, "Customer refund"),
        (TYPE_CUSTOMER_CREDIT_NOTE, "Customer credit note"),
        (TYPE_CUSTOMER_CREDIT, "Unapplied customer credit"),
        (TYPE_SUPPLIER_BILL, "Supplier bill"),
        (TYPE_SUPPLIER_PAYMENT, "Supplier payment"),
        (TYPE_COMPANY_EXPENSE, "Company expense"),
        (TYPE_UTILITY_BILL, "Utility bill"),
        (TYPE_PAYROLL, "Payroll expense"),
        (TYPE_PRODUCTION_COST, "Production cost"),
        (TYPE_FACTORY_DAILY_COST, "Factory daily cost"),
        (TYPE_BANK_DEPOSIT, "Bank deposit"),
        (TYPE_BANK_WITHDRAWAL, "Bank withdrawal"),
        (TYPE_CASH_DEPOSIT, "Cash deposit"),
        (TYPE_CASH_WITHDRAWAL, "Cash withdrawal"),
        (TYPE_ACCOUNT_TRANSFER, "Company account transfer"),
        (TYPE_BANK_FEE, "Bank fee"),
        (TYPE_PROCESSOR_FEE, "Payment processor fee"),
        (TYPE_OWNER_INVESTMENT, "Owner investment"),
        (TYPE_OWNER_WITHDRAWAL, "Owner withdrawal"),
        (TYPE_LOAN_RECEIVED, "Loan received"),
        (TYPE_LOAN_PRINCIPAL, "Loan principal payment"),
        (TYPE_LOAN_INTEREST, "Loan interest payment"),
        (TYPE_SHAREHOLDER_ADVANCE, "Shareholder advance"),
        (TYPE_SHAREHOLDER_REPAYMENT, "Shareholder repayment"),
        (TYPE_ASSET_PURCHASE, "Asset purchase"),
        (TYPE_INVENTORY_ADJUSTMENT, "Inventory adjustment"),
    )

    STATE_DRAFT = "DRAFT"
    STATE_PENDING = "PENDING"
    STATE_EVIDENCE_REQUIRED = "EVIDENCE_REQUIRED"
    STATE_APPROVED = "APPROVED"
    STATE_REJECTED = "REJECTED"
    STATE_POSTED = "POSTED"
    STATE_REVERSED = "REVERSED"
    STATE_CHOICES = (
        (STATE_DRAFT, "Draft"),
        (STATE_PENDING, "Pending approval"),
        (STATE_EVIDENCE_REQUIRED, "Evidence required"),
        (STATE_APPROVED, "Approved, awaiting posting"),
        (STATE_REJECTED, "Rejected"),
        (STATE_POSTED, "Posted"),
        (STATE_REVERSED, "Reversed"),
    )
    RISK_STANDARD = "STANDARD"
    RISK_HIGH = "HIGH"
    RISK_CHOICES = ((RISK_STANDARD, "Standard"), (RISK_HIGH, "High"))

    operation_number = models.CharField(max_length=40, unique=True)
    operation_type = models.CharField(max_length=32, choices=TYPE_CHOICES, db_index=True)
    state = models.CharField(max_length=24, choices=STATE_CHOICES, default=STATE_DRAFT, db_index=True)
    risk_level = models.CharField(max_length=10, choices=RISK_CHOICES, default=RISK_STANDARD, db_index=True)
    transaction_date = models.DateField(db_index=True)
    side = models.CharField(max_length=2, choices=BUSINESS_SIDES, db_index=True)
    currency = models.CharField(max_length=3, choices=SUPPORTED_CURRENCIES, db_index=True)
    amount_before_tax = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    tax_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    total_amount = models.DecimalField(max_digits=18, decimal_places=2)
    rate_to_cad = models.DecimalField(max_digits=20, decimal_places=10)
    rate_to_bdt = models.DecimalField(max_digits=20, decimal_places=10)
    amount_cad = models.DecimalField(max_digits=18, decimal_places=2)
    amount_bdt = models.DecimalField(max_digits=20, decimal_places=2)
    customer = models.ForeignKey(
        "Customer", null=True, blank=True, on_delete=models.PROTECT, related_name="finance_operations"
    )
    invoice = models.ForeignKey(
        "Invoice", null=True, blank=True, on_delete=models.PROTECT, related_name="finance_operations"
    )
    supplier = models.ForeignKey(
        Supplier, null=True, blank=True, on_delete=models.PROTECT, related_name="finance_operations"
    )
    supplier_bill = models.ForeignKey(
        SupplierBill, null=True, blank=True, on_delete=models.PROTECT, related_name="finance_operations"
    )
    employee = models.ForeignKey(
        "EmployeeProfile", null=True, blank=True, on_delete=models.PROTECT, related_name="finance_operations"
    )
    department = models.ForeignKey(
        "Department", null=True, blank=True, on_delete=models.PROTECT, related_name="finance_operations"
    )
    production_order = models.ForeignKey(
        "ProductionOrder", null=True, blank=True, on_delete=models.PROTECT, related_name="finance_operations"
    )
    opportunity = models.ForeignKey(
        "Opportunity", null=True, blank=True, on_delete=models.PROTECT, related_name="finance_operations"
    )
    expense_category = models.ForeignKey(
        ExpenseCategory, null=True, blank=True, on_delete=models.PROTECT, related_name="finance_operations"
    )
    from_account = models.ForeignKey(
        CashBankAccount, null=True, blank=True, on_delete=models.PROTECT, related_name="outgoing_finance_operations"
    )
    to_account = models.ForeignKey(
        CashBankAccount, null=True, blank=True, on_delete=models.PROTECT, related_name="incoming_finance_operations"
    )
    reference = models.CharField(max_length=120, blank=True, default="", db_index=True)
    payment_method = models.CharField(max_length=30, blank=True, default="")
    party_name = models.CharField(max_length=200, blank=True, default="")
    business_purpose = models.TextField(blank=True, default="")
    reason = models.TextField(blank=True, default="")
    notes = models.TextField(blank=True, default="")
    details = models.JSONField(default=dict, blank=True)
    duplicate_warning = models.BooleanField(default=False, db_index=True)
    duplicate_warning_text = models.TextField(blank=True, default="")
    posting_preview = models.JSONField(default=dict, blank=True)
    posting_error = models.TextField(blank=True, default="")
    posting_attempted_at = models.DateTimeField(null=True, blank=True)
    posted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="posted_finance_operations"
    )
    primary_journal = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="finance_operations"
    )
    source_content_type = models.ForeignKey(
        ContentType, null=True, blank=True, on_delete=models.PROTECT, related_name="finance_operation_sources"
    )
    source_object_id = models.PositiveBigIntegerField(null=True, blank=True)
    source_record = GenericForeignKey("source_content_type", "source_object_id")
    documents = GenericRelation(FinancialDocument, related_query_name="finance_operations")

    class Meta:
        ordering = ("-transaction_date", "-id")
        indexes = [
            models.Index(fields=("operation_type", "state"), name="fin_ops_type_state_idx"),
            models.Index(fields=("side", "transaction_date"), name="fin_ops_side_date_idx"),
            models.Index(fields=("department", "state"), name="fin_ops_dept_state_idx"),
        ]
        constraints = [
            models.CheckConstraint(condition=models.Q(total_amount__gt=0), name="finance_operation_total_positive"),
            models.CheckConstraint(condition=models.Q(rate_to_cad__gt=0), name="finance_operation_cad_rate_positive"),
            models.CheckConstraint(condition=models.Q(rate_to_bdt__gt=0), name="finance_operation_bdt_rate_positive"),
        ]

    @property
    def finance_status_label(self):
        return {
            self.STATE_DRAFT: "Draft",
            self.STATE_PENDING: "Pending Approval",
            self.STATE_EVIDENCE_REQUIRED: "Returned",
            self.STATE_REJECTED: "Rejected",
            self.STATE_APPROVED: "Approved, Awaiting Posting",
            self.STATE_POSTED: "Posted",
            self.STATE_REVERSED: "Reversed",
        }.get(self.state, self.get_state_display())

    def clean(self):
        if self.operation_type in {
            self.TYPE_SUPPLIER_BILL,
            self.TYPE_COMPANY_EXPENSE,
            self.TYPE_UTILITY_BILL,
        }:
            expected = (self.amount_before_tax or Decimal("0")) + (self.tax_amount or Decimal("0"))
            if self.total_amount != expected:
                raise ValidationError({"total_amount": "Total must equal amount before tax plus tax."})
        if self.invoice_id and self.customer_id and self.invoice.customer_id != self.customer_id:
            raise ValidationError({"invoice": "The selected invoice does not belong to this customer."})
        if self.supplier_bill_id and self.supplier_id and self.supplier_bill.supplier_id != self.supplier_id:
            raise ValidationError({"supplier_bill": "The selected bill does not belong to this supplier."})
        if self.from_account_id and self.to_account_id and self.from_account_id == self.to_account_id:
            raise ValidationError({"to_account": "From and to accounts must be different."})
        for field_name in ("from_account", "to_account"):
            account = getattr(self, field_name, None)
            is_transfer_destination = (
                field_name == "to_account" and self.operation_type == self.TYPE_ACCOUNT_TRANSFER
            )
            if account and not is_transfer_destination and account.side != self.side:
                raise ValidationError({field_name: "The account must belong to the selected business side."})
            if account and not is_transfer_destination and account.currency != self.currency:
                raise ValidationError({field_name: "The account currency must match the transaction currency."})

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.exclude(
            state__in=(self.STATE_DRAFT, self.STATE_PENDING, self.STATE_EVIDENCE_REQUIRED)
        ).filter(pk=self.pk).exists():
            _assert_fields_unchanged(
                self,
                (
                    "operation_number", "operation_type", "risk_level", "transaction_date", "side", "currency",
                    "amount_before_tax", "tax_amount", "total_amount", "rate_to_cad", "rate_to_bdt", "amount_cad",
                    "amount_bdt", "customer_id", "invoice_id", "supplier_id", "supplier_bill_id", "employee_id",
                    "department_id", "production_order_id", "opportunity_id", "expense_category_id",
                    "from_account_id", "to_account_id", "reference", "payment_method", "party_name",
                    "business_purpose", "reason", "details", "source_content_type_id", "source_object_id",
                ),
                "Approved finance operations are immutable; use a reversal or a new correcting operation.",
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.state != self.STATE_DRAFT:
            raise ValidationError("Submitted finance operations cannot be deleted; reject or reverse them.")
        return super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.operation_number} - {self.get_operation_type_display()}"


class FinancialExceptionReview(FinancialAuditFields):
    SEVERITY_CRITICAL = "CRITICAL"
    SEVERITY_HIGH = "HIGH"
    SEVERITY_CHOICES = ((SEVERITY_CRITICAL, "Critical"), (SEVERITY_HIGH, "High"))

    STATUS_OPEN = "OPEN"
    STATUS_EVIDENCE_REQUIRED = "EVIDENCE_REQUIRED"
    STATUS_APPROVED = "APPROVED"
    STATUS_REJECTED = "REJECTED"
    STATUS_INCOMPLETE = "INCOMPLETE"
    STATUS_DEFERRED = "DEFERRED"
    STATUS_RESOLVED = "RESOLVED"
    STATUS_CHOICES = (
        (STATUS_OPEN, "Open"),
        (STATUS_EVIDENCE_REQUIRED, "Evidence required"),
        (STATUS_APPROVED, "Correction approved"),
        (STATUS_REJECTED, "Proposed correction rejected"),
        (STATUS_INCOMPLETE, "Historical data incomplete"),
        (STATUS_DEFERRED, "Deferred"),
        (STATUS_RESOLVED, "Resolved"),
    )

    exception_id = models.CharField(max_length=32, unique=True)
    source_key = models.CharField(max_length=64, unique=True)
    severity = models.CharField(max_length=10, choices=SEVERITY_CHOICES, db_index=True)
    area = models.CharField(max_length=80, db_index=True)
    exception_type = models.CharField(max_length=100, db_index=True)
    record_type = models.CharField(max_length=80, db_index=True)
    record_number = models.CharField(max_length=120, db_index=True)
    customer = models.ForeignKey(
        "Customer", null=True, blank=True, on_delete=models.PROTECT, related_name="financial_exception_reviews"
    )
    supplier = models.ForeignKey(
        Supplier, null=True, blank=True, on_delete=models.PROTECT, related_name="financial_exception_reviews"
    )
    counterparty_name = models.CharField(max_length=200, blank=True, default="", db_index=True)
    transaction_date = models.DateField(null=True, blank=True, db_index=True)
    side = models.CharField(max_length=2, choices=BUSINESS_SIDES, blank=True, default="", db_index=True)
    currency = models.CharField(max_length=3, blank=True, default="", db_index=True)
    current_value = models.TextField()
    expected_value = models.TextField()
    difference = models.TextField()
    reason = models.TextField()
    evidence_available = models.TextField(blank=True, default="")
    evidence_needed = models.TextField()
    recommended_action = models.TextField()
    review_status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=STATUS_OPEN, db_index=True)
    review_notes = models.TextField(blank=True, default="")
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_financial_exceptions",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    source_snapshot = models.JSONField(default=dict, blank=True)
    documents = GenericRelation(
        FinancialDocument,
        content_type_field="content_type",
        object_id_field="object_id",
        related_query_name="exception_reviews",
    )

    SOURCE_FIELDS = (
        "exception_id", "source_key", "severity", "area", "exception_type", "record_type", "record_number",
        "customer_id", "supplier_id", "counterparty_name", "transaction_date", "side", "currency",
        "current_value", "expected_value", "difference", "reason", "evidence_available", "evidence_needed",
        "recommended_action", "source_snapshot",
    )

    class Meta:
        ordering = ("severity", "area", "exception_id")
        indexes = [
            models.Index(fields=("severity", "review_status"), name="fin_exc_severity_status_idx"),
            models.Index(fields=("area", "review_status"), name="fin_exc_area_status_idx"),
            models.Index(fields=("side", "currency"), name="fin_exc_side_currency_idx"),
        ]

    def save(self, *args, **kwargs):
        _assert_fields_unchanged(self, self.SOURCE_FIELDS, "Imported exception source values are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Historical exception reviews cannot be deleted.")

    def __str__(self):
        return f"{self.exception_id} - {self.record_type} {self.record_number}"


class FinancialAdjustmentRequest(FinancialAuditFields):
    TYPE_MANUAL = "MANUAL"
    TYPE_OPENING = "OPENING"
    TYPE_HISTORICAL = "HISTORICAL"
    TYPE_REVERSAL = "REVERSAL"
    TYPE_RECEIVABLE = "RECEIVABLE"
    TYPE_PAYABLE = "PAYABLE"
    TYPE_CURRENCY = "CURRENCY"
    TYPE_CHOICES = (
        (TYPE_MANUAL, "Manual journal"),
        (TYPE_OPENING, "Opening balance"),
        (TYPE_HISTORICAL, "Historical adjustment"),
        (TYPE_REVERSAL, "Journal reversal"),
        (TYPE_RECEIVABLE, "Receivable correction"),
        (TYPE_PAYABLE, "Payable correction"),
        (TYPE_CURRENCY, "Currency snapshot correction"),
    )

    STATE_DRAFT = "DRAFT"
    STATE_SUBMITTED = "SUBMITTED"
    STATE_APPROVED = "APPROVED"
    STATE_REJECTED = "REJECTED"
    STATE_POSTED = "POSTED"
    STATE_REVERSED = "REVERSED"
    STATE_CHOICES = (
        (STATE_DRAFT, "Draft"),
        (STATE_SUBMITTED, "Submitted"),
        (STATE_APPROVED, "Approved"),
        (STATE_REJECTED, "Rejected"),
        (STATE_POSTED, "Posted"),
        (STATE_REVERSED, "Reversed"),
    )

    request_id = models.CharField(max_length=40, unique=True)
    exception = models.ForeignKey(
        FinancialExceptionReview, null=True, blank=True, on_delete=models.PROTECT, related_name="adjustment_requests"
    )
    adjustment_type = models.CharField(max_length=16, choices=TYPE_CHOICES, db_index=True)
    state = models.CharField(max_length=12, choices=STATE_CHOICES, default=STATE_DRAFT, db_index=True)
    journal_date = models.DateField(db_index=True)
    side = models.CharField(max_length=2, choices=BUSINESS_SIDES, db_index=True)
    currency = models.CharField(max_length=3, choices=SUPPORTED_CURRENCIES, db_index=True)
    native_amount = models.DecimalField(max_digits=18, decimal_places=2)
    rate_to_cad = models.DecimalField(max_digits=20, decimal_places=10)
    rate_to_bdt = models.DecimalField(max_digits=20, decimal_places=10)
    debit_account = models.ForeignKey(
        FinancialAccount, on_delete=models.PROTECT, related_name="debit_adjustment_requests"
    )
    credit_account = models.ForeignKey(
        FinancialAccount, on_delete=models.PROTECT, related_name="credit_adjustment_requests"
    )
    reason = models.TextField()
    before_values = models.JSONField(default=dict)
    after_values = models.JSONField(default=dict)
    approval_notes = models.TextField(blank=True, default="")
    evidence_documents = models.ManyToManyField(FinancialDocument, related_name="adjustment_requests", blank=True)
    source_journal = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="adjustment_requests_to_reverse"
    )
    journal = models.OneToOneField(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="adjustment_request"
    )

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=("state", "adjustment_type"), name="fin_adjust_state_type_idx"),
            models.Index(fields=("side", "journal_date"), name="fin_adjust_side_date_idx"),
        ]
        constraints = [
            models.CheckConstraint(condition=models.Q(native_amount__gt=0), name="financial_adjustment_positive"),
            models.CheckConstraint(
                condition=~models.Q(debit_account=models.F("credit_account")),
                name="financial_adjustment_distinct_accounts",
            ),
        ]

    def clean(self):
        if self.debit_account_id and self.credit_account_id and self.debit_account_id == self.credit_account_id:
            raise ValidationError("Debit and credit accounts must be different.")
        if self.adjustment_type == self.TYPE_REVERSAL and not self.source_journal_id:
            raise ValidationError({"source_journal": "A reversal requires the original posted journal."})

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.exclude(state=self.STATE_DRAFT).filter(pk=self.pk).exists():
            _assert_fields_unchanged(
                self,
                (
                    "request_id", "exception_id", "adjustment_type", "journal_date", "side", "currency",
                    "native_amount", "rate_to_cad", "rate_to_bdt", "debit_account_id", "credit_account_id",
                    "reason", "before_values", "after_values", "source_journal_id",
                ),
                "Submitted adjustment requests are immutable; reject and create a replacement request.",
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.state != self.STATE_DRAFT:
            raise ValidationError("Submitted adjustment requests cannot be deleted.")
        return super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.request_id} ({self.state})"


@receiver(m2m_changed, sender=FinancialAdjustmentRequest.evidence_documents.through)
def protect_submitted_adjustment_evidence(sender, instance, action, **kwargs):
    if action in {"pre_remove", "pre_clear"} and instance.state != FinancialAdjustmentRequest.STATE_DRAFT:
        raise ValidationError("Evidence on a submitted adjustment cannot be removed; reject and replace the request.")
