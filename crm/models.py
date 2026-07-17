import uuid

from django.db import IntegrityError, models, transaction
from django.db.utils import OperationalError, ProgrammingError
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from decimal import Decimal, ROUND_HALF_UP
from .models_access import UserAccess
from .models_platform import (
    CRMSetting,
    Department,
    FavoriteRecord,
    Position,
    RecentSearch,
    RecentlyViewedRecord,
    SavedFilter,
    UserDashboardPreference,
)
from .models_employee import EmployeeIdSequence, EmployeeProfile
from .services.costing_currency import CurrencyConversionError, convert_currency

class BDMonthlyTarget(models.Model):
    year = models.PositiveIntegerField()
    month = models.PositiveIntegerField()
    target_bdt = models.DecimalField(max_digits=14, decimal_places=0, default=Decimal("0"))
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="bd_monthly_targets_updated",
    )

    class Meta:
        unique_together = ("year", "month")
        ordering = ("-year", "-month")

    def __str__(self):
        return f"BD {self.year}-{self.month} target {self.target_bdt} BDT"


class AIHealthIssue(models.Model):
    SEVERITY_CHOICES = [
        ("info", "Info"),
        ("warning", "Warning"),
        ("critical", "Critical"),
    ]

    title = models.CharField(max_length=200)
    details = models.TextField(blank=True, default="")
    severity = models.CharField(max_length=10, choices=SEVERITY_CHOICES, default="info")
    source = models.CharField(max_length=80, blank=True, default="")
    is_resolved = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ai_health_issues",
    )

    class Meta:
        db_table = "crm_aihealthissue"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.severity.upper()} - {self.title}"


class AIHealthRun(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ai_health_runs",
    )

    score = models.IntegerField(default=100)
    ok_count = models.IntegerField(default=0)
    warn_count = models.IntegerField(default=0)
    bad_count = models.IntegerField(default=0)
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"HealthRun {self.id} score={self.score}"


class AIHealthRunCheck(models.Model):
    run = models.ForeignKey(AIHealthRun, on_delete=models.CASCADE, related_name="checks")

    name = models.CharField(max_length=120)
    status = models.CharField(max_length=20, default="ok")
    detail = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "crm_aihealthruncheck"
        ordering = ("id",)

    def __str__(self):
        return f"{self.name} {self.status}"


class AISystemLog(models.Model):
    LEVEL_CHOICES = (
        ("info", "Info"),
        ("warn", "Warning"),
        ("error", "Error"),
    )

    PROVIDER_CHOICES = (
        ("openai", "OpenAI"),
        ("anthropic", "Anthropic"),
        ("local", "Local"),
    )

    created_at = models.DateTimeField(auto_now_add=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ai_system_logs",
    )

    feature = models.CharField(max_length=80, default="unknown", db_index=True)

    provider = models.CharField(
        max_length=40,
        choices=PROVIDER_CHOICES,
        default="openai",
        db_index=True,
    )

    model_name = models.CharField(max_length=80, blank=True, default="")

    level = models.CharField(
        max_length=10,
        choices=LEVEL_CHOICES,
        default="info",
        db_index=True,
    )

    message = models.CharField(max_length=255, blank=True, default="")
    error_type = models.CharField(max_length=120, blank=True, default="")
    error_detail = models.TextField(blank=True, default="")

    latency_ms = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["provider", "level"]),
            models.Index(fields=["created_at"]),
        ]
        managed = False
        db_table = "crm_aisystemlog"

    def __str__(self):
        return f"{self.created_at} {self.provider} {self.level} {self.feature}"


class SystemActivityLog(models.Model):
    LEVEL_CHOICES = (
        ("info", "Info"),
        ("warn", "Warn"),
        ("error", "Error"),
    )

    created_at = models.DateTimeField(auto_now_add=True)

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="system_activity_logs",
    )

    area = models.CharField(max_length=50, default="crm")
    action = models.CharField(max_length=50, default="view")
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES, default="info")

    path = models.CharField(max_length=255, blank=True, default="")
    method = models.CharField(max_length=10, blank=True, default="")

    model_label = models.CharField(max_length=80, blank=True, default="")
    object_id = models.CharField(max_length=64, blank=True, default="")

    message = models.CharField(max_length=255, blank=True, default="")
    meta_json = models.TextField(blank=True, default="")

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.created_at} {self.level} {self.area} {self.action} {self.message}"


class CRMAuditLog(models.Model):
    ACTION_CREATED = "created"
    ACTION_UPDATED = "updated"
    ACTION_DELETED = "deleted"
    ACTION_APPROVED = "approved"
    ACTION_REJECTED = "rejected"
    ACTION_CONVERTED = "converted"
    ACTION_PAYMENT_RECORDED = "payment_recorded"
    ACTION_STATUS_CHANGED = "status_changed"
    ACTION_CHOICES = [
        (ACTION_CREATED, "Created"),
        (ACTION_UPDATED, "Updated"),
        (ACTION_DELETED, "Deleted"),
        (ACTION_APPROVED, "Approved"),
        (ACTION_REJECTED, "Rejected"),
        (ACTION_CONVERTED, "Converted"),
        (ACTION_PAYMENT_RECORDED, "Payment Recorded"),
        (ACTION_STATUS_CHANGED, "Status Changed"),
    ]

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="crm_audit_logs",
    )
    module = models.CharField(max_length=40, db_index=True)
    record_id = models.CharField(max_length=64, db_index=True)
    record_label = models.CharField(max_length=220, blank=True, default="")
    action_type = models.CharField(max_length=30, choices=ACTION_CHOICES, db_index=True)
    field_name = models.CharField(max_length=100, blank=True, default="")
    previous_value = models.TextField(blank=True, default="")
    new_value = models.TextField(blank=True, default="")
    target_url = models.CharField(max_length=300, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=["module", "record_id", "-created_at"]),
            models.Index(fields=["actor", "-created_at"]),
            models.Index(fields=["action_type", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.get_action_type_display()} {self.module} {self.record_id}"
# crm/models.py
import os
import string
import secrets
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


# ----------------------------
# Helpers
# ----------------------------

def _lead_code_prefix(source="", lead_type=""):
    source_text = (source or "").strip().lower()
    lead_type_text = (lead_type or "").strip().lower()

    if lead_type_text == "inbound":
        return "IN"
    if lead_type_text == "outbound":
        return "OUT"

    inbound_markers = ("website", "inbound", "inquiry", "form", "contact")
    outbound_markers = ("outbound", "campaign", "manual", "cold", "research")
    if any(marker in source_text for marker in inbound_markers):
        return "IN"
    if any(marker in source_text for marker in outbound_markers):
        return "OUT"
    return "LEAD"


def generate_lead_id(source="", lead_type=""):
    prefix = _lead_code_prefix(source=source, lead_type=lead_type)
    existing_ids = Lead.objects.filter(lead_id__startswith=f"{prefix}-").values_list("lead_id", flat=True)
    highest_number = 1000
    for lead_id in existing_ids:
        try:
            number = int(str(lead_id).split("-", 1)[1])
        except (IndexError, TypeError, ValueError):
            continue
        highest_number = max(highest_number, number)

    for offset in range(1, 1000):
        candidate = f"{prefix}-{highest_number + offset}"
        if not Lead.objects.filter(lead_id=candidate).exists():
            return candidate

    return f"{prefix}-{timezone.now().strftime('%y%m%d%H%M%S')}"


def generate_customer_code():
    chars = string.ascii_uppercase + string.digits
    while True:
        code = "C" + "".join(secrets.choice(chars) for _ in range(9))
        if not Customer.objects.filter(customer_code=code).exists():
            return code


# ----------------------------
# Dropdown choices
# ----------------------------

SOURCE_CHOICES = [
    ("Website Inquiry", "Website Inquiry"),
    ("Instagram", "Instagram"),
    ("LinkedIn", "LinkedIn"),
    ("Email Campaign", "Email Campaign"),
    ("Referral", "Referral"),
    ("Google Search / SEO", "Google Search / SEO"),
    ("Trade Show / Event", "Trade Show / Event"),
    ("WhatsApp", "WhatsApp"),
    ("Returning Client", "Returning Client"),
    ("Other", "Other"),
]

LEAD_DIRECTION_CHOICES = [
    ("inbound", "Inbound"),
    ("outbound", "Outbound"),
]

BRAND_STAGE_CHOICES = [
    ("Startup / New Brand", "Startup / New Brand"),
    ("Brand Owner / Designer", "Brand Owner / Designer"),
    ("Retail Store / Boutique", "Retail Store / Boutique"),
    ("Corporate Client", "Corporate Client"),
    ("Distributor / Wholesaler", "Distributor / Wholesaler"),
    ("Private Label Client", "Private Label Client"),
    ("Manufacturer Collaboration", "Manufacturer Collaboration"),
    ("Influencer / Content Creator", "Influencer / Content Creator"),
    ("Returning Customer", "Returning Customer"),
    ("Other", "Other"),
]

OUTBOUND_STATUS_CHOICES = [
    ("Not Contacted", "Not Contacted"),
    ("First Contact Sent", "First Contact Sent"),
    ("Follow Up 1 Sent", "Follow Up 1 Sent"),
    ("Follow Up 2 Sent", "Follow Up 2 Sent"),
    ("Follow Up 3 Sent", "Follow Up 3 Sent"),
    ("Replied", "Replied"),
    ("Interested", "Interested"),
    ("Meeting Booked", "Meeting Booked"),
    ("Quote Requested", "Quote Requested"),
    ("Sample Discussion", "Sample Discussion"),
    ("Converted to Opportunity", "Converted to Opportunity"),
    ("No Response", "No Response"),
    ("Bad Fit", "Bad Fit"),
    ("Archived", "Archived"),
]

LEAD_QUAL_STATUS_CHOICES = [
    ("Raw Imported", "Raw Imported"),
    ("Researching", "Researching"),
    ("Enriched", "Enriched"),
    ("Qualified", "Qualified"),
    ("Strong Fit", "Strong Fit"),
    ("Needs Review", "Needs Review"),
    ("Outreach Ready", "Outreach Ready"),
    ("Contact Missing", "Contact Missing"),
    ("Duplicate", "Duplicate"),
    ("Bad Fit", "Bad Fit"),
    ("Archived", "Archived"),
]

LEAD_STATUS_CHOICES = [
    ("New", "New"),
    ("Working", "Working"),
    ("Nurturing", "Nurturing"),
    ("Qualified", "Qualified"),
    ("Unqualified", "Unqualified"),
    ("Converted", "Converted"),
    ("On Hold", "On Hold"),
    ("Lost", "Lost"),
]

PRIORITY_CHOICES = [
    ("Low", "Low"),
    ("Medium", "Medium"),
    ("High", "High"),
    ("Hot", "Hot"),
]


SPORTS_PRODUCT_CATEGORY_CHOICES = [
    ("Basketball Jersey", "Basketball Jersey"),
    ("Soccer Jersey", "Soccer Jersey"),
    ("Football Jersey", "Football Jersey"),
    ("Baseball Jersey", "Baseball Jersey"),
    ("Hockey Jersey", "Hockey Jersey"),
    ("Volleyball Jersey", "Volleyball Jersey"),
    ("Rugby Jersey", "Rugby Jersey"),
    ("Sports Tracksuit", "Sports Tracksuit"),
    ("Team Uniform", "Team Uniform"),
    ("Training Jersey", "Training Jersey"),
    ("Warm Up Jacket", "Warm Up Jacket"),
    ("Warm Up Pants", "Warm Up Pants"),
    ("Athletic Shorts", "Athletic Shorts"),
    ("Compression Shirt", "Compression Shirt"),
    ("Compression Shorts", "Compression Shorts"),
    ("Sports Polo", "Sports Polo"),
    ("Coach Jacket", "Coach Jacket"),
    ("Sideline Jacket", "Sideline Jacket"),
    ("Tracksuit Set", "Tracksuit Set"),
    ("Practice Jersey", "Practice Jersey"),
]


def _extend_choices(base_choices, extra_choices):
    existing = {value for value, _label in base_choices}
    return list(base_choices) + [choice for choice in extra_choices if choice[0] not in existing]


def lead_product_category_choices():
    return Opportunity.PRODUCT_CATEGORY_CHOICES


def lead_product_type_choices():
    return Opportunity.PRODUCT_TYPE_CHOICES


def lead_product_interest_choices():
    return _extend_choices(Opportunity.PRODUCT_TYPE_CHOICES, SPORTS_PRODUCT_CATEGORY_CHOICES)


# ----------------------------
# Lead model
# ----------------------------

class Lead(models.Model):
    MARKET_CHOICES = [
        ("BD", "Bangladesh"),
        ("CA", "Canada"),
        ("USA", "USA"),
        ("OTHER", "Other"),
    ]

    market = models.CharField(max_length=10, choices=MARKET_CHOICES, default="CA")

    customer = models.ForeignKey(
        "Customer",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="leads",
    )
    import_job = models.ForeignKey(
        "LeadImportJob",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="leads",
    )

    lead_id = models.CharField(
        max_length=20,
        unique=True,
        blank=True,
        db_index=True,
    )

    account_brand = models.CharField(max_length=200, blank=True)
    contact_name = models.CharField(max_length=200, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)

    company_website = models.CharField(max_length=255, blank=True)
    country = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=100, blank=True)
    product_category = models.CharField(
        max_length=100,
        choices=lead_product_category_choices,
        blank=True,
    )
    primary_product_type = models.CharField(
        max_length=100,
        choices=lead_product_type_choices,
        blank=True,
        default="",
        db_index=True,
    )
    product_interest = models.CharField(
        max_length=200,
        choices=lead_product_interest_choices,
        blank=True,
    )
    order_quantity = models.CharField(max_length=100, blank=True)
    budget = models.CharField(max_length=100, blank=True)
    preferred_contact_time = models.CharField(max_length=100, blank=True)

    attachment = models.FileField(upload_to="lead_files/", null=True, blank=True)

    source = models.CharField(
        max_length=50,
        choices=SOURCE_CHOICES,
        default="Website Inquiry",
        db_index=True,
    )
    lead_type = models.CharField(
        max_length=20,
        choices=LEAD_DIRECTION_CHOICES,
        default="inbound",
        db_index=True,
    )
    source_channel = models.CharField(max_length=100, blank=True, default="")
    outbound_method = models.CharField(max_length=100, blank=True, default="")
    outbound_status = models.CharField(
        max_length=60,
        choices=OUTBOUND_STATUS_CHOICES,
        blank=True,
        default="",
        db_index=True,
    )
    lead_status = models.CharField(
        max_length=50,
        choices=LEAD_STATUS_CHOICES,
        default="New",
        db_index=True,
    )
    priority = models.CharField(
        max_length=20,
        choices=PRIORITY_CHOICES,
        default="Medium",
        db_index=True,
    )
    priority_level = models.CharField(
        max_length=20,
        choices=PRIORITY_CHOICES,
        default="Medium",
        db_index=True,
    )
    brand_stage = models.CharField(
        max_length=50,
        choices=BRAND_STAGE_CHOICES,
        blank=True,
        default="",
    )
    target_order_volume_min = models.PositiveIntegerField(null=True, blank=True)
    target_order_volume_max = models.PositiveIntegerField(null=True, blank=True)
    brand_fit_score = models.PositiveIntegerField(default=0)
    fit_score_locked = models.BooleanField(default=False)
    region = models.CharField(max_length=100, blank=True, default="")
    website = models.CharField(max_length=255, blank=True, default="")
    instagram_handle = models.CharField(max_length=200, blank=True, default="")
    linkedin_url = models.CharField(max_length=255, blank=True, default="")
    last_outreach_date = models.DateField(null=True, blank=True)
    next_follow_up_date = models.DateField(null=True, blank=True)
    last_reply_date = models.DateField(null=True, blank=True)
    ideal_customer_profile_match = models.BooleanField(default=False)
    disqualification_reason = models.TextField(blank=True, default="")
    qualification_status = models.CharField(
        max_length=40,
        choices=LEAD_QUAL_STATUS_CHOICES,
        default="Raw Imported",
        db_index=True,
    )
    qualification_reason = models.TextField(blank=True, default="")
    confidence_level = models.PositiveIntegerField(default=0)
    target_order_range_estimate = models.CharField(max_length=120, blank=True, default="")
    product_category_guess = models.CharField(max_length=120, blank=True, default="")
    recommended_channel = models.CharField(max_length=120, blank=True, default="")
    recommended_next_action = models.CharField(max_length=200, blank=True, default="")
    last_enriched_at = models.DateTimeField(null=True, blank=True)

    owner = models.CharField(max_length=100, blank=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_leads",
    )
    is_archived = models.BooleanField(default=False, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="archived_leads",
    )
    created_date = models.DateField(default=timezone.localdate)
    next_followup = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    utm_source = models.CharField(max_length=120, blank=True, default="")
    utm_medium = models.CharField(max_length=120, blank=True, default="")
    utm_campaign = models.CharField(max_length=120, blank=True, default="")
    utm_content = models.CharField(max_length=120, blank=True, default="")
    utm_term = models.CharField(max_length=120, blank=True, default="")
    first_touch_channel = models.CharField(max_length=120, blank=True, default="")
    last_touch_channel = models.CharField(max_length=120, blank=True, default="")

    def save(self, *args, **kwargs):
        if not self.lead_id:
            self.lead_id = generate_lead_id(source=self.source, lead_type=self.lead_type)

        if not self.created_date:
            self.created_date = timezone.localdate()

        if self.lead_type == "outbound" and not self.fit_score_locked:
            score, _signals = self.compute_fit_score()
            self.brand_fit_score = score
            self.ideal_customer_profile_match = score >= 70

        if self.website and not self.company_website:
            self.company_website = self.website
        if self.company_website and not self.website:
            self.website = self.company_website

        if self.next_follow_up_date and not self.next_followup:
            self.next_followup = self.next_follow_up_date
        if self.next_followup and not self.next_follow_up_date:
            self.next_follow_up_date = self.next_followup

        super().save(*args, **kwargs)

    def compute_fit_score(self):
        strengths = []
        score = 0

        if self.website or self.company_website:
            score += 10
            strengths.append("Website")
        if self.instagram_handle:
            score += 8
            strengths.append("Instagram")
        if self.linkedin_url:
            score += 6
            strengths.append("LinkedIn")

        min_vol = self.target_order_volume_min or 0
        max_vol = self.target_order_volume_max or 0
        volume_signal = max(min_vol, max_vol)

        if volume_signal >= 500:
            score += 15
            strengths.append("500+ pcs")
        if volume_signal >= 1000:
            score += 25
            strengths.append("1000+ pcs")
        if volume_signal >= 2000:
            score += 10
            strengths.append("2000+ pcs")
        if volume_signal >= 5000:
            score += 10
            strengths.append("5000+ pcs")

        strong_products = {
            "Hoodie", "T Shirt", "Activewear", "Athleticwear", "Swimwear",
            "Undergarments", "Kidswear", "Outerwear", "Denim Jacket", "Joggers"
        }
        if self.product_interest in strong_products:
            score += 12
            strengths.append("Product fit")

        if 0 < volume_signal < 100:
            score -= 20
        if not (self.website or self.company_website) and not self.instagram_handle:
            score -= 10

        score = max(0, min(score, 100))
        return score, strengths

    def __str__(self):
        return f"{self.account_brand} ({self.lead_id})"


class ProductReferenceImage(models.Model):
    ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

    lead = models.ForeignKey(
        Lead,
        related_name="product_reference_images",
        on_delete=models.CASCADE,
    )
    opportunity = models.ForeignKey(
        "Opportunity",
        related_name="product_reference_images",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    production_order = models.ForeignKey(
        "ProductionOrder",
        related_name="product_reference_images",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    image = models.ImageField(upload_to="product_reference_images/%Y/%m/")
    caption = models.CharField(max_length=160, blank=True, default="")
    slot = models.PositiveSmallIntegerField(default=1)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="product_reference_images",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["slot", "uploaded_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["lead", "slot"], name="uniq_product_reference_image_lead_slot"),
        ]
        indexes = [
            models.Index(fields=["lead", "slot"]),
            models.Index(fields=["opportunity"]),
            models.Index(fields=["production_order"]),
        ]

    def clean(self):
        super().clean()
        if self.slot not in (1, 2, 3):
            raise ValidationError({"slot": "Only three product reference images are allowed."})

        if self.image:
            extension = os.path.splitext(self.image.name or "")[1].lower()
            if extension not in self.ALLOWED_EXTENSIONS:
                raise ValidationError({"image": "Upload a JPG, PNG, or WEBP image."})

        if self.opportunity_id and self.lead_id and self.opportunity.lead_id != self.lead_id:
            raise ValidationError({"opportunity": "Reference image opportunity must belong to the same lead."})

        if self.production_order_id and self.lead_id:
            order_lead_id = getattr(self.production_order, "lead_id", None)
            if order_lead_id and order_lead_id != self.lead_id:
                raise ValidationError({"production_order": "Reference image production order must belong to the same lead."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        label = self.caption or f"Reference image {self.slot}"
        return f"{label} for {self.lead}"


# ----------------------------
# Customer model
# ----------------------------

class Customer(models.Model):
    customer_code = models.CharField(max_length=50, unique=True, blank=True)

    account_brand = models.CharField(max_length=200, blank=True, default="")
    contact_name = models.CharField(max_length=200, blank=True, default="")
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    website = models.CharField(max_length=255, blank=True)
    industry = models.CharField(max_length=120, blank=True)
    market = models.CharField(max_length=10, blank=True)

    address_line1 = models.CharField(max_length=255, blank=True)
    address_line2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    province = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=100, blank=True)

    shipping_name = models.CharField(max_length=200, blank=True)
    shipping_address1 = models.CharField(max_length=255, blank=True)
    shipping_address2 = models.CharField(max_length=255, blank=True)
    shipping_city = models.CharField(max_length=100, blank=True)
    shipping_state = models.CharField(max_length=100, blank=True)
    shipping_postcode = models.CharField(max_length=20, blank=True)
    shipping_country = models.CharField(max_length=100, blank=True)

    is_active = models.BooleanField(default=True)
    is_archived = models.BooleanField(default=False, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="archived_customers",
    )
    created_date = models.DateField(default=timezone.localdate)
    updated_at = models.DateTimeField(auto_now=True)
    notes = models.TextField(blank=True)

    def save(self, *args, **kwargs):
        if not self.customer_code:
            self.customer_code = generate_customer_code()

        if not self.created_date:
            self.created_date = timezone.localdate()

        super().save(*args, **kwargs)

    def __str__(self):
        display = self.account_brand or self.contact_name or "Customer"
        return f"{display} [{self.customer_code}]"


class CustomerNote(models.Model):
    customer = models.ForeignKey(
        "Customer",
        related_name="notes_list",
        on_delete=models.CASCADE,
    )
    author = models.CharField(max_length=100, blank=True, default="")
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.customer_id} note {self.created_at:%Y-%m-%d}"


class CustomerEvent(models.Model):
    EVENT_TYPE_CHOICES = [
        ("lead_created", "Lead created"),
        ("opportunity_created", "Opportunity created"),
        ("moved_to_production", "Moved to production"),
        ("production_status", "Production status changed"),
        ("production_completed", "Production completed"),
        ("production_closed_won", "Production closed won"),
        ("production_closed_lost", "Production closed lost"),
    ]

    customer = models.ForeignKey(
        "Customer",
        related_name="customer_events",
        on_delete=models.CASCADE,
    )
    event_type = models.CharField(
        max_length=40,
        choices=EVENT_TYPE_CHOICES,
    )
    title = models.CharField(max_length=200)
    details = models.TextField(blank=True, default="")
    opportunity = models.ForeignKey(
        "Opportunity",
        related_name="customer_events",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    production = models.ForeignKey(
        "ProductionOrder",
        related_name="customer_events",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.customer_id} {self.event_type} {self.created_at:%Y-%m-%d}"

class LeadComment(models.Model):
    lead = models.ForeignKey(
        Lead,
        related_name="comments",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    opportunity = models.ForeignKey(
        "Opportunity",
        related_name="comments",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    production = models.ForeignKey(
        "ProductionOrder",
        related_name="comments",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    author = models.CharField(max_length=100, blank=True, default="")
    author_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="chatter_comments",
    )
    content = models.TextField()
    attachment = models.FileField(
        upload_to="chatter_attachments/",
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    pinned = models.BooleanField(default=False)
    is_ai = models.BooleanField(default=False)

    class Meta:
        ordering = ["-pinned", "-created_at"]

    def __str__(self):
        return f"{self.author}: {self.content[:40]}"

    @property
    def was_edited(self):
        if not self.created_at or not self.updated_at:
            return False
        return (self.updated_at - self.created_at).total_seconds() > 1


class LeadTask(models.Model):
    STATUS_CHOICES = [
        ("Open", "Open"),
        ("In Progress", "In Progress"),
        ("Done", "Done"),
    ]

    lead = models.ForeignKey(Lead, related_name="tasks", on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="Open")
    priority = models.CharField(
        max_length=20, choices=PRIORITY_CHOICES, default="Medium"
    )
    assigned_to = models.CharField(max_length=100, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["status", "due_date", "-created_at"]

    def __str__(self):
        return f"Task {self.title} for {self.lead.lead_id}"


class LeadActivity(models.Model):
    ACTIVITY_TYPE_CHOICES = [
        ("lead_created", "Lead created"),
        ("converted", "Converted to opportunity"),
        ("ai_summary", "AI summary created"),
        ("file_uploaded", "File uploaded"),
        ("file_deleted", "File deleted"),
        ("note_added", "Note added"),
        ("task_created", "Task created"),
        ("task_completed", "Task completed"),
        ("shipping_updated", "Shipping updated"),
        ("stage_updated", "Stage updated"),
        ("cold_email_sent", "Cold email sent"),
        ("linkedin_message_sent", "LinkedIn message sent"),
        ("instagram_dm_sent", "Instagram DM sent"),
        ("call_made", "Call made"),
        ("follow_up_sent", "Follow up sent"),
        ("reply_received", "Reply received"),
        ("meeting_booked", "Meeting booked"),
        ("quote_shared", "Quote shared"),
        ("sample_discussion", "Sample discussion"),
    ]

    lead = models.ForeignKey(Lead, related_name="activities", on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    activity_type = models.CharField(max_length=40, choices=ACTIVITY_TYPE_CHOICES)
    description = models.TextField(blank=True, default="")
    channel = models.CharField(max_length=50, blank=True, default="")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="lead_activities",
    )
    note = models.TextField(blank=True, default="")
    message_copy = models.TextField(blank=True, default="")
    outcome = models.CharField(max_length=120, blank=True, default="")
    follow_up_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_activity_type_display()} for {self.lead.lead_id}"


class LeadContactPoint(models.Model):
    CONTACT_TYPE_CHOICES = [
        ("email", "Email"),
        ("phone", "Phone"),
        ("contact_form", "Contact form"),
        ("instagram", "Instagram"),
        ("linkedin", "LinkedIn"),
        ("website", "Website"),
        ("other", "Other"),
    ]

    lead = models.ForeignKey(
        Lead,
        related_name="contact_points",
        on_delete=models.CASCADE,
    )
    contact_type = models.CharField(max_length=30, choices=CONTACT_TYPE_CHOICES)
    value = models.CharField(max_length=255)
    label = models.CharField(max_length=120, blank=True, default="")
    source_url = models.URLField(blank=True, default="")
    confidence = models.PositiveIntegerField(default=0)
    is_primary = models.BooleanField(default=False)
    last_verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_primary", "-confidence", "contact_type"]

    def __str__(self):
        return f"{self.lead.lead_id} {self.contact_type}: {self.value}"


class LeadAIInsight(models.Model):
    INSIGHT_SOURCE_CHOICES = [
        ("auto", "Auto"),
        ("manual", "Manual"),
    ]

    lead = models.ForeignKey(
        Lead,
        related_name="ai_insights",
        on_delete=models.CASCADE,
    )
    source = models.CharField(max_length=20, choices=INSIGHT_SOURCE_CHOICES, default="auto")
    summary_text = models.TextField(blank=True, default="")
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.lead.lead_id} insight {self.created_at:%Y-%m-%d}"


class LeadImportJob(models.Model):
    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("processing", "Processing"),
        ("done", "Done"),
        ("failed", "Failed"),
    ]

    file = models.FileField(upload_to="lead_imports/")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="lead_import_jobs",
    )
    total_rows = models.PositiveIntegerField(default=0)
    created_count = models.PositiveIntegerField(default=0)
    updated_count = models.PositiveIntegerField(default=0)
    duplicate_count = models.PositiveIntegerField(default=0)
    strong_fit_count = models.PositiveIntegerField(default=0)
    moderate_fit_count = models.PositiveIntegerField(default=0)
    weak_fit_count = models.PositiveIntegerField(default=0)
    bad_fit_count = models.PositiveIntegerField(default=0)
    missing_contact_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    stats = models.JSONField(default=dict, blank=True)
    error_log = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Lead import {self.pk} ({self.status})"


class LeadResearchJob(models.Model):
    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("processing", "Processing"),
        ("done", "Done"),
        ("failed", "Failed"),
    ]

    lead = models.ForeignKey(
        Lead,
        related_name="research_jobs",
        on_delete=models.CASCADE,
    )
    website = models.URLField(blank=True, default="")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="lead_research_jobs",
    )
    data = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Lead research {self.pk} ({self.status})"




# -----------------------------------
# Opportunity and related models
# -----------------------------------

class Opportunity(models.Model):
    ORDER_CURRENCY_CHOICES = [
        ("CAD", "CAD"),
        ("USD", "USD"),
        ("BDT", "BDT"),
    ]

    STAGE_CHOICES = [
        ("Prospecting", "Prospecting"),
        ("Qualification", "Qualification"),
        ("Needs Analysis", "Needs Analysis"),
        ("Proposal", "Proposal or Quote"),
        ("Negotiation", "Negotiation"),
        ("Awaiting Payment", "Awaiting Payment"),
        ("Sampling", "Sampling"),
        ("Production", "Production"),
        ("Shipment Complete", "Shipment Complete"),
        ("Closed Won", "Closed Won"),
        ("Closed Lost", "Closed Lost"),
    ]

    PRODUCT_TYPE_CHOICES = [
        ("Activewear", "Activewear"),
        ("Athleticwear", "Athleticwear"),
        ("Streetwear", "Streetwear"),
        ("Swimwear", "Swimwear"),
        ("Undergarments", "Undergarments"),
        ("Outerwear", "Outerwear"),
        ("Casualwear", "Casualwear"),
        ("Kidswear", "Kidswear"),
        ("Corporate / Uniforms", "Corporate / Uniforms"),
        ("Sustainable Collection", "Sustainable Collection"),
        ("Accessories", "Accessories"),
        ("Other", "Other"),
    ]

    PRODUCT_CATEGORY_BASE_CHOICES = [
        ("Leggings", "Leggings"),
        ("Sports Bra", "Sports Bra"),
        ("Crop Top", "Crop Top"),
        ("Yoga Set", "Yoga Set"),
        ("Tank Top", "Tank Top"),
        ("Joggers", "Joggers"),
        ("Track Pants", "Track Pants"),
        ("Tracksuit Set", "Tracksuit Set"),
        ("Training Jacket", "Training Jacket"),
        ("Windbreaker", "Windbreaker"),
        ("Hoodie", "Hoodie"),
        ("Sweatshirt", "Sweatshirt"),
        ("Cargo Pants", "Cargo Pants"),
        ("Oversized Tee", "Oversized Tee"),
        ("Denim Jacket", "Denim Jacket"),
        ("Bikini Set", "Bikini Set"),
        ("One Piece", "One Piece"),
        ("Mens Trunk", "Mens Trunk"),
        ("Rash Guard", "Rash Guard"),
        ("Mens Boxer", "Mens Boxer"),
        ("Brief", "Brief"),
        ("Womens Panty", "Womens Panty"),
        ("Bra", "Bra"),
        ("Camisole", "Camisole"),
        ("Padded Jacket", "Padded Jacket"),
        ("Light Jacket", "Light Jacket"),
        ("Pullover", "Pullover"),
        ("Zip Up Hoodie", "Zip Up Hoodie"),
        ("T Shirt", "T Shirt"),
        ("Polo Shirt", "Polo Shirt"),
        ("Lounge Set", "Lounge Set"),
        ("Shorts", "Shorts"),
        ("Onesie", "Onesie"),
        ("Romper", "Romper"),
        ("Hoodie Set", "Hoodie Set"),
        ("Jogger Set", "Jogger Set"),
        ("Baby Sleeper", "Baby Sleeper"),
        ("Office Shirt", "Office Shirt"),
        ("Polo Uniform", "Polo Uniform"),
        ("Work Jacket", "Work Jacket"),
        ("Promotional Tee", "Promotional Tee"),
        ("Organic Cotton Wear", "Organic Cotton Wear"),
        ("Recycled Polyester", "Recycled Polyester"),
        ("Eco Fabric Product", "Eco Fabric Product"),
        ("Cap", "Cap"),
        ("Tote Bag", "Tote Bag"),
        ("Socks", "Socks"),
        ("Headband", "Headband"),
        ("Other", "Other"),
    ]
    PRODUCT_CATEGORY_CHOICES = _extend_choices(PRODUCT_CATEGORY_BASE_CHOICES, SPORTS_PRODUCT_CATEGORY_CHOICES)

    lead = models.ForeignKey(
        "Lead",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="opportunities",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_opportunities",
    )
    converted_from_lead_type = models.CharField(max_length=20, blank=True, default="")
    converted_from_source_channel = models.CharField(max_length=100, blank=True, default="")
    converted_from_outbound_status = models.CharField(max_length=60, blank=True, default="")

    customer = models.ForeignKey(
        "Customer",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="opportunities",
    )

    opportunity_id = models.CharField(
        max_length=50,
        unique=True,
        blank=True,
    )
    opportunity_date = models.DateField(null=True, blank=True, db_index=True)

    stage = models.CharField(
        max_length=50,
        choices=STAGE_CHOICES,
        default="Prospecting",
    )

    product_category = models.CharField(
        max_length=50,
        choices=PRODUCT_CATEGORY_CHOICES,
        default="Other",
    )

    product_type = models.CharField(
        max_length=50,
        choices=PRODUCT_TYPE_CHOICES,
        default="Other",
    )

    moq_units = models.IntegerField(null=True, blank=True)

    order_currency = models.CharField(
        max_length=3,
        choices=ORDER_CURRENCY_CHOICES,
        default="CAD",
    )

    order_value = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    order_value_usd = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    fx_rate_bdt_per_usd = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
    )
    costing_total_cost_per_piece = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
    )
    costing_fob_per_piece = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
    )
    costing_margin_percent = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
    )
    costing_status = models.CharField(
        max_length=20,
        blank=True,
        default="",
    )

    created_date = models.DateField(auto_now_add=True)
    closed_won_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    next_followup = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    is_open = models.BooleanField(default=True)
    is_archived = models.BooleanField(default=False, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="archived_opportunities",
    )

    @property
    def status_key(self):
        if self.stage == "Closed Won":
            return "closed_won"
        if self.stage == "Closed Lost":
            return "closed_lost"
        if self.is_open is False:
            return "closed_lost"
        return "open"

    @property
    def status_label(self):
        mapping = {
            "open": "Open",
            "closed_won": "Closed Won",
            "closed_lost": "Closed Lost",
        }
        return mapping.get(self.status_key, "Open")

    @property
    def effective_opportunity_date(self):
        return self.opportunity_date or self.created_date

    def save(self, *args, **kwargs):
        if not self.customer and self.lead and self.lead.customer_id:
            self.customer = self.lead.customer

        if not self.opportunity_id and self.lead and self.lead.lead_id:
            count_for_lead = Opportunity.objects.filter(lead=self.lead).count() + 1
            self.opportunity_id = f"OPP-{self.lead.lead_id}-{count_for_lead:03}"
        elif not self.opportunity_id and self.customer_id:
            customer_label = self.customer.customer_code or f"CUST-{self.customer_id}"
            count_for_customer = Opportunity.objects.filter(customer=self.customer, lead__isnull=True).count() + 1
            self.opportunity_id = f"OPP-{customer_label}-{count_for_customer:03}"
        elif not self.opportunity_id:
            self.opportunity_id = f"OPP-{timezone.now():%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"

        if self.stage == "Closed Won" and self.closed_won_at is None:
            self.closed_won_at = timezone.now()
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                kwargs["update_fields"] = set(update_fields) | {"closed_won_at"}

        super().save(*args, **kwargs)

    def __str__(self):
        account = (
            getattr(self.lead, "account_brand", "")
            or getattr(self.customer, "account_brand", "")
            or getattr(self.customer, "contact_name", "")
            or "Unlinked account"
        )
        return f"{self.opportunity_id} for {account}"


class OpportunityTask(models.Model):
    STATUS_CHOICES = [
        ("Open", "Open"),
        ("In Progress", "In Progress"),
        ("Done", "Done"),
    ]

    opportunity = models.ForeignKey(
        Opportunity,
        related_name="tasks",
        on_delete=models.CASCADE,
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="Open",
    )
    priority = models.CharField(
        max_length=20,
        choices=PRIORITY_CHOICES,
        default="Medium",
    )
    assigned_to = models.CharField(max_length=100, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["status", "due_date", "-created_at"]

    def __str__(self):
        return f"Task {self.title} for {self.opportunity.opportunity_id}"


class OpportunityFile(models.Model):
    opportunity = models.ForeignKey(
        Opportunity,
        related_name="files",
        on_delete=models.CASCADE,
    )
    file = models.FileField(upload_to="opportunity_files/")
    original_name = models.CharField(max_length=255, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def save(self, *args, **kwargs):
        if self.file and not self.original_name:
            self.original_name = self.file.name
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.original_name} ({self.opportunity.opportunity_id})"

# -----------------------------------
# Costing
# -----------------------------------

COST_SECTION_CHOICES = [
    ("fabric", "Fabric"),
    ("trims", "Trims"),
    ("labor", "Labor"),
    ("overhead", "Overhead"),
    ("process", "Process"),
    ("packaging", "Packaging"),
    ("freight", "Freight"),
    ("testing", "Testing"),
    ("other", "Other"),
]

OVERHEAD_METHOD_CHOICES = [
    ("per_minute", "Per minute"),
    ("percent_of_labor", "Percent of labor"),
    ("per_piece", "Per piece"),
]

COST_SHEET_STATUS_CHOICES = [
    ("draft", "Draft"),
    ("approved", "Approved"),
    ("locked", "Locked"),
]

COSTING_CURRENCY_CHOICES = [
    ("USD", "USD"),
    ("CAD", "CAD"),
    ("BDT", "BDT"),
]

COSTING_SIMPLE_CURRENCY_CHOICES = [
    ("BDT", "BDT"),
]

FACTORY_LOCATION_CHOICES = [
    ("bd", "Bangladesh"),
    ("ca", "Canada"),
    ("other", "Other"),
]

COST_SHEET_SIMPLE_STATUS_CHOICES = [
    ("draft", "Draft"),
    ("approved", "Approved"),
]

NEW_COSTING_STATUS_CHOICES = [
    ("draft", "Draft"),
    ("approved", "Approved"),
]

NEW_COSTING_CATEGORY_CHOICES = [
    ("fabric", "Fabrics"),
    ("sewing_trim", "Sewing trims"),
    ("packaging_trim", "Packaging trims"),
    ("labels_branding", "Labels and branding"),
    ("wash_process", "Washing and process"),
    ("cm_labor", "CM and labor"),
    ("logistics_compliance", "Logistics and compliance"),
    ("other", "Other costs"),
]

NEW_COSTING_UOM_CHOICES = [
    ("piece", "Per piece"),
    ("kg", "Per kg"),
    ("meter", "Per meter"),
    ("yard", "Per yard"),
    ("roll", "Per roll"),
    ("cone", "Per cone"),
    ("pack", "Per pack"),
    ("order", "Per order"),
]

NEW_COSTING_CURRENCY_CHOICES = [
    ("BDT", "BDT"),
    ("CAD", "CAD"),
    ("USD", "USD"),
]

OPPORTUNITY_DOC_TYPES = [
    ("costing_pdf", "Costing PDF"),
    ("costing_excel", "Costing Excel"),
    ("costing_other", "Costing Other"),
    ("other", "Other"),
]


class CostSheet(models.Model):
    opportunity = models.ForeignKey(
        Opportunity,
        on_delete=models.CASCADE,
        related_name="cost_sheets",
    )
    customer = models.ForeignKey(
        "Customer",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="cost_sheets",
    )
    product_type = models.CharField(
        max_length=50,
        choices=Opportunity.PRODUCT_TYPE_CHOICES,
        default="Other",
    )
    style_code = models.CharField(max_length=50, blank=True)
    style_name = models.CharField(max_length=200, blank=True)
    version_number = models.PositiveIntegerField(default=1)
    currency = models.CharField(
        max_length=10,
        choices=COSTING_CURRENCY_CHOICES,
        default="USD",
    )
    production_location = models.CharField(
        max_length=20,
        choices=[
            ("bd", "Bangladesh"),
            ("ca", "Canada"),
            ("other", "Other"),
        ],
        default="bd",
    )
    target_quantity = models.PositiveIntegerField(default=0)
    overhead_method = models.CharField(
        max_length=20,
        choices=OVERHEAD_METHOD_CHOICES,
        default="per_piece",
    )
    target_margin_percent = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("0"),
    )
    quote_price_per_piece = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=20,
        choices=COST_SHEET_STATUS_CHOICES,
        default="draft",
    )
    is_active = models.BooleanField(default=False)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_cost_sheets",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"CostSheet {self.opportunity.opportunity_id} v{self.version_number}"

    def save(self, *args, **kwargs):
        if not self.customer and self.opportunity and self.opportunity.customer_id:
            self.customer = self.opportunity.customer

        if self._state.adding:
            latest = (
                CostSheet.objects.filter(opportunity=self.opportunity)
                .order_by("-version_number")
                .first()
            )
            self.version_number = (latest.version_number if latest else 0) + 1

        super().save(*args, **kwargs)

        if self.is_active:
            CostSheet.objects.filter(opportunity=self.opportunity).exclude(id=self.id).update(
                is_active=False
            )


class CostSheetSimple(models.Model):
    opportunity = models.ForeignKey(
        Opportunity,
        on_delete=models.CASCADE,
        related_name="simple_cost_sheets",
    )
    customer = models.ForeignKey(
        "Customer",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="simple_cost_sheets",
    )
    style_name = models.CharField(max_length=200, blank=True)
    style_code = models.CharField(max_length=50, blank=True)
    product_type = models.CharField(
        max_length=50,
        choices=Opportunity.PRODUCT_TYPE_CHOICES,
        default="Other",
    )
    quantity = models.PositiveIntegerField(default=0)
    currency = models.CharField(
        max_length=10,
        choices=COSTING_SIMPLE_CURRENCY_CHOICES,
        default="BDT",
    )
    exchange_rate_bdt_per_cad = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
    )
    factory_location = models.CharField(
        max_length=20,
        choices=FACTORY_LOCATION_CHOICES,
        default="bd",
    )
    fabric_cost_per_piece = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    fabric_wastage_percent = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("0"),
    )
    rib_cost_per_piece = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    woven_fabric_cost_per_piece = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    zipper_cost_per_piece = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    zipper_puller_cost_per_piece = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    button_cost_per_piece = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    thread_cost_per_piece = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    lining_cost_per_piece = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    velcro_cost_per_piece = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    neck_tape_cost_per_piece = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    elastic_cost_per_piece = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    collar_cuff_cost_per_piece = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    ring_cost_per_piece = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    buckle_clip_cost_per_piece = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    main_label_cost_per_piece = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    care_label_cost_per_piece = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    hang_tag_cost_per_piece = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    conveyance_cost_per_piece = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    trim_cost_per_piece = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    labor_cost_per_piece = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    overhead_cost_per_piece = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    process_cost_per_piece = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    packaging_cost_per_piece = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    freight_cost_per_piece = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    testing_cost_per_piece = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    other_cost_per_piece = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    quote_price_per_piece = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    notes = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=COST_SHEET_SIMPLE_STATUS_CHOICES,
        default="draft",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]

    def __str__(self):
        label = self.opportunity.opportunity_id if self.opportunity_id else f"Costing {self.pk}"
        return f"{label} (Simple)"

    def save(self, *args, **kwargs):
        if not self.customer and self.opportunity and self.opportunity.customer_id:
            self.customer = self.opportunity.customer
        super().save(*args, **kwargs)


class CostingHeader(models.Model):
    QUOTATION_STATUS_DRAFT = "draft"
    QUOTATION_STATUS_APPROVED = "approved"
    QUOTATION_STATUS_REJECTED = "rejected"
    QUOTATION_STATUS_SENT = "sent"
    QUOTATION_STATUS_ACCEPTED = "accepted"
    QUOTATION_STATUS_DECLINED = "declined"
    QUOTATION_STATUS_CHOICES = [
        (QUOTATION_STATUS_DRAFT, "Draft"),
        (QUOTATION_STATUS_APPROVED, "Approved"),
        (QUOTATION_STATUS_REJECTED, "Rejected"),
        (QUOTATION_STATUS_SENT, "Sent"),
        (QUOTATION_STATUS_ACCEPTED, "Accepted"),
        (QUOTATION_STATUS_DECLINED, "Declined"),
    ]

    opportunity = models.ForeignKey(
        Opportunity,
        on_delete=models.CASCADE,
        related_name="costing_headers",
    )
    is_archived = models.BooleanField(default=False, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="archived_quotations",
    )
    customer = models.ForeignKey(
        "Customer",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="costing_headers",
    )
    style_name = models.CharField(max_length=200, blank=True)
    style_code = models.CharField(max_length=50, blank=True)
    buyer = models.CharField(max_length=200, blank=True, default="")
    brand = models.CharField(max_length=200, blank=True, default="")
    product_type = models.CharField(
        max_length=50,
        choices=Opportunity.PRODUCT_TYPE_CHOICES,
        default="Other",
    )
    gender = models.CharField(max_length=50, blank=True, default="")
    size_range = models.CharField(max_length=100, blank=True, default="")
    season = models.CharField(max_length=100, blank=True, default="")
    factory_location = models.CharField(
        max_length=20,
        choices=FACTORY_LOCATION_CHOICES,
        default="bd",
    )
    order_quantity = models.PositiveIntegerField(default=0)
    moq = models.PositiveIntegerField(default=0)
    costing_date = models.DateField(null=True, blank=True)
    currency = models.CharField(
        max_length=10,
        choices=NEW_COSTING_CURRENCY_CHOICES,
        default="BDT",
    )
    exchange_rate = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
    )
    finance_percent_fabric = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("0"),
    )
    finance_percent_trims = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("0"),
    )
    commission_percent = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("0"),
    )
    target_margin_percent = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
    )
    manual_fob_per_piece = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
    )
    shipping_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        blank=True,
        default=Decimal("0"),
    )
    merchandiser = models.CharField(max_length=200, blank=True, default="")
    # Product specification snapshot
    fabric_type = models.CharField(max_length=200, blank=True, default="")
    fabric_gsm = models.CharField(max_length=100, blank=True, default="")
    fabric_composition = models.CharField(max_length=200, blank=True, default="")
    wash_type = models.CharField(max_length=200, blank=True, default="")
    print_type = models.CharField(max_length=200, blank=True, default="")
    embroidery = models.CharField(max_length=200, blank=True, default="")
    label_type = models.CharField(max_length=200, blank=True, default="")
    packaging_type = models.CharField(max_length=200, blank=True, default="")
    special_trims = models.CharField(max_length=300, blank=True, default="")
    fit_remarks = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=NEW_COSTING_STATUS_CHOICES,
        default="draft",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_costing_headers",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    quotation_number = models.CharField(max_length=50, blank=True, default="", db_index=True)
    quotation_status = models.CharField(
        max_length=20,
        choices=QUOTATION_STATUS_CHOICES,
        default=QUOTATION_STATUS_DRAFT,
        db_index=True,
    )
    quoted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="quoted_costing_headers",
    )
    quoted_at = models.DateTimeField(null=True, blank=True)
    quotation_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_client_quotations",
    )
    quotation_approved_at = models.DateTimeField(null=True, blank=True)
    quotation_rejected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="rejected_client_quotations",
    )
    quotation_rejected_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]

    def __str__(self):
        label = self.opportunity.opportunity_id if self.opportunity_id else f"Costing {self.pk}"
        return f"{label} (Header)"

    def save(self, *args, **kwargs):
        if not self.customer and self.opportunity and self.opportunity.customer_id:
            self.customer = self.opportunity.customer
        super().save(*args, **kwargs)


class QuickCosting(models.Model):
    DETAILED_PER_PIECE_COST_FIELDS = (
        "making_cost_per_piece",
        "print_embroidery_cost_per_piece",
        "trims_cost_per_piece",
        "packaging_cost_per_piece",
    )
    COSTING_TYPE_CHOICES = [
        ("quick", "Quick"),
    ]
    COMMISSION_NONE = "none"
    COMMISSION_FIXED = "fixed"
    COMMISSION_PERCENTAGE = "percentage"
    COMMISSION_TYPE_CHOICES = [
        (COMMISSION_NONE, "None"),
        (COMMISSION_FIXED, "Fixed Amount"),
        (COMMISSION_PERCENTAGE, "Percentage"),
    ]
    COMMISSION_CURRENCY_CHOICES = [
        ("BDT", "BDT"),
        ("CAD", "CAD"),
        ("USD", "USD"),
    ]
    PURPOSE_SAMPLE = "sample"
    PURPOSE_BULK = "bulk"
    PURPOSE_CHOICES = [
        (PURPOSE_SAMPLE, "Sample"),
        (PURPOSE_BULK, "Bulk Production"),
    ]
    PRICING_FULL_PACKAGE = "full_package"
    PRICING_FOB = "fob"
    PRICING_CMT = "cmt_sewing"
    PRICING_TYPE_CHOICES = [
        (PRICING_FULL_PACKAGE, "Full Package"),
        (PRICING_FOB, "FOB"),
        (PRICING_CMT, "CMT / Sewing Only"),
    ]
    STATUS_DRAFT = "draft"
    STATUS_SUBMITTED = "submitted"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_RECALL_REQUESTED = "recall_requested"
    STATUS_RECALLED = "recalled"
    STATUS_SUPERSEDED = "superseded"
    STATUS_QUOTED = "quoted"
    STATUS_INVOICED = "invoiced"
    STATUS_PRODUCTION = "production"
    STATUS_SHIPPED = "shipped"
    STATUS_CLOSED = "closed"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_SUBMITTED, "Pending Approval"),
        (STATUS_APPROVED, "CEO Approved"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_RECALL_REQUESTED, "Recall Requested"),
        (STATUS_RECALLED, "Recalled"),
        (STATUS_SUPERSEDED, "Superseded"),
        (STATUS_QUOTED, "Quoted"),
        (STATUS_INVOICED, "Invoiced"),
        (STATUS_PRODUCTION, "Production"),
        (STATUS_SHIPPED, "Shipped"),
        (STATUS_CLOSED, "Closed"),
    ]
    ACTIVE_APPROVED_STATUSES = (
        STATUS_APPROVED,
        STATUS_QUOTED,
        STATUS_INVOICED,
        STATUS_PRODUCTION,
        STATUS_SHIPPED,
        STATUS_CLOSED,
    )
    INACTIVE_REPORTING_STATUSES = (
        STATUS_REJECTED,
        STATUS_RECALL_REQUESTED,
        STATUS_RECALLED,
        STATUS_SUPERSEDED,
    )
    NON_BLOCKING_ACCOUNTING_STATUSES = (
        "draft",
        "test",
        "rolled_back",
        "rolled back",
        "rollback",
        "cancelled",
        "canceled",
        "void",
        "voided",
        "reversed",
    )
    REVISION_COPY_FIELDS = (
        "opportunity",
        "account_brand",
        "contact_name",
        "buyer_name",
        "project_name",
        "product_type",
        "costing_purpose",
        "pricing_type",
        "quantity",
        "exchange_rate_bdt_per_cad",
        "currency",
        "material_cost",
        "production_cost",
        "other_expenses",
        "shipping_cost",
        "selling_price_per_piece",
        "commission_per_piece",
        "commission_percent",
        "salesperson",
        "commission_type",
        "commission_value",
        "commission_currency",
        "fabric_cost_per_kg",
        "fabric_consumption_kg_per_piece",
        "making_cost_per_piece",
        "print_embroidery_cost_per_piece",
        "trims_cost_per_piece",
        "packaging_cost_per_piece",
        "target_margin_percent",
        "sewing_charge_per_piece_bdt",
        "sewing_cost_per_piece_bdt",
        "extra_local_cost_bdt",
    )

    costing_type = models.CharField(
        max_length=20,
        choices=COSTING_TYPE_CHOICES,
        default="quick",
        editable=False,
        db_index=True,
    )
    opportunity = models.ForeignKey(
        Opportunity,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="quick_costings",
    )
    account_brand = models.CharField(max_length=200, blank=True, default="")
    contact_name = models.CharField(max_length=200, blank=True, default="")
    buyer_name = models.CharField(max_length=200)
    project_name = models.CharField(max_length=200)
    product_type = models.CharField(
        max_length=50,
        choices=Opportunity.PRODUCT_TYPE_CHOICES,
        default="Other",
    )
    costing_purpose = models.CharField(
        max_length=20,
        choices=PURPOSE_CHOICES,
        default=PURPOSE_BULK,
        db_index=True,
    )
    pricing_type = models.CharField(
        max_length=20,
        choices=PRICING_TYPE_CHOICES,
        null=True,
        blank=True,
        db_index=True,
    )
    quantity = models.PositiveIntegerField(default=1)
    exchange_rate_bdt_per_cad = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
    )
    currency = models.CharField(
        max_length=3,
        choices=NEW_COSTING_CURRENCY_CHOICES,
        default="BDT",
        null=True,
        blank=True,
    )
    material_cost = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    production_cost = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    other_expenses = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    shipping_cost = models.DecimalField(max_digits=12, decimal_places=2, blank=True, default=Decimal("0"))
    selling_price_per_piece = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    commission_per_piece = models.DecimalField(max_digits=12, decimal_places=2, blank=True, default=Decimal("0"))
    commission_percent = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
    )
    salesperson = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sales_quick_costings",
    )
    commission_type = models.CharField(
        max_length=20,
        choices=COMMISSION_TYPE_CHOICES,
        default=COMMISSION_NONE,
    )
    commission_value = models.DecimalField(max_digits=12, decimal_places=2, blank=True, default=Decimal("0"))
    commission_currency = models.CharField(
        max_length=3,
        choices=COMMISSION_CURRENCY_CHOICES,
        default="BDT",
    )
    fabric_cost_per_kg = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    fabric_consumption_kg_per_piece = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    making_cost_per_piece = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    print_embroidery_cost_per_piece = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    trims_cost_per_piece = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    packaging_cost_per_piece = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    target_margin_percent = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
    )
    sewing_charge_per_piece_bdt = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    sewing_cost_per_piece_bdt = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    extra_local_cost_bdt = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
        db_index=True,
    )
    revision_number = models.PositiveIntegerField(default=1, db_index=True)
    revision_root = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="revision_children",
    )
    previous_revision = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="next_revisions",
    )
    superseded_by = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="superseded_versions",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_quick_costings",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="rejected_quick_costings",
    )
    rejected_at = models.DateTimeField(null=True, blank=True)
    approval_submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="submitted_quick_costings",
    )
    approval_submitted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    recall_requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="quick_costing_recall_requests",
    )
    recall_requested_at = models.DateTimeField(null=True, blank=True)
    recall_previous_status = models.CharField(max_length=20, blank=True, default="")
    recall_rejected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="quick_costing_recall_rejections",
    )
    recall_rejected_at = models.DateTimeField(null=True, blank=True)
    recalled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="quick_costings_recalled",
    )
    recalled_at = models.DateTimeField(null=True, blank=True)
    recall_reason = models.TextField(blank=True, default="")
    quotation_revision_required = models.BooleanField(default=False, db_index=True)
    quotation_revision_required_at = models.DateTimeField(null=True, blank=True)
    quotation_number = models.CharField(max_length=50, blank=True, default="", db_index=True)
    quoted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="quoted_quick_costings",
    )
    quoted_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="quick_costings",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]

    def __str__(self):
        return f"Quick Costing {self.pk} - {self.project_name}"

    @property
    def purpose_label(self):
        if self.costing_purpose == self.PURPOSE_SAMPLE:
            return "Sample Costing"
        return "Bulk Production Costing"

    @property
    def effective_pricing_type(self):
        return self.pricing_type or self.PRICING_FULL_PACKAGE

    @property
    def is_bangladesh_local_sewing(self):
        return self.effective_pricing_type == self.PRICING_CMT

    @property
    def service_type_label(self):
        return "Bangladesh Local Sewing" if self.is_bangladesh_local_sewing else "Canada Export"

    @property
    def revision_label(self):
        return f"V{self.revision_number or 1}"

    def revision_root_record(self):
        return self.revision_root if self.revision_root_id else self

    def revision_family_queryset(self):
        if not self.pk:
            return type(self).objects.none()
        root = self.revision_root_record()
        root_id = root.pk or self.pk
        return type(self).objects.filter(models.Q(pk=root_id) | models.Q(revision_root_id=root_id))

    def latest_revision(self):
        if not self.pk:
            return self
        return self.revision_family_queryset().order_by("-revision_number", "-created_at", "-pk").first()

    def latest_approved_revision(self):
        if not self.pk:
            return self if self.status in self.ACTIVE_APPROVED_STATUSES else None
        return (
            self.revision_family_queryset()
            .filter(status__in=self.ACTIVE_APPROVED_STATUSES)
            .order_by("-revision_number", "-approved_at", "-created_at", "-pk")
            .first()
        )

    @property
    def is_latest_revision(self):
        latest = self.latest_revision()
        return not latest or latest.pk == self.pk

    @property
    def is_latest_approved_revision(self):
        latest = self.latest_approved_revision()
        return bool(latest and latest.pk == self.pk)

    @property
    def counts_in_reporting(self):
        return (
            self.status in self.ACTIVE_APPROVED_STATUSES
            and self.is_latest_approved_revision
        )

    @classmethod
    def active_approved_statuses(cls):
        return cls.ACTIVE_APPROVED_STATUSES

    @classmethod
    def reporting_excluded_statuses(cls):
        return cls.INACTIVE_REPORTING_STATUSES

    @classmethod
    def active_accounting_entries(cls, queryset):
        for status in cls.NON_BLOCKING_ACCOUNTING_STATUSES:
            queryset = queryset.exclude(status__iexact=status)
        return queryset

    def recall_dependency_blockers(self):
        if not self.pk:
            return []
        blockers = []
        if self.invoices.exists():
            blockers.append("invoice")
        if InvoicePayment.objects.filter(invoice__quick_costing_id=self.pk).exists():
            blockers.append("payment")

        production_order = None
        try:
            production_order = self.production_order
        except ProductionOrder.DoesNotExist:
            production_order = None
        if production_order:
            blockers.append("production order")
            if production_order.shipments.exists():
                blockers.append("shipment")
        elif self.opportunity_id and Shipment.objects.filter(opportunity_id=self.opportunity_id).exists():
            blockers.append("shipment")

        accounting_filter = models.Q()
        has_accounting_filter = False
        if production_order:
            accounting_filter |= models.Q(production_order_id=production_order.pk)
            has_accounting_filter = True
        if self.opportunity_id:
            accounting_filter |= models.Q(opportunity_id=self.opportunity_id)
            has_accounting_filter = True
        accounting_entries = AccountingEntry.objects.filter(accounting_filter) if has_accounting_filter else AccountingEntry.objects.none()
        if has_accounting_filter and self.active_accounting_entries(accounting_entries).exists():
            blockers.append("accounting entry")
        return blockers

    def can_request_recall(self):
        return (
            self.status in {self.STATUS_APPROVED, self.STATUS_QUOTED}
            and self.is_latest_revision
            and not self.recall_dependency_blockers()
        )

    def can_create_revision_copy(self):
        return (
            self.status == self.STATUS_RECALLED
            and not self.superseded_by_id
            and not self.next_revisions.exists()
        )

    def create_revision_copy(self, *, user=None):
        if not self.pk:
            raise ValidationError("Quick Costing must be saved before creating a revision.")
        with transaction.atomic():
            source = (
                type(self).objects.select_for_update()
                .select_related("revision_root", "created_by")
                .get(pk=self.pk)
            )
            if not source.can_create_revision_copy():
                raise ValidationError("Only recalled Quick Costing can be revised.")

            root = source.revision_root_record()
            latest = (
                source.revision_family_queryset()
                .select_for_update()
                .order_by("-revision_number", "-created_at", "-pk")
                .first()
            )
            next_revision_number = ((latest.revision_number if latest else source.revision_number) or 1) + 1
            actor = user if user and getattr(user, "is_authenticated", False) else None
            revision_data = {
                field_name: getattr(source, field_name)
                for field_name in self.REVISION_COPY_FIELDS
            }
            revision_data.update(
                status=self.STATUS_DRAFT,
                revision_number=next_revision_number,
                revision_root=root,
                previous_revision=source,
                created_by=actor or source.created_by,
                approved_by=None,
                approved_at=None,
                rejected_by=None,
                rejected_at=None,
                approval_submitted_by=None,
                approval_submitted_at=None,
                recall_requested_by=None,
                recall_requested_at=None,
                recall_previous_status="",
                recall_rejected_by=None,
                recall_rejected_at=None,
                recalled_by=None,
                recalled_at=None,
                recall_reason="",
                quotation_number="",
                quoted_by=None,
                quoted_at=None,
                quotation_revision_required=False,
                quotation_revision_required_at=None,
            )
            new_revision = type(self).objects.create(**revision_data)
            return new_revision

    def clean(self):
        super().clean()
        if self.is_bangladesh_local_sewing:
            if self.currency != "BDT":
                raise ValidationError({"currency": "CMT / Sewing Only must use BDT."})
            if not self.sewing_charge_per_piece_bdt or self.sewing_charge_per_piece_bdt <= 0:
                raise ValidationError(
                    {"sewing_charge_per_piece_bdt": "Sewing charge must be greater than zero."}
                )
            if self.sewing_cost_per_piece_bdt is not None and self.sewing_cost_per_piece_bdt < 0:
                raise ValidationError({"sewing_cost_per_piece_bdt": "Sewing cost cannot be negative."})
            if self.extra_local_cost_bdt is not None and self.extra_local_cost_bdt < 0:
                raise ValidationError({"extra_local_cost_bdt": "Extra local cost cannot be negative."})
        if (
            self.created_by_id
            and self.approved_by_id == self.created_by_id
            and self.approved_by
            and not self.approved_by.is_superuser
            and not getattr(self, "_authorized_self_approval", False)
        ):
            raise ValidationError("The costing creator cannot approve their own Quick Costing.")

    def save(self, *args, **kwargs):
        if self.approved_by_id and self.created_by_id == self.approved_by_id:
            previous_approver_id = None
            if self.pk:
                previous_approver_id = (
                    type(self).objects.filter(pk=self.pk).values_list("approved_by_id", flat=True).first()
                )
            if (
                previous_approver_id != self.approved_by_id
                and not getattr(self, "_authorized_self_approval", False)
            ):
                approver = self.approved_by
                if approver and not approver.is_superuser:
                    raise ValidationError("The costing creator cannot approve their own Quick Costing.")
        return super().save(*args, **kwargs)

    @property
    def is_locked(self):
        return self.status in {
            self.STATUS_APPROVED,
            self.STATUS_QUOTED,
            self.STATUS_RECALL_REQUESTED,
            self.STATUS_RECALLED,
            self.STATUS_SUPERSEDED,
            self.STATUS_INVOICED,
            self.STATUS_PRODUCTION,
            self.STATUS_SHIPPED,
            self.STATUS_CLOSED,
        }

    def _commission_currency(self):
        currency = (self.commission_currency or self.currency or "BDT").upper().strip()
        return currency if currency in {"BDT", "CAD", "USD"} else "BDT"

    def _usd_to_bdt_rate(self):
        opportunity = getattr(self, "opportunity", None)
        rate = getattr(opportunity, "fx_rate_bdt_per_usd", None) if opportunity else None
        if rate and rate > 0:
            return rate
        return None

    def _fixed_commission_in_costing_currency(self, value, exchange_rate):
        costing_currency = (self.currency or "BDT").upper()
        commission_currency = self._commission_currency()
        if commission_currency == costing_currency:
            return value, True
        if commission_currency == "CAD" and costing_currency == "BDT" and exchange_rate:
            return value * exchange_rate, True
        if commission_currency == "BDT" and costing_currency == "CAD" and exchange_rate:
            return value / exchange_rate, True
        if commission_currency == "USD" and costing_currency == "BDT":
            usd_rate = self._usd_to_bdt_rate()
            if usd_rate:
                return value * usd_rate, True
        if commission_currency == "BDT" and costing_currency == "USD":
            usd_rate = self._usd_to_bdt_rate()
            if usd_rate:
                return value / usd_rate, True
        return Decimal("0"), False

    def _commission_display_amount(self, commission_total, exchange_rate):
        costing_currency = (self.currency or "BDT").upper()
        commission_currency = self._commission_currency()
        if commission_currency == costing_currency:
            return commission_total, True
        if commission_currency == "CAD" and costing_currency == "BDT" and exchange_rate:
            return commission_total / exchange_rate, True
        if commission_currency == "BDT" and costing_currency == "CAD" and exchange_rate:
            return commission_total * exchange_rate, True
        if commission_currency == "USD" and costing_currency == "BDT":
            usd_rate = self._usd_to_bdt_rate()
            if usd_rate:
                return commission_total / usd_rate, True
        if commission_currency == "BDT" and costing_currency == "USD":
            usd_rate = self._usd_to_bdt_rate()
            if usd_rate:
                return commission_total * usd_rate, True
        return Decimal("0"), False

    def _commission_summary(self, profit_before_commission, quantity, exchange_rate):
        commission_type = self.commission_type or self.COMMISSION_NONE
        if commission_type not in {self.COMMISSION_NONE, self.COMMISSION_FIXED, self.COMMISSION_PERCENTAGE}:
            commission_type = self.COMMISSION_NONE

        value = self.commission_value or Decimal("0")
        if value < 0:
            value = Decimal("0")

        conversion_available = True
        if commission_type == self.COMMISSION_FIXED and value > 0:
            commission_total, conversion_available = self._fixed_commission_in_costing_currency(value, exchange_rate)
        elif commission_type == self.COMMISSION_PERCENTAGE and value > 0:
            commission_base = profit_before_commission if profit_before_commission > 0 else Decimal("0")
            commission_total = (commission_base * value / Decimal("100")).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )
        else:
            commission_total = Decimal("0")

        commission_per_piece = (commission_total / quantity) if quantity else Decimal("0")
        display_amount, display_available = self._commission_display_amount(commission_total, exchange_rate)
        return {
            "commission_type": commission_type,
            "commission_value": value,
            "commission_currency": self._commission_currency(),
            "commission_total": commission_total,
            "commission_per_piece": commission_per_piece,
            "commission_amount_calculated": commission_total,
            "commission_display_amount": display_amount,
            "commission_display_available": display_available,
            "commission_conversion_available": conversion_available,
        }

    def _legacy_commission_summary(self, selling_price_per_piece, quantity):
        zero = Decimal("0")
        commission_percent = self.commission_percent
        commission_type = self.COMMISSION_NONE
        commission_value = zero
        if commission_percent is not None:
            if Decimal("0") <= commission_percent <= Decimal("100"):
                commission_per_piece = (selling_price_per_piece * commission_percent / Decimal("100")).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP,
                )
                commission_total = commission_per_piece * quantity
                commission_type = self.COMMISSION_PERCENTAGE
                commission_value = commission_percent
            else:
                commission_per_piece = zero
                commission_total = zero
        else:
            commission_per_piece = self.commission_per_piece or zero
            if commission_per_piece < zero:
                commission_per_piece = zero
            commission_total = commission_per_piece * quantity
            if commission_total:
                commission_type = self.COMMISSION_FIXED
                commission_value = commission_total
        return {
            "commission_type": commission_type,
            "commission_value": commission_value,
            "commission_currency": self.currency or "BDT",
            "commission_total": commission_total,
            "commission_per_piece": commission_per_piece,
            "commission_amount_calculated": commission_total,
            "commission_display_amount": commission_total,
            "commission_display_available": True,
            "commission_conversion_available": True,
        }

    def calculation_summary(self):
        quantity = Decimal(self.quantity or 0)
        if self.is_bangladesh_local_sewing:
            zero = Decimal("0")
            charge = self.sewing_charge_per_piece_bdt or zero
            cost = self.sewing_cost_per_piece_bdt
            extra_cost = self.extra_local_cost_bdt or zero
            sales_value = charge * quantity
            shipping_cost = zero
            net_revenue = sales_value - shipping_cost - extra_cost
            cost_available = cost is not None and cost > 0
            product_production_cost = (cost * quantity) if cost_available else zero
            total_cost = (product_production_cost + shipping_cost + extra_cost) if cost_available else zero
            profit = net_revenue - product_production_cost if cost_available else zero
            cost_per_piece = (total_cost / quantity) if quantity and cost_available else zero
            product_production_cost_per_piece = (
                product_production_cost / quantity
                if quantity and cost_available
                else zero
            )
            commission = self._commission_summary(profit, quantity, None)
            commission_per_piece = commission["commission_per_piece"]
            commission_total = commission["commission_total"]
            net_profit = profit - commission_total
            profit_per_piece = (profit / quantity) if quantity and cost_available else zero
            net_profit_per_piece = profit_per_piece - commission_per_piece
            margin = ((profit / sales_value) * Decimal("100")) if sales_value and cost_available else zero
            net_margin = ((net_profit / sales_value) * Decimal("100")) if sales_value and cost_available else zero
            return {
                "quantity": quantity,
                "currency": "BDT",
                "is_legacy_currency": False,
                "exchange_rate": None,
                "uses_detailed_costing": False,
                "fabric_cost_per_kg": zero,
                "fabric_consumption_kg_per_piece": zero,
                "fabric_cost_per_piece": zero,
                "making_cost_per_piece": cost or zero,
                "print_embroidery_cost_per_piece": zero,
                "trims_cost_per_piece": zero,
                "packaging_cost_per_piece": zero,
                "sales_value": sales_value,
                "net_revenue": net_revenue,
                "product_production_cost_total": product_production_cost,
                "product_production_cost_per_piece": product_production_cost_per_piece,
                "material_cost_total": zero,
                "material_cost_per_piece": zero,
                "production_cost_total": product_production_cost,
                "production_cost_per_piece": product_production_cost_per_piece,
                "other_expenses_total": extra_cost,
                "other_expenses_per_piece": (extra_cost / quantity) if quantity else zero,
                "shipping_cost_total": shipping_cost,
                "shipping_cost_per_piece": zero,
                "total_cost": total_cost,
                "cost_per_piece": cost_per_piece,
                "selling_price_per_piece": charge,
                "selling_price_total": sales_value,
                "revenue": sales_value,
                "gross_profit_per_piece": profit_per_piece,
                "gross_profit_total": profit,
                "commission_per_piece": commission_per_piece,
                "commission_total": commission_total,
                "commission_percent": None,
                "commission_amount_calculated": commission["commission_amount_calculated"],
                "commission_type": commission["commission_type"],
                "commission_value": commission["commission_value"],
                "commission_currency": commission["commission_currency"],
                "commission_display_amount": commission["commission_display_amount"],
                "commission_display_available": commission["commission_display_available"],
                "commission_conversion_available": commission["commission_conversion_available"],
                "profit_before_commission": profit,
                "profit_before_commission_per_piece": profit_per_piece,
                "final_profit_after_commission": net_profit,
                "final_profit_after_commission_per_piece": net_profit_per_piece,
                "net_profit_per_piece": net_profit_per_piece,
                "net_profit_total": net_profit,
                "gross_profit_margin_percent": margin,
                "net_profit_margin_percent": net_margin,
                "target_margin_percent": self.target_margin_percent,
                "margin_status": "Cost unavailable" if not cost_available else "Calculated",
                "profit_per_piece": profit_per_piece,
                "total_profit": profit,
                "profit_margin_percent": margin,
                "cost_available": cost_available,
            }
        exchange_rate = self.exchange_rate_bdt_per_cad or None
        material_cost = self.material_cost or Decimal("0")
        production_cost = self.production_cost or Decimal("0")
        other_expenses = self.other_expenses or Decimal("0")
        shipping_cost = self.shipping_cost or Decimal("0")
        selling_price_per_piece = self.selling_price_per_piece or Decimal("0")
        detailed_values = (
            self.fabric_cost_per_kg,
            self.fabric_consumption_kg_per_piece,
            *(getattr(self, field_name) for field_name in self.DETAILED_PER_PIECE_COST_FIELDS),
        )
        uses_detailed_costing = any(value is not None for value in detailed_values)
        fabric_cost_per_kg = self.fabric_cost_per_kg or Decimal("0")
        fabric_consumption_kg_per_piece = self.fabric_consumption_kg_per_piece or Decimal("0")
        fabric_cost_per_piece = fabric_cost_per_kg * fabric_consumption_kg_per_piece
        per_piece_components = {
            field_name: getattr(self, field_name) or Decimal("0")
            for field_name in self.DETAILED_PER_PIECE_COST_FIELDS
        }
        making_cost_per_piece = per_piece_components["making_cost_per_piece"]
        print_embroidery_cost_per_piece = per_piece_components["print_embroidery_cost_per_piece"]
        trims_cost_per_piece = per_piece_components["trims_cost_per_piece"]
        packaging_cost_per_piece = per_piece_components["packaging_cost_per_piece"]
        detailed_component_total_per_piece = sum(per_piece_components.values(), Decimal("0"))
        other_expenses_per_piece = (other_expenses / quantity) if quantity else Decimal("0")
        shipping_cost_per_piece = (shipping_cost / quantity) if quantity else Decimal("0")

        if uses_detailed_costing:
            material_cost = fabric_cost_per_piece * quantity
            production_cost = detailed_component_total_per_piece * quantity
            product_production_cost_per_piece = fabric_cost_per_piece + detailed_component_total_per_piece
            product_production_cost_total = material_cost + production_cost
        else:
            product_production_cost_total = material_cost + production_cost
            product_production_cost_per_piece = (
                product_production_cost_total / quantity
                if quantity
                else Decimal("0")
            )

        sales_value = selling_price_per_piece * quantity
        net_revenue = sales_value - shipping_cost - other_expenses
        total_cost = product_production_cost_total + other_expenses + shipping_cost
        cost_per_piece = (total_cost / quantity) if quantity else Decimal("0")
        gross_profit_total = net_revenue - product_production_cost_total
        gross_profit_per_piece = (gross_profit_total / quantity) if quantity else Decimal("0")
        commission = self._commission_summary(gross_profit_total, quantity, exchange_rate)
        if commission["commission_type"] == self.COMMISSION_NONE:
            commission = self._legacy_commission_summary(selling_price_per_piece, quantity)
        commission_per_piece = commission["commission_per_piece"]
        commission_total = commission_per_piece * quantity
        net_profit_total = gross_profit_total - commission_total
        net_profit_per_piece = (net_profit_total / quantity) if quantity else Decimal("0")
        gross_profit_margin_percent = ((gross_profit_total / sales_value) * Decimal("100")) if sales_value else Decimal("0")
        net_profit_margin_percent = ((net_profit_total / sales_value) * Decimal("100")) if sales_value else Decimal("0")
        target_margin_percent = self.target_margin_percent
        if target_margin_percent is None:
            margin_status = "No target set"
        elif net_profit_margin_percent >= target_margin_percent:
            margin_status = "Meets target"
        else:
            margin_status = "Below target"
        return {
            "quantity": quantity,
            "currency": self.currency or "BDT",
            "is_legacy_currency": self.currency is None,
            "exchange_rate": exchange_rate,
            "uses_detailed_costing": uses_detailed_costing,
            "fabric_cost_per_kg": fabric_cost_per_kg,
            "fabric_consumption_kg_per_piece": fabric_consumption_kg_per_piece,
            "fabric_cost_per_piece": fabric_cost_per_piece,
            "making_cost_per_piece": making_cost_per_piece,
            "print_embroidery_cost_per_piece": print_embroidery_cost_per_piece,
            "trims_cost_per_piece": trims_cost_per_piece,
            "packaging_cost_per_piece": packaging_cost_per_piece,
            "sales_value": sales_value,
            "net_revenue": net_revenue,
            "product_production_cost_total": product_production_cost_total,
            "product_production_cost_per_piece": product_production_cost_per_piece,
            "material_cost_total": material_cost,
            "material_cost_per_piece": (material_cost / quantity) if quantity else Decimal("0"),
            "production_cost_total": production_cost,
            "production_cost_per_piece": (production_cost / quantity) if quantity else Decimal("0"),
            "other_expenses_total": other_expenses,
            "other_expenses_per_piece": other_expenses_per_piece,
            "shipping_cost_total": shipping_cost,
            "shipping_cost_per_piece": shipping_cost_per_piece,
            "total_cost": total_cost,
            "cost_per_piece": cost_per_piece,
            "selling_price_per_piece": selling_price_per_piece,
            "selling_price_total": sales_value,
            "revenue": sales_value,
            "gross_profit_per_piece": gross_profit_per_piece,
            "gross_profit_total": gross_profit_total,
            "commission_per_piece": commission_per_piece,
            "commission_total": commission_total,
            "commission_percent": self.commission_percent,
            "commission_amount_calculated": commission["commission_amount_calculated"],
            "commission_type": commission["commission_type"],
            "commission_value": commission["commission_value"],
            "commission_currency": commission["commission_currency"],
            "commission_display_amount": commission["commission_display_amount"],
            "commission_display_available": commission["commission_display_available"],
            "commission_conversion_available": commission["commission_conversion_available"],
            "profit_before_commission": gross_profit_total,
            "profit_before_commission_per_piece": gross_profit_per_piece,
            "final_profit_after_commission": net_profit_total,
            "final_profit_after_commission_per_piece": net_profit_per_piece,
            "net_profit_per_piece": net_profit_per_piece,
            "net_profit_total": net_profit_total,
            "gross_profit_margin_percent": gross_profit_margin_percent,
            "net_profit_margin_percent": net_profit_margin_percent,
            "target_margin_percent": target_margin_percent,
            "margin_status": margin_status,
            "profit_per_piece": gross_profit_per_piece,
            "total_profit": gross_profit_total,
            "profit_margin_percent": gross_profit_margin_percent,
        }


class CostingLineItem(models.Model):
    costing = models.ForeignKey(
        CostingHeader,
        on_delete=models.CASCADE,
        related_name="line_items",
    )
    category = models.CharField(
        max_length=30,
        choices=NEW_COSTING_CATEGORY_CHOICES,
        default="other",
    )
    item_name = models.CharField(max_length=200)
    description = models.CharField(max_length=300, blank=True, default="")
    item_reference = models.CharField(max_length=200, blank=True, default="")
    supplier = models.CharField(max_length=200, blank=True, default="")
    uom = models.CharField(
        max_length=20,
        choices=NEW_COSTING_UOM_CHOICES,
        default="piece",
    )
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    freight = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    consumption_value = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    wastage_percent = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("0"),
    )
    denominator_value = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
    )
    placement = models.CharField(max_length=120, blank=True, default="")
    color = models.CharField(max_length=120, blank=True, default="")
    gsm = models.CharField(max_length=120, blank=True, default="")
    cut_width = models.CharField(max_length=120, blank=True, default="")
    ship_mode = models.CharField(max_length=120, blank=True, default="")
    pay_mode = models.CharField(max_length=120, blank=True, default="")
    sort_order = models.PositiveIntegerField(default=0)
    remarks = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["category", "sort_order", "id"]

    def __str__(self):
        return f"{self.category} - {self.item_name}"


class CostingSMV(models.Model):
    costing = models.OneToOneField(
        CostingHeader,
        on_delete=models.CASCADE,
        related_name="smv",
    )
    machine_smv = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    finishing_smv = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    cpm = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    efficiency_costing = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("100"),
    )
    efficiency_planned = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("100"),
    )

    def __str__(self):
        return f"SMV {self.costing_id}"


class CostingAuditLog(models.Model):
    ACTION_CHOICES = [
        ("created", "Created"),
        ("updated", "Updated"),
        ("approved", "Approved"),
        ("unlocked", "Unlocked"),
        ("quoted", "Converted to quotation"),
        ("invoice_created", "Converted to invoice"),
        ("production_created", "Converted to production order"),
        ("exported", "Exported"),
        ("uploaded_file", "Uploaded file"),
    ]

    costing = models.ForeignKey(
        CostingHeader,
        on_delete=models.CASCADE,
        related_name="audits",
    )
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="costing_audits",
    )
    changed_at = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=255, blank=True, default="")
    before_data = models.JSONField(null=True, blank=True)
    after_data = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ["-changed_at", "-id"]

    def __str__(self):
        return f"{self.costing_id} {self.action}"


class CostingSnapshot(models.Model):
    costing = models.ForeignKey(
        CostingHeader,
        on_delete=models.CASCADE,
        related_name="snapshots",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    source = models.CharField(max_length=50, default="approval")
    data = models.JSONField()

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.costing_id} snapshot"


class CostLineItem(models.Model):
    cost_sheet = models.ForeignKey(
        CostSheet,
        on_delete=models.CASCADE,
        related_name="line_items",
    )
    section = models.CharField(
        max_length=20,
        choices=COST_SECTION_CHOICES,
        default="other",
    )
    item_name = models.CharField(max_length=200)
    uom = models.CharField(max_length=30, blank=True)
    consumption_per_piece = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    waste_percent = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("0"),
    )
    rate = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    setup_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
    )
    total_cost_per_piece = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["section", "id"]

    def __str__(self):
        return f"{self.section} - {self.item_name}"

    def save(self, *args, **kwargs):
        consumption = self.consumption_per_piece or Decimal("0")
        rate = self.rate or Decimal("0")
        waste = self.waste_percent or Decimal("0")
        setup = self.setup_cost or Decimal("0")

        base = consumption * rate * (Decimal("1") + (waste / Decimal("100")))
        qty = self.cost_sheet.target_quantity if self.cost_sheet else 0
        setup_per_piece = setup / Decimal(qty) if qty else Decimal("0")
        self.total_cost_per_piece = base + setup_per_piece
        super().save(*args, **kwargs)


class ActualCostEntry(models.Model):
    production_order = models.ForeignKey(
        "ProductionOrder",
        on_delete=models.CASCADE,
        related_name="actual_cost_entries",
    )
    opportunity = models.ForeignKey(
        Opportunity,
        on_delete=models.CASCADE,
        related_name="actual_cost_entries",
    )
    cost_sheet = models.ForeignKey(
        CostSheet,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="actual_cost_entries",
    )
    section = models.CharField(
        max_length=20,
        choices=COST_SECTION_CHOICES,
        default="other",
    )
    item_name = models.CharField(max_length=200)
    uom = models.CharField(max_length=30, blank=True)
    actual_qty_total = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    actual_rate = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0"),
    )
    actual_total_cost = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        default=Decimal("0"),
    )
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.section} - {self.item_name}"

    def save(self, *args, **kwargs):
        qty = self.actual_qty_total or Decimal("0")
        rate = self.actual_rate or Decimal("0")
        if not self.actual_total_cost:
            self.actual_total_cost = qty * rate
        super().save(*args, **kwargs)


class OpportunityDocument(models.Model):
    opportunity = models.ForeignKey(
        Opportunity,
        on_delete=models.CASCADE,
        related_name="documents",
    )
    file = models.FileField(upload_to="opportunity_documents/")
    original_name = models.CharField(max_length=255, blank=True)
    doc_type = models.CharField(
        max_length=30,
        choices=OPPORTUNITY_DOC_TYPES,
        default="other",
    )
    cost_sheet = models.ForeignKey(
        CostSheet,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="documents",
    )
    cost_sheet_simple = models.ForeignKey(
        CostSheetSimple,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="documents",
    )
    costing_header = models.ForeignKey(
        "CostingHeader",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="documents",
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="opportunity_documents",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at", "-id"]

    def __str__(self):
        return self.original_name or self.file.name

    def save(self, *args, **kwargs):
        if self.file and not self.original_name:
            self.original_name = (getattr(self.file, "name", "") or "")[:255]
        super().save(*args, **kwargs)


class CostSheetAudit(models.Model):
    ACTION_CHOICES = [
        ("created_version", "Created version"),
        ("approved", "Approved"),
        ("locked", "Locked"),
        ("exported", "Exported"),
        ("uploaded_file", "Uploaded file"),
        ("edited_actual", "Edited actual costs"),
    ]

    cost_sheet = models.ForeignKey(
        CostSheet,
        on_delete=models.CASCADE,
        related_name="audits",
    )
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    changed_at = models.DateTimeField(auto_now_add=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="cost_sheet_audits",
    )
    note = models.CharField(max_length=255, blank=True, default="")
    before_data = models.JSONField(null=True, blank=True)
    after_data = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ["-changed_at", "-id"]

    def __str__(self):
        return f"{self.cost_sheet_id} {self.action}"

# -----------------------------------
# Product and master tables
# -----------------------------------


class Product(models.Model):
    product_code = models.CharField(max_length=50, unique=True, blank=True)
    name = models.CharField(max_length=200)

    product_type = models.CharField(
        max_length=50,
        choices=Opportunity.PRODUCT_TYPE_CHOICES,
        default="Other",
    )
    product_category = models.CharField(
        max_length=50,
        choices=Opportunity.PRODUCT_CATEGORY_CHOICES,
        default="Other",
    )

    default_gsm = models.CharField(max_length=50, blank=True)
    default_fabric = models.CharField(max_length=100, blank=True)
    default_moq = models.IntegerField(null=True, blank=True)
    default_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )

    image = models.ImageField(
        upload_to="product_images/",
        null=True,
        blank=True,
    )

    notes = models.TextField(blank=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.product_code:
            last_id = Product.objects.count() + 1
            self.product_code = f"P{last_id:04}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.product_code} - {self.name}"


class ProductTypeMaster(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ProductCategoryMaster(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class FabricNameMaster(models.Model):
    name = models.CharField(max_length=150, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class GSMRangeMaster(models.Model):
    name = models.CharField(max_length=50, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class FabricGroupMaster(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Fabric group"
        verbose_name_plural = "Fabric groups"

    def __str__(self):
        return self.name


class FabricTypeMaster(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class KnitStructureMaster(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class WeaveMaster(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class SurfaceMaster(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class HandfeelMaster(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class FabricChoiceMaster(models.Model):
    name = models.CharField(max_length=100, unique=True)
    category = models.CharField(
        max_length=50,
        choices=[
            ("group", "Fabric Group"),
            ("type", "Fabric Type"),
            ("structure", "Knit Structure"),
            ("weave", "Weave"),
            ("surface", "Surface"),
            ("handfeel", "Handfeel"),
        ],
        default="group",
    )

    def __str__(self):
        return f"{self.name} ({self.category})"


# -----------------------------------
# Fabric, accessory, trim, thread
# -----------------------------------


class Fabric(models.Model):
    fabric_code = models.CharField(max_length=50, unique=True, blank=True)
    name = models.CharField(max_length=200)

    fabric_group = models.CharField(max_length=100, blank=True)
    fabric_type = models.CharField(max_length=100, blank=True)
    weave = models.CharField(max_length=100, blank=True)
    knit_structure = models.CharField(max_length=100, blank=True)
    construction = models.CharField(max_length=200, blank=True)
    composition = models.CharField(max_length=200, blank=True)
    gsm = models.CharField(max_length=50, blank=True)

    stretch_type = models.CharField(max_length=100, blank=True)
    surface = models.CharField(max_length=100, blank=True)
    handfeel = models.CharField(max_length=100, blank=True)
    drape = models.CharField(max_length=100, blank=True)
    warmth = models.CharField(max_length=100, blank=True)
    weight_class = models.CharField(max_length=100, blank=True)
    breathability = models.CharField(max_length=100, blank=True)
    sheerness = models.CharField(max_length=100, blank=True)
    shrinkage = models.CharField(max_length=100, blank=True)
    durability = models.CharField(max_length=100, blank=True)

    color_options = models.CharField(max_length=255, blank=True)

    price_per_kg = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    price_per_meter = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )

    image = models.ImageField(
        upload_to="fabric_images/",
        null=True,
        blank=True,
    )

    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.fabric_code:
            last_id = Fabric.objects.count() + 1
            self.fabric_code = f"F{last_id:04}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.fabric_code} - {self.name}"


class Accessory(models.Model):
    accessory_code = models.CharField(max_length=50, unique=True, blank=True)
    name = models.CharField(max_length=200)

    accessory_type = models.CharField(max_length=100, blank=True)
    size = models.CharField(max_length=100, blank=True)
    color = models.CharField(max_length=100, blank=True)
    material = models.CharField(max_length=100, blank=True)
    finish = models.CharField(max_length=100, blank=True)

    supplier = models.CharField(max_length=200, blank=True)
    price_per_unit = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True
    )

    image = models.ImageField(
        upload_to="accessory_images/",
        null=True,
        blank=True,
    )

    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.accessory_code:
            last_id = Accessory.objects.count() + 1
            self.accessory_code = f"A{last_id:04}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.accessory_code} - {self.name}"


class Trim(models.Model):
    trim_code = models.CharField(max_length=50, unique=True, blank=True)
    name = models.CharField(max_length=200)

    trim_type = models.CharField(max_length=100, blank=True)
    width = models.CharField(max_length=50, blank=True)
    color = models.CharField(max_length=100, blank=True)
    material = models.CharField(max_length=100, blank=True)

    price_per_meter = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )

    image = models.ImageField(
        upload_to="trim_images/",
        null=True,
        blank=True,
    )

    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.trim_code:
            last_id = Trim.objects.count() + 1
            self.trim_code = f"T{last_id:04}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.trim_code} - {self.name}"


class ThreadOption(models.Model):
    thread_code = models.CharField(max_length=50, unique=True, blank=True)
    name = models.CharField(max_length=200)

    thread_type = models.CharField(max_length=100, blank=True)
    count = models.CharField(max_length=100, blank=True)
    color = models.CharField(max_length=100, blank=True)
    brand = models.CharField(max_length=100, blank=True)
    use_for = models.CharField(max_length=200, blank=True)

    price_per_cone = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )

    image = models.ImageField(
        upload_to="thread_images/",
        null=True,
        blank=True,
    )

    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.thread_code:
            last_id = ThreadOption.objects.count() + 1
            self.thread_code = f"TH{last_id:04}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.thread_code} - {self.name}"


# -----------------------------------
# Inventory
# -----------------------------------
# at top of file you already have:
from django.db import models
from django.utils import timezone
from django.conf import settings


class InventoryItem(models.Model):
    CATEGORY_CHOICES = [
        ("thread", "Thread"),
        ("needle", "Needle"),
        ("accessory", "Accessory"),
        ("trim", "Trim"),
        ("fabric_roll", "Fabric roll"),
        ("polybag", "Polybag"),
        ("carton", "Carton"),
        ("other", "Other"),
    ]
    MATERIAL_GROUP_CHOICES = [
        ("fabric", "Fabric"),
        ("trim", "Trim"),
        ("label", "Label"),
        ("packaging", "Packaging"),
        ("printing_material", "Printing Material"),
        ("accessories", "Accessories"),
        ("sample_material", "Sample Material"),
        ("other", "Other"),
    ]

    name = models.CharField(max_length=200)
    category = models.CharField(
        max_length=50,
        choices=CATEGORY_CHOICES,
        default="other",
    )
    material_group = models.CharField(
        max_length=40,
        choices=MATERIAL_GROUP_CHOICES,
        default="other",
        db_index=True,
    )

    product = models.ForeignKey("Product", null=True, blank=True, on_delete=models.SET_NULL)
    fabric = models.ForeignKey("Fabric", null=True, blank=True, on_delete=models.SET_NULL)
    accessory = models.ForeignKey("Accessory", null=True, blank=True, on_delete=models.SET_NULL)
    trim = models.ForeignKey("Trim", null=True, blank=True, on_delete=models.SET_NULL)
    thread_option = models.ForeignKey("ThreadOption", null=True, blank=True, on_delete=models.SET_NULL)

    sku = models.CharField(max_length=100, blank=True)
    code = models.CharField(max_length=100, blank=True)

    unit_type = models.CharField(max_length=50, default="pcs")
    unit_cost = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    min_level = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    minimum_stock = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    reorder_level = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    incoming_quantity = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    reserved_quantity = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    damaged_quantity = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    waste_quantity = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    supplier_name = models.CharField(max_length=200, blank=True, default="")

    location = models.CharField(max_length=200, blank=True)
    image = models.ImageField(upload_to="inventory_images/", null=True, blank=True)
    notes = models.TextField(blank=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.quantity} {self.unit_type})"

    @property
    def effective_material_group(self):
        if self.material_group and self.material_group != "other":
            return self.material_group
        return {
            "fabric_roll": "fabric",
            "trim": "trim",
            "polybag": "packaging",
            "carton": "packaging",
            "accessory": "accessories",
            "thread": "sample_material",
            "needle": "sample_material",
        }.get(self.category, "other")

    @property
    def effective_minimum_stock(self):
        return self.minimum_stock or self.min_level or Decimal("0")

    @property
    def effective_reorder_level(self):
        return self.reorder_level or self.effective_minimum_stock

    @property
    def available_quantity(self):
        return (self.quantity or Decimal("0")) - (self.reserved_quantity or Decimal("0"))

    @property
    def stock_value(self):
        if self.unit_cost is None or self.quantity is None:
            return Decimal("0")
        return (self.unit_cost or Decimal("0")) * (self.quantity or Decimal("0"))

    @property
    def reserved_value(self):
        if self.unit_cost is None:
            return Decimal("0")
        return (self.unit_cost or Decimal("0")) * (self.reserved_quantity or Decimal("0"))

    @property
    def waste_value(self):
        if self.unit_cost is None:
            return Decimal("0")
        return (self.unit_cost or Decimal("0")) * ((self.waste_quantity or Decimal("0")) + (self.damaged_quantity or Decimal("0")))

    @property
    def needs_reorder(self):
        return (self.quantity or Decimal("0")) <= self.effective_reorder_level


class InventoryReorder(models.Model):
    inventory_item = models.ForeignKey(
        "InventoryItem",
        on_delete=models.CASCADE,
        related_name="reorders",
        null=True,
        blank=True,
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    note = models.CharField(max_length=255, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"Reorder for {self.inventory_item} - {self.quantity}"


class InventoryMovement(models.Model):
    MOVEMENT_CHOICES = [
        ("received", "Received"),
        ("allocated", "Allocated"),
        ("consumed", "Consumed"),
        ("adjusted", "Adjusted"),
        ("damaged", "Damaged"),
    ]

    inventory_item = models.ForeignKey(
        "InventoryItem",
        on_delete=models.CASCADE,
        related_name="movements",
    )
    movement_type = models.CharField(max_length=20, choices=MOVEMENT_CHOICES)
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.CharField(max_length=255, blank=True, default="")
    production_order = models.ForeignKey(
        "ProductionOrder",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inventory_movements",
    )
    production_material = models.ForeignKey(
        "ProductionOrderMaterial",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="movements",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["inventory_item", "movement_type"]),
            models.Index(fields=["production_order", "movement_type"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.inventory_item} - {self.get_movement_type_display()} {self.quantity}"


from django.utils import timezone

# ==============================
# CALENDAR EVENT MODEL
# ==============================

class Event(models.Model):
    EVENT_TYPE_CHOICES = [
        ("call", "Call"),
        ("follow_up", "Follow up"),
        ("sample", "Sample work"),
        ("production", "Production"),
        ("shipping", "Shipping"),
        ("payment", "Payment reminder"),
        ("other", "Other"),
    ]

    PRIORITY_CHOICES = [
        ("low", "Low"),
        ("medium", "Medium"),
        ("high", "High"),
    ]

    STATUS_CHOICES = [
        ("planned", "Planned"),
        ("in_work", "In work"),
        ("done", "Done"),
    ]

    PRODUCTION_STAGE_CHOICES = [
        ("development", "Development"),
        ("sampling", "Sampling"),
        ("bulk_cutting", "Bulk cutting"),
        ("sewing", "Sewing"),
        ("finishing", "Finishing"),
        ("packing", "Packing"),
        ("shipped", "Shipped"),
    ]

    title = models.CharField(max_length=255)

    start_datetime = models.DateTimeField()
    end_datetime = models.DateTimeField(blank=True, null=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="created_calendar_events",
    )

    event_type = models.CharField(
        max_length=50,
        choices=EVENT_TYPE_CHOICES,
        default="call",
    )

    priority = models.CharField(
        max_length=20,
        choices=PRIORITY_CHOICES,
        default="medium",
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="planned",
    )

    note = models.TextField(blank=True, default="")
    location = models.CharField(max_length=255, blank=True, default="")
    meeting_link = models.URLField(blank=True, default="")

    # links into CRM
    lead = models.ForeignKey(
        "Lead",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="events",
    )
    opportunity = models.ForeignKey(
        "Opportunity",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="events",
    )
    customer = models.ForeignKey(
        "Customer",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="events",
    )
    production = models.ForeignKey(
        "ProductionOrder",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="events",
    )

    # assignment and reminders
    assigned_to_name = models.CharField(
        max_length=120,
        blank=True,
        null=True,
        help_text="Person who will follow up",
    )
    assigned_to_email = models.EmailField(
        blank=True,
        null=True,
        help_text="Their email for alerts",
    )
    attendees = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="calendar_events_invited",
    )
    external_attendees = models.TextField(
        blank=True,
        default="",
        help_text="External attendee emails, separated by comma or new line",
    )
    reminder_minutes_before = models.PositiveIntegerField(
        blank=True,
        null=True,
        help_text="Minutes before to send reminder",
    )
    reminder_sent = models.BooleanField(default=False)

    # AI summary
    ai_note = models.TextField(
        blank=True,
        null=True,
        default="",
    )

    # simple production link
    production_stage = models.CharField(
        max_length=40,
        choices=PRODUCTION_STAGE_CHOICES,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["start_datetime", "title"]

    def __str__(self):
        return self.title

    @property
    def is_overdue(self):
        if not self.start_datetime:
            return False
        if self.status == "done":
            return False
        return self.start_datetime < timezone.now()


class EventReminderDismissal(models.Model):
    event = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name="reminder_dismissals",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="dismissed_calendar_reminders",
    )
    dismissed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("event", "user")
        indexes = [
            models.Index(fields=["user", "event"]),
            models.Index(fields=["dismissed_at"]),
        ]

    def __str__(self):
        return f"{self.user_id} dismissed event {self.event_id}"


# ==============================
# AI AGENT AND LEAD AI MESSAGES
# ==============================

class AIAgent(models.Model):
    CATEGORY_CHOICES = [
        ("lead", "Lead helper"),
        ("opportunity", "Opportunity helper"),
        ("customer", "Customer helper"),
        ("production", "Production helper"),
        ("general", "General"),
    ]

    name = models.CharField(max_length=100)
    category = models.CharField(
        max_length=30,
        choices=CATEGORY_CHOICES,
        default="lead",
    )
    description = models.TextField(blank=True)
    system_prompt = models.TextField(
        blank=True,
        help_text="Main instructions this AI agent should follow.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class LeadAIMessage(models.Model):
    MESSAGE_TYPE_CHOICES = [
        ("summary", "Summary"),
        ("idea", "Idea"),
        ("follow_up", "Follow up"),
        ("note", "Note"),
    ]

    lead = models.ForeignKey(
        "Lead",
        on_delete=models.CASCADE,
        related_name="ai_messages",
    )
    agent = models.ForeignKey(
        "AIAgent",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messages",
    )
    message_type = models.CharField(
        max_length=20,
        choices=MESSAGE_TYPE_CHOICES,
        default="summary",
    )
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.lead.lead_id} - {self.message_type}"


## ==============================
# PRODUCTION ORDER
## ==============================

from decimal import Decimal
from django.db import models
from django.utils import timezone
from django.db.models.signals import post_save
from django.dispatch import receiver


class ProductionOrder(models.Model):
    """
    One work order for your factory.
    Stores basic info, style details, fabric, cost and remake.
    """

    # choices
    ORDER_TYPE_CHOICES = [
        ("fob", "FOB full service BD"),
        ("sewing_charge", "Sewing charge only"),
        ("canada_full", "Canada door to door"),
    ]

    PRODUCTION_ORDER_TYPE_CHOICES = [
        ("sampling", "Sample Development"),
        ("bulk", "Bulk Production"),
    ]

    OPERATIONAL_STATUS_CHOICES = [
        ("planning", "Not Started"),
        ("pattern", "Pattern"),
        ("sample_development", "Sample"),
        ("sample_sent", "Sample Sent"),
        ("approved", "Approved"),
        ("fabric_sourcing", "Fabric Sourcing"),
        ("cutting", "Cutting"),
        ("sewing", "Sewing"),
        ("printing", "Print / Embroidery"),
        ("finishing", "Finishing"),
        ("qc", "Quality Check"),
        ("packing", "Packing"),
        ("ready_to_ship", "Ready To Ship"),
        ("shipped", "Shipped"),
        ("on_hold", "On Hold"),
        ("cancelled", "Cancelled"),
    ]

    APPROVED_SNAPSHOT_FIELDS = (
        "source_quotation_id",
        "source_quick_costing_id",
        "quotation_number_snapshot",
        "client_name_snapshot",
        "brand_name_snapshot",
        "product_name_snapshot",
        "product_type_snapshot",
        "approved_currency",
        "approved_selling_price",
        "approved_total_value",
        "approved_costing_summary",
        "approved_price_locked_at",
    )

    ORDER_CODE_PREFIX = "PO"
    ORDER_CODE_GENERATION_ATTEMPTS = 8

    FACTORY_CHOICES = [
        ("bd", "Bangladesh"),
        ("ca", "Canada"),
    ]

    STATUS_CHOICES = [
        ("planning", "Planning"),
        ("in_progress", "In progress"),
        ("hold", "On hold"),
        ("done", "Done"),
        ("closed_won", "Closed Won"),
        ("closed_lost", "Closed Lost"),
    ]

    SIZE_GROUP_CHOICES = [
        ("men", "Men"),
        ("women", "Women"),
        ("kids", "Kids"),
        ("youth", "Youth"),
        ("unisex", "Unisex"),
    ]

    # basic order info
    title = models.CharField(max_length=200)
    order_code = models.CharField(max_length=50, unique=True, blank=True)

    lead = models.ForeignKey("Lead", on_delete=models.SET_NULL, null=True, blank=True)
    opportunity = models.ForeignKey(
        "Opportunity", on_delete=models.SET_NULL, null=True, blank=True
    )
    cost_sheet_active = models.ForeignKey(
        "CostSheet",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="production_orders",
    )
    costing_header = models.ForeignKey(
        "CostingHeader",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="production_orders",
    )
    source_quotation = models.OneToOneField(
        "CostingHeader",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="auto_production_order",
    )
    source_quick_costing = models.OneToOneField(
        "QuickCosting",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        editable=False,
        related_name="production_order",
    )
    quotation_number_snapshot = models.CharField(
        max_length=50,
        blank=True,
        default="",
        editable=False,
    )
    client_name_snapshot = models.CharField(
        max_length=200,
        blank=True,
        default="",
        editable=False,
    )
    brand_name_snapshot = models.CharField(
        max_length=200,
        blank=True,
        default="",
        editable=False,
    )
    product_name_snapshot = models.CharField(
        max_length=200,
        blank=True,
        default="",
        editable=False,
    )
    product_type_snapshot = models.CharField(
        max_length=100,
        blank=True,
        default="",
        editable=False,
    )
    approved_currency = models.CharField(
        max_length=10,
        choices=NEW_COSTING_CURRENCY_CHOICES,
        null=True,
        blank=True,
        editable=False,
    )
    approved_selling_price = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        null=True,
        blank=True,
        editable=False,
    )
    approved_total_value = models.DecimalField(
        max_digits=16,
        decimal_places=4,
        null=True,
        blank=True,
        editable=False,
    )
    approved_costing_summary = models.JSONField(
        blank=True,
        default=dict,
        editable=False,
    )
    approved_price_locked_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
    )
    customer = models.ForeignKey(
        "Customer",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="production_orders",
    )
    assigned_production_manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="managed_production_orders",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="created_production_orders",
    )
    product = models.ForeignKey(
        "Product", on_delete=models.SET_NULL, null=True, blank=True
    )
    fabrics = models.ManyToManyField(
        "Fabric",
        blank=True,
        related_name="production_orders",
    )
    accessories = models.ManyToManyField(
        "Accessory",
        blank=True,
        related_name="production_orders",
    )
    trims = models.ManyToManyField(
        "Trim",
        blank=True,
        related_name="production_orders",
    )
    threads = models.ManyToManyField(
        "ThreadOption",
        blank=True,
        related_name="production_orders",
    )

    factory_location = models.CharField(
        max_length=10, choices=FACTORY_CHOICES, default="bd"
    )
    order_type = models.CharField(
        max_length=20, choices=ORDER_TYPE_CHOICES, default="fob"
    )
    production_order_type = models.CharField(
        max_length=20,
        choices=PRODUCTION_ORDER_TYPE_CHOICES,
        default="bulk",
        db_index=True,
    )

    sample_deadline = models.DateField(null=True, blank=True)
    bulk_deadline = models.DateField(null=True, blank=True)

    qty_total = models.PositiveIntegerField(default=0)
    qty_reject = models.PositiveIntegerField(default=0)
    sewing_charge_per_piece_bdt = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    sewing_cost_per_piece_bdt = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    extra_local_cost_bdt = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
    )
    completed_quantity = models.PositiveIntegerField(null=True, blank=True)

    style_image = models.ImageField(
        upload_to="production_styles/", null=True, blank=True
    )

    # style and work order details
    style_name = models.CharField(max_length=200, blank=True)
    color_info = models.CharField(max_length=200, blank=True)
    size_group = models.CharField(
        max_length=20,
        choices=SIZE_GROUP_CHOICES,
        default="unisex",
    )
    size_ratio_note = models.TextField(blank=True)
    accessories_note = models.TextField(blank=True)
    packaging_note = models.TextField(blank=True)
    extra_order_note = models.TextField(blank=True)

    # free text notes
    notes = models.TextField(blank=True)
    ai_note = models.TextField(blank=True, null=True, default="")

    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="planning"
    )
    operational_status = models.CharField(
        max_length=32,
        choices=OPERATIONAL_STATUS_CHOICES,
        default="planning",
        blank=True,
        db_index=True,
    )
    is_archived = models.BooleanField(default=False, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="archived_production_orders",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # fabric in kg
    fabric_required_kg = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    fabric_received_kg = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    fabric_used_kg = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    fabric_leftover_kg = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    fabric_waste_kg = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    fabric_waste_percent = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )

    fabric_cost_per_kg_bdt = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    fabric_total_cost_bdt = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )

    # material cost taka
    material_thread_cost_bdt = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    material_zipper_cost_bdt = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    material_accessories_cost_bdt = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    material_label_cost_bdt = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    material_other_cost_bdt = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )

    material_total_cost_bdt = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )

    # production cost taka
    production_cutting_cost_bdt = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    production_sewing_cost_bdt = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    production_finishing_cost_bdt = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    production_packing_cost_bdt = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    production_overhead_cost_bdt = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    production_other_cost_bdt = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )

    production_total_cost_bdt = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )

    # remake
    remake_required = models.BooleanField(default=False)
    remake_qty = models.PositiveIntegerField(null=True, blank=True)
    remake_cost_bdt = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )

    # final cost
    actual_total_cost_bdt = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    actual_cost_per_piece_bdt = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True
    )

    class Meta:
        ordering = ["-created_at"]

    @classmethod
    def generate_order_code(cls):
        timestamp = timezone.now().strftime("%y%m%d%H%M%S")
        suffix = uuid.uuid4().hex[:6].upper()
        return f"{cls.ORDER_CODE_PREFIX}{timestamp}{suffix}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            existing_snapshot = (
                self.__class__.objects.filter(pk=self.pk)
                .values(*self.APPROVED_SNAPSHOT_FIELDS)
                .first()
            )
            if existing_snapshot and (
                existing_snapshot["source_quotation_id"]
                or existing_snapshot["approved_price_locked_at"]
            ):
                changed_fields = [
                    field_name
                    for field_name in self.APPROVED_SNAPSHOT_FIELDS
                    if existing_snapshot[field_name] != getattr(self, field_name)
                ]
                if changed_fields:
                    raise ValidationError(
                        "Approved quotation pricing and costing snapshots are locked."
                    )
            if self.order_code:
                self.order_code = self.order_code.strip()
            return super().save(*args, **kwargs)

        if self.order_type == "sewing_charge":
            source = self.source_quick_costing
            if not source:
                raise ValidationError(
                    "Bangladesh Local Sewing production requires an approved Quick Costing."
                )
            if source.effective_pricing_type != QuickCosting.PRICING_CMT:
                raise ValidationError("The source Quick Costing must use CMT / Sewing Only pricing.")
            if source.status != QuickCosting.STATUS_APPROVED:
                raise ValidationError("The source Quick Costing must be CEO approved.")

        supplied_order_code = (self.order_code or "").strip()
        if supplied_order_code:
            self.order_code = supplied_order_code
            return super().save(*args, **kwargs)

        last_error = None
        for _attempt in range(self.ORDER_CODE_GENERATION_ATTEMPTS):
            self.order_code = self.generate_order_code()
            if self.__class__.objects.filter(order_code=self.order_code).exists():
                continue
            try:
                with transaction.atomic():
                    return super().save(*args, **kwargs)
            except IntegrityError as exc:
                last_error = exc
                self.pk = None
                self._state.adding = True

        raise last_error or IntegrityError("Could not generate a unique production order code.")

    def __str__(self):
        if self.purchase_order_number:
            return f"{self.purchase_order_number} - {self.title}"
        return self.title

    @classmethod
    def format_purchase_order_number(cls, value, object_id=None):
        code = str(value or "").strip()
        if not code:
            return ""
        if code.upper().startswith("PO-"):
            return code

        index = 0
        while index < len(code) and code[index].isalpha():
            index += 1

        digit_start = index
        while index < len(code) and code[index].isdigit():
            index += 1

        timestamp_digits = code[digit_start:index]
        if len(timestamp_digits) >= 6:
            friendly = f"PO-{timestamp_digits[-6:]}"
        elif len(code) <= 10:
            friendly = code
        else:
            friendly = f"PO-{code[-6:]}"

        if object_id:
            return f"{friendly}-{int(object_id):03d}"
        return friendly

    @classmethod
    def identifier_search_query(cls, value, field_name="order_code"):
        query = str(value or "").strip()
        lookup = models.Q(**{f"{field_name}__icontains": query})
        normalized = query.upper()
        parts = normalized.split("-")
        if (
            len(parts) == 3
            and parts[0] == "PO"
            and len(parts[1]) == 6
            and parts[1].isdigit()
            and parts[2].isdigit()
        ):
            id_field = "pk"
            if "__" in field_name:
                id_field = f"{field_name.rsplit('__', 1)[0]}__pk"
            lookup |= (
                models.Q(**{f"{field_name}__icontains": parts[1]})
                & models.Q(**{id_field: int(parts[2])})
            )
        elif normalized.startswith("PO-") and len(normalized) > 3:
            lookup |= models.Q(**{f"{field_name}__icontains": normalized[3:]})
        return lookup

    @property
    def short_order_code(self):
        return self.format_purchase_order_number(self.order_code, self.pk)

    @property
    def purchase_order_number(self):
        return self.short_order_code

    @property
    def internal_order_id(self):
        return (self.order_code or "").strip() or str(self.pk or "")

    @property
    def percent_done(self):
        """
        Percent of stages marked as done.
        """
        try:
            stages = self.stages.all()
            total = stages.count()
            if total == 0:
                return 0
            done = stages.filter(status="done").count()
            return int((done / total) * 100)
        except (OperationalError, ProgrammingError):
            return 0

    @property
    def is_delayed(self):
        """
        True if order is delayed.
        Delayed if:
        - bulk deadline passed and status not done, or
        - any stage planned_end is in past and not done.
        """
        try:
            today = timezone.now().date()

            if self.status == "done":
                return False

            if self.bulk_deadline and today > self.bulk_deadline:
                return True

            late_stage = self.stages.filter(
                planned_end__lt=today
            ).exclude(status="done").exists()

            return late_stage
        except (OperationalError, ProgrammingError):
            return False


class ProductionOrderLine(models.Model):
    """
    Line items within a production order.
    Each line stores the product-specific work order details.
    """

    order = models.ForeignKey(
        ProductionOrder,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    line_no = models.PositiveIntegerField(default=1)

    style_name = models.CharField(max_length=200, blank=True)
    color_info = models.CharField(max_length=200, blank=True)
    quantity = models.PositiveIntegerField(null=True, blank=True)
    size_group = models.CharField(
        max_length=20,
        choices=ProductionOrder.SIZE_GROUP_CHOICES,
        default="unisex",
    )
    size_ratio_note = models.TextField(blank=True)
    accessories_note = models.TextField(blank=True)
    packaging_note = models.TextField(blank=True)
    extra_order_note = models.TextField(blank=True)

    class Meta:
        ordering = ["line_no", "id"]

    def __str__(self):
        label = self.style_name or "Line"
        return f"{self.order.purchase_order_number or self.order_id} - {label}"


class ProductionOrderMaterial(models.Model):
    STATUS_CHOICES = [
        ("reserved", "Reserved"),
        ("partial", "Partially Consumed"),
        ("consumed", "Consumed"),
        ("cancelled", "Cancelled"),
    ]

    order = models.ForeignKey(
        "ProductionOrder",
        on_delete=models.CASCADE,
        related_name="materials",
    )
    inventory_item = models.ForeignKey(
        "InventoryItem",
        on_delete=models.CASCADE,
        related_name="production_materials",
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    allocated_quantity = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    consumed_quantity = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    damaged_quantity = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    unit_type = models.CharField(max_length=50, blank=True, default="")
    notes = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="reserved")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.unit_type and self.inventory_item:
            self.unit_type = self.inventory_item.unit_type or ""
        if not self.allocated_quantity and self.quantity:
            self.allocated_quantity = self.quantity
        remaining = self.remaining_quantity
        if self.allocated_quantity and remaining <= 0:
            self.status = "consumed"
        elif self.consumed_quantity or self.damaged_quantity:
            self.status = "partial"
        super().save(*args, **kwargs)

    @property
    def remaining_quantity(self):
        allocated = self.allocated_quantity or self.quantity or Decimal("0")
        consumed = self.consumed_quantity or Decimal("0")
        damaged = self.damaged_quantity or Decimal("0")
        return allocated - consumed - damaged

    def __str__(self):
        return f"{self.order.purchase_order_number} - {self.inventory_item.name}"


from decimal import Decimal
from django.db import models
from django.utils import timezone


class Shipment(models.Model):
    CARRIER_CHOICES = [
        ("fedex", "FedEx"),
        ("dhl", "DHL"),
        ("ups", "UPS"),
        ("other", "Other"),
    ]

    STATUS_CHOICES = [
        ("planned", "Planned"),
        ("booked", "Booked"),
        ("shipped", "Shipped"),
        ("out_for_delivery", "Out for delivery"),
        ("delivered", "Delivered"),
        ("cancelled", "Cancelled"),
    ]

    # links
    order = models.ForeignKey(
        "ProductionOrder",
        on_delete=models.CASCADE,
        related_name="shipments",
        null=True,
        blank=True,
    )
    opportunity = models.ForeignKey(
        "Opportunity",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shipments",
    )
    customer = models.ForeignKey(
        "Customer",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shipments",
    )

    # core info
    carrier = models.CharField(
        max_length=20,
        choices=CARRIER_CHOICES,
        default="dhl",
    )
    tracking_number = models.CharField(max_length=100, blank=True)

    ship_date = models.DateField(null=True, blank=True)
    shipment_type = models.CharField(
        max_length=20,
        choices=[
            ("sample", "Sample"),
            ("bulk", "Bulk"),
            ("remake", "Remake"),
        ],
        default="bulk",
    )

    # weight and box count
    box_count = models.PositiveIntegerField(null=True, blank=True)
    total_weight_kg = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )

    # cost fields
    cost_bdt = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Bangladesh team enters cost in taka",
    )
    rate_bdt_per_cad = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        default=Decimal("90.00"),
        help_text="How many taka for one Canadian dollar",
    )
    cost_cad = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="planned",
    )

    last_tracking_status = models.CharField(max_length=200, blank=True)
    last_tracking_check = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    last_notified_status = models.CharField(max_length=30, blank=True, default="")

    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-ship_date", "-created_at"]

    def __str__(self):
        base = self.tracking_number or f"Shipment {self.pk}"
        if self.order and self.order.purchase_order_number:
            return f"{self.order.purchase_order_number} - {base}"
        return base

    def update_cost_cad(self):
        """
        Use taka and rate to get Canadian dollar amount.
        """
        if self.cost_bdt is None:
            self.cost_cad = None
            return

        try:
            rate = Decimal(str(self.rate_bdt_per_cad)) if self.rate_bdt_per_cad is not None else None
        except Exception:
            rate = None

        if not rate or rate <= 0:
            self.cost_cad = None
            return

        try:
            self.cost_cad = convert_currency(
                self.cost_bdt,
                "BDT",
                "CAD",
                bdt_per_cad=rate,
            )
        except CurrencyConversionError:
            self.cost_cad = None

    @property
    def tracking_url(self):
        if not self.tracking_number:
            return ""
        if self.carrier == "fedex":
            return f"https://www.fedex.com/fedextrack/?tracknumbers={self.tracking_number}"
        if self.carrier == "dhl":
            return f"https://www.dhl.com/global-en/home/tracking.html?tracking-id={self.tracking_number}"
        if self.carrier == "ups":
            return f"https://www.ups.com/track?tracknum={self.tracking_number}"
        return ""

    def save(self, *args, **kwargs):
        # copy links from order if missing
        if self.order:
            if not self.opportunity and self.order.opportunity:
                self.opportunity = self.order.opportunity
            if not self.customer and self.order.customer:
                self.customer = self.order.customer

        # update CAD value
        self.update_cost_cad()

        super().save(*args, **kwargs)

        if self.order_id:
            from .services.production_operational_status import sync_operational_status

            sync_operational_status(self.order)


class OrderLifecycle(models.Model):
    STATUS_CHOICES = [
        ("lead", "Lead"),
        ("costing", "Costing"),
        ("quotation", "Quotation"),
        ("invoice", "Invoice"),
        ("production", "Production"),
        ("shipping", "Shipping"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ]

    customer = models.ForeignKey(
        "Customer",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_lifecycles",
    )
    lead = models.ForeignKey(
        "Lead",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_lifecycles",
    )
    opportunity = models.ForeignKey(
        "Opportunity",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_lifecycles",
    )
    costing = models.ForeignKey(
        "CostingHeader",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_lifecycles_as_costing",
    )
    quotation = models.ForeignKey(
        "CostingHeader",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_lifecycles_as_quotation",
    )
    invoice = models.ForeignKey(
        "Invoice",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_lifecycles",
    )
    production_order = models.ForeignKey(
        "ProductionOrder",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_lifecycles",
    )
    shipping_record = models.ForeignKey(
        "Shipment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_lifecycles",
    )

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="lead", db_index=True)
    estimated_revenue = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    estimated_cost = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    estimated_profit = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    estimated_margin = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal("0"))

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_order_lifecycles",
    )
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-updated_at", "-id"]
        indexes = [
            models.Index(fields=["status", "updated_at"]),
            models.Index(fields=["customer", "status"]),
            models.Index(fields=["opportunity", "status"]),
        ]

    def __str__(self):
        if self.invoice_id and self.invoice:
            return f"Lifecycle for {self.invoice.invoice_number}"
        if self.production_order_id and self.production_order:
            return f"Lifecycle for {self.production_order.purchase_order_number or self.production_order_id}"
        if self.quotation_id and self.quotation:
            return f"Lifecycle for {self.quotation.quotation_number or 'COST-' + str(self.quotation_id)}"
        return f"Order Lifecycle {self.pk or ''}".strip()


class AutomationRule(models.Model):
    RULE_TYPE_CHOICES = [
        ("invoice", "Invoice"),
        ("production", "Production"),
        ("inventory", "Inventory"),
        ("lifecycle", "Lifecycle"),
        ("general", "General"),
    ]

    rule_name = models.CharField(max_length=160, unique=True)
    rule_type = models.CharField(max_length=30, choices=RULE_TYPE_CHOICES, default="general", db_index=True)
    enabled = models.BooleanField(default=True, db_index=True)
    trigger = models.CharField(max_length=160, blank=True, default="")
    condition = models.JSONField(default=dict, blank=True)
    action = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_automation_rules",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["rule_type", "rule_name"]
        indexes = [
            models.Index(fields=["rule_type", "enabled"]),
        ]

    def __str__(self):
        return self.rule_name


class AutomationNotification(models.Model):
    TYPE_CHOICES = [
        ("ceo_approval", "CEO Approval Required"),
        ("ceo_approved", "CEO Approved Quotation"),
        ("ceo_rejected", "CEO Rejected Quotation"),
        ("production_created", "Production Order Created"),
        ("sample_due", "Sample Due"),
        ("production_due", "Production Overdue"),
        ("shipment_due", "Shipment Due Today"),
        ("shipment_delayed", "Shipment Delayed"),
        ("invoice_overdue", "Invoice Overdue"),
        ("task_assigned", "Task Assigned"),
        ("task_completed", "Task Completed"),
        ("comment_added", "Comment Added"),
        ("mention", "Mention"),
        ("general", "General"),
    ]
    PRIORITY_CHOICES = [
        ("critical", "Critical"),
        ("high", "High"),
        ("normal", "Normal"),
        ("information", "Information"),
    ]

    rule = models.ForeignKey(
        "AutomationRule",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications",
    )
    source_key = models.CharField(max_length=220, unique=True, db_index=True)
    rule_type = models.CharField(max_length=30, choices=AutomationRule.RULE_TYPE_CHOICES, default="general", db_index=True)
    notification_type = models.CharField(max_length=30, choices=TYPE_CHOICES, default="general", db_index=True)
    title = models.CharField(max_length=220)
    message = models.TextField(blank=True, default="")
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default="normal", db_index=True)
    is_read = models.BooleanField(default=False, db_index=True)
    is_resolved = models.BooleanField(default=False, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    record_content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.SET_NULL)
    record_object_id = models.PositiveIntegerField(null=True, blank=True)
    record = GenericForeignKey("record_content_type", "record_object_id")
    record_label = models.CharField(max_length=220, blank=True, default="")
    target_url = models.CharField(max_length=300, blank=True, default="")
    assigned_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="crm_notifications",
    )
    assigned_role = models.CharField(max_length=40, blank=True, default="", db_index=True)
    due_date = models.DateField(null=True, blank=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["is_read", "-updated_at", "-id"]
        indexes = [
            models.Index(fields=["rule_type", "is_resolved", "is_read"]),
            models.Index(fields=["priority", "is_resolved"]),
            models.Index(fields=["record_content_type", "record_object_id"]),
            models.Index(fields=["assigned_user", "is_resolved", "is_read"]),
            models.Index(fields=["assigned_role", "is_resolved", "is_read"]),
        ]

    def __str__(self):
        return self.title

    @property
    def read_status_label(self):
        return "Read" if self.is_read else "Unread"


class AutomationTask(models.Model):
    STATUS_CHOICES = [
        ("open", "Open"),
        ("in_progress", "In Progress"),
        ("done", "Done"),
        ("cancelled", "Cancelled"),
    ]
    PRIORITY_CHOICES = [
        ("low", "Low"),
        ("normal", "Normal"),
        ("high", "High"),
        ("urgent", "Urgent"),
    ]

    rule = models.ForeignKey(
        "AutomationRule",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tasks",
    )
    notification = models.ForeignKey(
        "AutomationNotification",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tasks",
    )
    source_key = models.CharField(max_length=220, unique=True, db_index=True)
    title = models.CharField(max_length=220)
    description = models.TextField(blank=True, default="")
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default="normal", db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="open", db_index=True)
    due_date = models.DateField(null=True, blank=True)

    record_content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.SET_NULL)
    record_object_id = models.PositiveIntegerField(null=True, blank=True)
    record = GenericForeignKey("record_content_type", "record_object_id")
    record_label = models.CharField(max_length=220, blank=True, default="")
    target_url = models.CharField(max_length=300, blank=True, default="")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_automation_tasks",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["status", "due_date", "-updated_at"]
        indexes = [
            models.Index(fields=["status", "priority"]),
            models.Index(fields=["record_content_type", "record_object_id"]),
        ]

    def __str__(self):
        return self.title


class Invoice(models.Model):
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("sent", "Sent to client"),
        ("partial", "Partly paid"),
        ("paid", "Paid"),
        ("cancelled", "Cancelled"),
    ]
    is_archived = models.BooleanField(default=False, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="archived_invoices",
    )
    INVOICE_MARKET_CHOICES = [
        ("north_america", "North America"),
        ("bangladesh", "Bangladesh"),
    ]
    INVOICE_TYPE_CHOICES = [
        ("sample", "Sample"),
        ("bulk", "Bulk Production"),
        ("sewing_charge", "Sewing Charge"),
    ]

    order = models.ForeignKey(
        "ProductionOrder",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices",
    )
    costing_header = models.ForeignKey(
        "CostingHeader",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices",
    )
    quick_costing = models.ForeignKey(
        "QuickCosting",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices",
    )
    customer = models.ForeignKey(
        "Customer",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    opportunity = models.ForeignKey(
        "Opportunity",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices",
    )

    invoice_number = models.CharField(max_length=50, unique=True)
    issue_date = models.DateField(default=timezone.now)
    invoice_date = models.DateField(null=True, blank=True, db_index=True)
    due_date = models.DateField(null=True, blank=True)

    currency = models.CharField(
        max_length=10,
        default="USD",
        choices=[
            ("USD", "USD"),
            ("CAD", "CAD"),
            ("BDT", "BDT"),
        ],
    )

    invoice_region = models.CharField(
        max_length=2,
        choices=[("CA", "Canada"), ("BD", "Bangladesh")],
        default="",
        blank=True,
    )
    invoice_market = models.CharField(
        max_length=20,
        choices=INVOICE_MARKET_CHOICES,
        default="north_america",
        db_index=True,
    )
    invoice_type = models.CharField(
        max_length=20,
        choices=INVOICE_TYPE_CHOICES,
        default="bulk",
        db_index=True,
    )
    invoice_status = models.CharField(
        max_length=12,
        choices=[("DRAFT", "Draft"), ("APPROVED", "Approved")],
        default="DRAFT",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_invoices",
    )

    deposit_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("70.00"),
    )
    deposit_percentage = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("50.00"),
        blank=True,
    )
    terms_override = models.TextField(blank=True, default="")

    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    shipping_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    discount_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))

    paid_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="draft",
    )

    notes = models.TextField(blank=True)
    sewing_charge = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    other_internal_cost = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    internal_cost_note = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-issue_date", "-created_at"]

    def __str__(self):
        return f"Invoice {self.invoice_number}"

    def save(self, *args, **kwargs):
        if self._state.adding and self.order_id:
            order = self.order
            if order.order_type == "sewing_charge" and order.factory_location == "bd":
                source = order.source_quick_costing
                if not source or not source.approved_at:
                    raise ValidationError(
                        "Bangladesh Local Sewing invoices require a CEO-approved Quick Costing."
                    )
                if self.quick_costing_id != source.pk:
                    raise ValidationError(
                        "The invoice must retain the Production Order's approved Quick Costing."
                    )
        result = super().save(*args, **kwargs)
        try:
            from crm.services.opportunity_payment_stage import sync_opportunity_stage_from_invoice

            sync_opportunity_stage_from_invoice(self)
        except Exception:
            pass
        return result

    @property
    def balance(self):
        return (self.total_amount or Decimal("0")) - (self.paid_amount or Decimal("0"))

    @property
    def effective_invoice_date(self):
        if self.invoice_date:
            return self.invoice_date
        if self.created_at:
            return self.created_at.date()
        return self.issue_date

    @property
    def is_historical_entry(self):
        return bool(self.invoice_date and self.created_at and self.invoice_date < self.created_at.date())

    @property
    def deposit_amount(self):
        total = self._decimal_or_zero(self.total_amount)
        percentage = self._decimal_or_zero(getattr(self, "deposit_percentage", Decimal("0")))
        if total <= 0 or percentage <= 0:
            return Decimal("0")
        return (total * percentage / Decimal("100")).quantize(Decimal("0.01"))

    @property
    def deposit_balance_due(self):
        balance = self._decimal_or_zero(self.total_amount) - self.deposit_amount
        if balance < 0:
            return Decimal("0")
        return balance.quantize(Decimal("0.01"))

    @staticmethod
    def _decimal_or_zero(value):
        if value in ("", None):
            return Decimal("0")
        if isinstance(value, Decimal):
            return value
        try:
            return Decimal(str(value))
        except Exception:
            return Decimal("0")

    @property
    def total_internal_cost(self):
        return self._decimal_or_zero(self.sewing_charge) + self._decimal_or_zero(self.other_internal_cost)

    @property
    def estimated_gross_profit(self):
        return self._decimal_or_zero(self.total_amount) - self.total_internal_cost

    @property
    def estimated_profit_margin(self):
        total = self._decimal_or_zero(self.total_amount)
        if total <= 0:
            return Decimal("0")
        return (self.estimated_gross_profit / total) * Decimal("100")

    @property
    def payment_status_key(self):
        total = self.total_amount or Decimal("0")
        paid = self.paid_amount or Decimal("0")
        if paid <= 0:
            return "unpaid"
        if total > 0 and paid > total:
            return "overpaid"
        if total > 0 and paid >= total:
            return "paid"
        return "partial"

    @property
    def payment_status_label(self):
        return {
            "unpaid": "Unpaid",
            "partial": "Partially paid",
            "paid": "Paid",
            "overpaid": "Overpaid",
        }.get(self.payment_status_key, "Unpaid")


class InvoiceSettings(models.Model):
    company_name = models.CharField(max_length=200, blank=True, default="Iconic Apparel House Inc.")
    company_email = models.EmailField(blank=True, default="info@iconicapparelhouse.com")
    company_phone = models.CharField(max_length=80, blank=True, default="604-500-6009")
    website = models.CharField(max_length=160, blank=True, default="iconicapparelhouse.com")
    slogan = models.CharField(max_length=255, blank=True, default="From Concept to Creation")
    invoice_footer_note = models.CharField(
        max_length=255,
        blank=True,
        default="Iconic Apparel House Inc. Your Trusted Manufacturing Partner for Growth.",
    )
    authorized_by_name = models.CharField(max_length=160, blank=True, default="")
    authorized_by_title = models.CharField(max_length=160, blank=True, default="")

    paypal_email_or_id = models.CharField(max_length=160, blank=True, default="iconicapparelhouse")
    paypal_qr_image = models.ImageField(upload_to="invoice_settings/qr/", blank=True, null=True)
    etransfer_email = models.EmailField(blank=True, default="accounts@iconicapparelhouse.com")
    canada_bank_name = models.CharField(max_length=160, blank=True, default="")
    canada_account_name = models.CharField(max_length=160, blank=True, default="")
    canada_account_number = models.CharField(max_length=120, blank=True, default="")
    canada_transit_number = models.CharField(max_length=80, blank=True, default="")
    canada_institution_number = models.CharField(max_length=80, blank=True, default="")
    canada_wire_note = models.TextField(blank=True, default="")
    canada_payment_terms = models.TextField(blank=True, default="")

    bd_bank_name = models.CharField(max_length=160, blank=True, default="")
    bd_account_name = models.CharField(max_length=160, blank=True, default="")
    bd_account_number = models.CharField(max_length=120, blank=True, default="")
    bd_branch = models.CharField(max_length=160, blank=True, default="")
    bd_routing_number = models.CharField(max_length=80, blank=True, default="")
    bd_swift = models.CharField(max_length=80, blank=True, default="")
    bkash_number = models.CharField(max_length=80, blank=True, default="")
    bkash_qr_image = models.ImageField(upload_to="invoice_settings/qr/", blank=True, null=True)
    nagad_number = models.CharField(max_length=80, blank=True, default="")
    nagad_qr_image = models.ImageField(upload_to="invoice_settings/qr/", blank=True, null=True)
    rocket_number = models.CharField(max_length=80, blank=True, default="")
    rocket_qr_image = models.ImageField(upload_to="invoice_settings/qr/", blank=True, null=True)
    bd_payment_terms = models.TextField(blank=True, default="")

    default_sample_deposit_percentage = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("100.00"))
    default_bulk_deposit_percentage = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("50.00"))
    default_bd_sewing_deposit_percentage = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("50.00"))
    default_currency_na = models.CharField(max_length=10, blank=True, default="CAD")
    default_currency_bd = models.CharField(max_length=10, blank=True, default="BDT")
    default_tax_note = models.CharField(max_length=255, blank=True, default="")
    terms_and_conditions_na = models.TextField(blank=True, default="")
    terms_and_conditions_bd = models.TextField(blank=True, default="")

    is_active = models.BooleanField(default=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="updated_invoice_settings",
    )

    class Meta:
        ordering = ["-is_active", "-updated_at", "-id"]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_active:
            InvoiceSettings.objects.exclude(pk=self.pk).filter(is_active=True).update(is_active=False)

    @classmethod
    def active(cls):
        try:
            return cls.objects.filter(is_active=True).order_by("-updated_at", "-id").first()
        except Exception:
            return None

    def __str__(self):
        return self.company_name or "Invoice Settings"


class InvoiceAudit(models.Model):
    ACTION_CHOICES = [
        ("approved", "Approved"),
    ]

    invoice = models.ForeignKey(
        "Invoice",
        on_delete=models.CASCADE,
        related_name="audits",
    )
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    changed_at = models.DateTimeField(auto_now_add=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invoice_audits",
    )
    note = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["-changed_at", "-id"]

    def __str__(self):
        return f"{self.invoice_id} {self.action}"


class InvoicePayment(models.Model):
    METHOD_CHOICES = [
        ("bank", "Bank transfer"),
        ("cash", "Cash"),
        ("cheque", "Cheque"),
        ("card", "Card"),
        ("mobile", "Mobile payment"),
        ("other", "Other"),
    ]

    SIDE_CHOICES = [
        ("CA", "Canada"),
        ("BD", "Bangladesh"),
    ]

    invoice = models.ForeignKey(
        "Invoice",
        on_delete=models.CASCADE,
        related_name="payments",
    )
    accounting_entry = models.ForeignKey(
        "AccountingEntry",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invoice_payments",
    )
    production_order = models.ForeignKey(
        "ProductionOrder",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invoice_payments",
    )

    payment_date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(
        max_length=10,
        default="CAD",
        choices=[
            ("USD", "USD"),
            ("CAD", "CAD"),
            ("BDT", "BDT"),
        ],
    )
    side = models.CharField(max_length=2, choices=SIDE_CHOICES, default="CA")
    payment_method = models.CharField(max_length=20, choices=METHOD_CHOICES, default="bank")

    rate_to_cad = models.DecimalField(max_digits=14, decimal_places=6, default=Decimal("0"))
    rate_to_bdt = models.DecimalField(max_digits=14, decimal_places=6, default=Decimal("0"))
    amount_cad = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    amount_bdt = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))

    notes = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_invoice_payments",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-payment_date", "-id"]

    def save(self, *args, **kwargs):
        amount = self.amount or Decimal("0")
        currency = (self.currency or "").upper().strip()

        if currency == "CAD":
            self.rate_to_cad = Decimal("1")
        if currency == "BDT":
            self.rate_to_bdt = Decimal("1")

        rate_to_cad = self.rate_to_cad or Decimal("0")
        rate_to_bdt = self.rate_to_bdt or Decimal("0")
        try:
            self.amount_cad = convert_currency(
                amount,
                currency,
                "CAD",
                stored_rate_to_cad=rate_to_cad,
                stored_rate_to_bdt=rate_to_bdt,
            )
        except CurrencyConversionError as exc:
            raise ValidationError({"rate_to_cad": str(exc)}) from exc
        try:
            self.amount_bdt = convert_currency(
                amount,
                currency,
                "BDT",
                stored_rate_to_cad=rate_to_cad,
                stored_rate_to_bdt=rate_to_bdt,
            )
        except CurrencyConversionError:
            self.amount_bdt = Decimal("0")

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.invoice.invoice_number} payment {self.amount} {self.currency}"


class SalesCommission(models.Model):
    """Invoice-backed salesperson commission; ownership resolves through the invoice Lead."""

    APPROVAL_PENDING = "pending"
    APPROVAL_APPROVED = "approved"
    APPROVAL_REJECTED = "rejected"
    APPROVAL_CHOICES = [
        (APPROVAL_PENDING, "Pending"),
        (APPROVAL_APPROVED, "Approved"),
        (APPROVAL_REJECTED, "Rejected"),
    ]
    PAID_UNPAID = "unpaid"
    PAID_PAID = "paid"
    PAID_CHOICES = [(PAID_UNPAID, "Unpaid"), (PAID_PAID, "Paid")]

    invoice = models.ForeignKey("Invoice", on_delete=models.CASCADE, related_name="sales_commissions")
    eligible_amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, choices=NEW_COSTING_CURRENCY_CHOICES)
    commission_percent = models.DecimalField(max_digits=6, decimal_places=2)
    commission_amount = models.DecimalField(max_digits=14, decimal_places=2, editable=False)
    approval_status = models.CharField(max_length=12, choices=APPROVAL_CHOICES, default=APPROVAL_PENDING)
    paid_status = models.CharField(max_length=10, choices=PAID_CHOICES, default=PAID_UNPAID)
    paid_date = models.DateField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_sales_commissions",
    )
    payment_reference = models.CharField(max_length=120, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["approval_status", "paid_status"], name="crm_salesco_approva_19e0df_idx"),
            models.Index(fields=["currency"], name="crm_salesco_currenc_94f5fd_idx"),
        ]

    def clean(self):
        super().clean()
        if self.eligible_amount is not None and self.eligible_amount < 0:
            raise ValidationError({"eligible_amount": "Eligible amount cannot be negative."})
        if self.commission_percent is not None and not Decimal("0") <= self.commission_percent <= Decimal("100"):
            raise ValidationError({"commission_percent": "Commission percent must be between 0 and 100."})
        if self.invoice_id and self.currency != self.invoice.currency:
            raise ValidationError({"currency": "Commission currency must match the invoice currency."})
        if self.paid_status == self.PAID_PAID and not self.paid_date:
            raise ValidationError({"paid_date": "Paid date is required when commission is paid."})

    def save(self, *args, **kwargs):
        self.commission_amount = (
            (self.eligible_amount or Decimal("0"))
            * (self.commission_percent or Decimal("0"))
            / Decimal("100")
        ).quantize(Decimal("0.01"))
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.invoice.invoice_number} commission {self.commission_amount} {self.currency}"


## ==============================
# PRODUCTION ATTACHMENT
## ==============================

class ProductionProgressPhoto(models.Model):
    STAGE_CHOICES = [
        ("cutting", "Cutting"),
        ("printing", "Printing"),
        ("sewing", "Sewing"),
        ("qc", "QC"),
        ("packing", "Packing"),
    ]
    ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

    order = models.ForeignKey(
        "ProductionOrder",
        on_delete=models.CASCADE,
        related_name="progress_photos",
    )
    stage = models.CharField(max_length=20, choices=STAGE_CHOICES, db_index=True)
    image = models.ImageField(upload_to="production_progress/%Y/%m/")
    caption = models.CharField(max_length=160, blank=True, default="")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="production_progress_photos",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["stage", "-uploaded_at", "-id"]
        indexes = [
            models.Index(fields=["order", "stage"]),
            models.Index(fields=["uploaded_at"]),
        ]

    def clean(self):
        super().clean()
        if self.image:
            extension = os.path.splitext(self.image.name or "")[1].lower()
            if extension not in self.ALLOWED_EXTENSIONS:
                raise ValidationError({"image": "Upload a JPG, PNG, or WEBP image."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_stage_display()} photo for {self.order}"


class ProductionOrderAttachment(models.Model):
    order = models.ForeignKey(
        "ProductionOrder",
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    line = models.ForeignKey(
        "ProductionOrderLine",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attachments",
    )
    file = models.FileField(upload_to="production_attachments/")
    name = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name or self.file.name


## ==============================
# PRODUCTION STAGE
## ==============================

class ProductionStage(models.Model):
    """
    One stage of the production order time line.
    For example cutting, sewing, packing and so on.
    """

    STAGE_CHOICES = [
        ("development", "Development"),
        ("sampling", "Sampling"),
        ("cutting", "Cutting"),
        ("sewing", "Sewing"),
        ("ironing", "Ironing"),
        ("qc", "QC"),
        ("finishing", "Finishing"),
        ("packing", "Packing"),
        ("shipping", "Shipping"),
    ]

    STATUS_CHOICES = [
        ("planned", "Planned"),
        ("in_progress", "In progress"),
        ("hold", "On hold"),
        ("done", "Done"),
        ("delay", "Delayed"),
    ]

    order = models.ForeignKey(
        "ProductionOrder",
        on_delete=models.CASCADE,
        related_name="stages",
    )

    stage_key = models.CharField(
        max_length=20,
        choices=STAGE_CHOICES,
    )

    display_name = models.CharField(
        max_length=50,
        blank=True,
        help_text="Optional nice label for this stage",
    )

    planned_start = models.DateField(null=True, blank=True)
    planned_end = models.DateField(null=True, blank=True)
    actual_start = models.DateField(null=True, blank=True)
    actual_end = models.DateField(null=True, blank=True)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="planned",
    )

    color_tag = models.CharField(
        max_length=20,
        blank=True,
        help_text="CSS color name or code for stage card",
    )

    notes = models.TextField(blank=True)

    ai_note = models.TextField(
        blank=True,
        null=True,
        default="",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "planned_start", "stage_key"]

    def __str__(self):
        return f"{self.order.purchase_order_number} - {self.get_stage_key_display()}"

    @property
    def is_late(self):
        if self.status == "done":
            return False
        if self.planned_end and timezone.now().date() > self.planned_end:
            return True
        return False

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

        if self.order_id:
            from .services.production_operational_status import sync_operational_status

            sync_operational_status(self.order)


# default stages for every new production order
DEFAULT_PRODUCTION_STAGES = [
    ("development", "Development", "#4caf50"),
    ("sampling", "Sampling", "#66bb6a"),
    ("cutting", "Cutting", "#ffee58"),
    ("sewing", "Sewing", "#29b6f6"),
    ("ironing", "Ironing", "#8e24aa"),
    ("qc", "QC", "#ff7043"),
    ("finishing", "Finishing", "#26a69a"),
    ("packing", "Packing", "#ab47bc"),
    ("shipping", "Shipping", "#ffa726"),
]


@receiver(post_save, sender=ProductionOrder)
def create_default_stages(sender, instance, created, **kwargs):
    """
    When a new production order is created, add the standard stages.
    """
    if not created:
        return

    if instance.stages.exists():
        return

    for key, label, color in DEFAULT_PRODUCTION_STAGES:
        ProductionStage.objects.create(
            order=instance,
            stage_key=key,
            display_name=label,
            color_tag=color,
        )

from decimal import Decimal
from django.db import models
from django.conf import settings
from django.utils import timezone

# other models above ...

class ExchangeRate(models.Model):
    cad_to_bdt = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        default=Decimal("0")
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"1 CAD = {self.cad_to_bdt} BDT"

from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import models
from django.utils import timezone



from django.conf import settings
from django.db import models


class AccountingAttachment(models.Model):
    entry = models.ForeignKey(
        "crm.AccountingEntry",
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    file = models.FileField(upload_to="accounting/")
    original_name = models.CharField(max_length=255, blank=True, default="")
    uploaded_by = models.ForeignKey(
        "auth.User",
        null=True, blank=True,
        on_delete=models.SET_NULL,
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    note = models.TextField(blank=True, default="")


    class Meta:
        ordering = ["-uploaded_at", "-id"]

    def __str__(self):
        return self.original_name or f"Attachment {self.id}"

    def save(self, *args, **kwargs):
        if not self.original_name and self.file:
            self.original_name = (getattr(self.file, "name", "") or "")[:255]
        super().save(*args, **kwargs)


class LibraryAttachment(models.Model):
    CATEGORY_CHOICES = [
        ("general", "General"),
        ("catalog", "Catalog"),
        ("product", "Product"),
        ("fabric", "Fabric"),
        ("accessory", "Accessory"),
        ("trim", "Trim"),
        ("thread", "Thread"),
        ("factory", "Factory"),
    ]

    title = models.CharField(max_length=200, blank=True, default="")
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default="general")
    file = models.FileField(upload_to="library/")
    original_name = models.CharField(max_length=255, blank=True, default="")
    note = models.TextField(blank=True, default="")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at", "-id"]

    def __str__(self):
        label = self.title or self.original_name
        return label or f"Library attachment {self.id}"

    def save(self, *args, **kwargs):
        if not self.original_name and self.file:
            self.original_name = (getattr(self.file, "name", "") or "")[:255]
        super().save(*args, **kwargs)

from django.conf import settings
from django.db import models
from django.utils import timezone


class AccountingEntryAudit(models.Model):
    ACTION_CHOICES = [
        ("CREATE", "Create"),
        ("UPDATE", "Update"),
        ("DELETE", "Delete"),
        ("FILES", "Files"),
    ]

    entry = models.ForeignKey(
        "crm.AccountingEntry",   # safer than direct reference
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="entry_audits",          # keep this, your views expect it
        related_query_name="entry_audit",
    )

    action = models.CharField(max_length=10, choices=ACTION_CHOICES)
    changed_at = models.DateTimeField(default=timezone.now, db_index=True)

    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="accounting_entry_audits",
    )

    before_data = models.JSONField(null=True, blank=True)
    after_data = models.JSONField(null=True, blank=True)

    note = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["-changed_at", "-id"]

    def __str__(self):
        return f"{self.entry_id} {self.action} {self.changed_at}"


from django.db import models
from django.conf import settings


class AccountingDocument(models.Model):
    SIDE_CHOICES = [
        ("CA", "CA"),
        ("BD", "BD"),
    ]

    DOC_TYPE_CHOICES = [
        ("INVOICE", "Invoice"),
        ("RECEIPT", "Receipt"),
        ("BILL", "Bill"),
        ("OTHER", "Other"),
    ]

    side = models.CharField(max_length=2, choices=SIDE_CHOICES, default="CA")
    doc_type = models.CharField(max_length=20, choices=DOC_TYPE_CHOICES, default="INVOICE")

    title = models.CharField(max_length=200, blank=True, default="")
    vendor = models.CharField(max_length=200, blank=True, default="")
    amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    doc_date = models.DateField(null=True, blank=True)

    file = models.FileField(upload_to="accounting_docs/")
    original_name = models.CharField(max_length=255, blank=True, default="")

    note = models.TextField(blank=True, default="")

    linked_entry = models.ForeignKey(
        "crm.AccountingEntry",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="documents",
    )

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at", "-id"]

    def save(self, *args, **kwargs):
        if not self.original_name and self.file:
            self.original_name = (getattr(self.file, "name", "") or "")[:255]
        super().save(*args, **kwargs)

    def __str__(self):
        base = self.title or self.original_name or f"Document {self.id}"
        return base




# ------------------------------------------------
# Bangladesh staff
# ------------------------------------------------
from decimal import Decimal
from django.db import models
from django.utils import timezone


class BDStaff(models.Model):
    """
    Bangladesh factory staff
    Basic info and base salary
    """
    name = models.CharField(max_length=120)
    role = models.CharField(
        max_length=120,
        blank=True,
        help_text="Example operator, helper, cutting master, merchandiser",
    )
    is_active = models.BooleanField(default=True)

    base_salary_bdt = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Monthly base salary in BDT",
    )

    join_date = models.DateField(null=True, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class BDStaffMonth(models.Model):
    """
    Salary view for one staff in one month.
    We track overtime and final pay here.
    """
    staff = models.ForeignKey(
        BDStaff,
        on_delete=models.CASCADE,
        related_name="month_rows",
    )
    year = models.IntegerField()
    month = models.IntegerField()

    base_salary_bdt = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    overtime_hours = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    overtime_rate_bdt = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Extra pay per overtime hour",
    )

    overtime_total_bdt = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    bonus_bdt = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Any extra bonus for this month",
    )

    deduction_bdt = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Any deduction like late or leave",
    )

    final_pay_bdt = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="What we really pay for this month",
    )

    is_paid = models.BooleanField(default=False)
    paid_date = models.DateField(null=True, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        unique_together = ("staff", "year", "month")
        ordering = ["-year", "-month", "staff__name"]

    def __str__(self):
        return f"{self.staff.name} {self.year}-{self.month}"

    def recalc_totals(self):
        overtime_hours = Decimal(str(self.overtime_hours or 0))
        overtime_rate = Decimal(str(self.overtime_rate_bdt or 0))
        base_salary = Decimal(str(self.base_salary_bdt or 0))
        bonus = Decimal(str(self.bonus_bdt or 0))
        deduction = Decimal(str(self.deduction_bdt or 0))

        self.overtime_total_bdt = overtime_hours * overtime_rate
        self.final_pay_bdt = base_salary + self.overtime_total_bdt + bonus - deduction

    def save(self, *args, **kwargs):
        if (self.base_salary_bdt is None or self.base_salary_bdt == Decimal("0.00")) and self.staff_id:
            staff_salary = self.staff.base_salary_bdt or Decimal("0.00")
            if staff_salary:
                self.base_salary_bdt = staff_salary

        if self.is_paid and not self.paid_date:
            self.paid_date = timezone.localdate()
        if not self.is_paid:
            self.paid_date = None

        self.recalc_totals()
        super().save(*args, **kwargs)


class MoneyTransfer(models.Model):
    METHOD_BANK = "bank"
    METHOD_APP = "app"
    METHOD_CASH = "cash"

    METHOD_CHOICES = [
        (METHOD_BANK, "Bank"),
        (METHOD_APP, "Online App"),
        (METHOD_CASH, "Cash"),
    ]

    amount_cad = models.DecimalField(max_digits=12, decimal_places=2)
    amount_bdt = models.DecimalField(max_digits=12, decimal_places=2)

    receiver_name = models.CharField(max_length=120)
    note = models.TextField(blank=True)

    sent_method = models.CharField(
        max_length=10,
        choices=METHOD_CHOICES,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    bd_entry = models.OneToOneField(
        "crm.AccountingEntry",   # IMPORTANT fix
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="money_transfer",
    )

    def __str__(self):
        return f"Transfer {self.amount_cad} CAD -> {self.amount_bdt} BDT"


from django.db import models
from django.conf import settings


class AccountingMonthClose(models.Model):

    SIDE_CHOICES = [
        ("CA", "Canada"),
        ("BD", "Bangladesh"),
        ("ALL", "All"),
    ]

    year = models.IntegerField()
    month = models.IntegerField()

    side = models.CharField(
        max_length=3,
        choices=SIDE_CHOICES,
        default="ALL"
    )

    is_closed = models.BooleanField(default=True)
    note = models.CharField(max_length=255, blank=True, default="")

    closed_at = models.DateTimeField(auto_now_add=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )

    class Meta:
        unique_together = ("year", "month", "side")
        verbose_name = "Accounting Month Close"
        verbose_name_plural = "Accounting Month Closes"

    def __str__(self):
        return f"{self.year}-{self.month:02d} {self.side} closed"

from decimal import Decimal
from django.conf import settings
from django.db import models


class AccountingEntry(models.Model):
    SIDE_CA = "CA"
    SIDE_BD = "BD"

    DIR_IN = "IN"
    DIR_OUT = "OUT"

    SIDE_CHOICES = [
        (SIDE_CA, "Canada"),
        (SIDE_BD, "Bangladesh"),
    ]

    DIRECTION_CHOICES = [
        (DIR_IN, "In"),
        (DIR_OUT, "Out"),
    ]

    date = models.DateField()

    side = models.CharField(max_length=2, choices=SIDE_CHOICES, db_index=True)
    direction = models.CharField(max_length=3, choices=DIRECTION_CHOICES, db_index=True)

    status = models.CharField(max_length=20, blank=True, default="")
    main_type = models.CharField(max_length=30, blank=True, default="")
    sub_type = models.CharField(max_length=80, blank=True, default="")

    customer = models.ForeignKey(
        "crm.Customer",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="accounting_entries",
    )
    opportunity = models.ForeignKey(
        "crm.Opportunity",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="accounting_entries",
    )
    production_order = models.ForeignKey(
        "crm.ProductionOrder",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="accounting_entries",
    )
    shipment = models.ForeignKey(
        "crm.Shipment",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="accounting_entries",
    )

    linked_entry = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="linked_children",
    )

    transfer_ref = models.CharField(
        max_length=50,
        blank=True,
        default="",
        db_index=True,
    )

    currency = models.CharField(max_length=3, default="CAD")

    amount_original = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    rate_to_cad = models.DecimalField(max_digits=14, decimal_places=6, default=Decimal("0"))
    rate_to_bdt = models.DecimalField(max_digits=14, decimal_places=6, default=Decimal("0"))

    amount_cad = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    amount_bdt = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))

    description = models.TextField(blank=True, default="")
    internal_note = models.TextField(blank=True, default="")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_accounting_entries",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        amt = self.amount_original or Decimal("0")
        currency = (self.currency or "").upper().strip()
        if currency == "CAD":
            self.rate_to_cad = Decimal("1")
        if currency == "BDT":
            self.rate_to_bdt = Decimal("1")

        r_cad = self.rate_to_cad or Decimal("0")
        r_bdt = self.rate_to_bdt or Decimal("0")
        try:
            self.amount_cad = convert_currency(
                amt,
                currency,
                "CAD",
                stored_rate_to_cad=r_cad,
                stored_rate_to_bdt=r_bdt,
            )
        except CurrencyConversionError as exc:
            raise ValidationError({"rate_to_cad": str(exc)}) from exc
        try:
            self.amount_bdt = convert_currency(
                amt,
                currency,
                "BDT",
                stored_rate_to_cad=r_cad,
                stored_rate_to_bdt=r_bdt,
            )
        except CurrencyConversionError:
            self.amount_bdt = Decimal("0")

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.date} {self.side} {self.direction} {self.amount_cad}"

class AccountingMonthLock(models.Model):
        year = models.IntegerField(db_index=True)
        month = models.IntegerField(db_index=True)
        is_closed = models.BooleanField(default=False, db_index=True)

        closed_at = models.DateTimeField(null=True, blank=True)
        closed_by = models.ForeignKey(
            settings.AUTH_USER_MODEL,
            null=True,
            blank=True,
            on_delete=models.SET_NULL,
            related_name="closed_months",
        )

        class Meta:
            unique_together = ("year", "month")

        def __str__(self):
            return f"{self.year}-{self.month:02d} closed={self.is_closed}"

        from django.db import models
        from django.contrib.auth import get_user_model

        User = get_user_model()

class AccountingMonthlyTarget(models.Model):
    SIDE_CHOICES = (
        ("BD", "Bangladesh"),
        ("CA", "Canada"),
    )

    side = models.CharField(max_length=2, choices=SIDE_CHOICES, default="BD")
    year = models.PositiveIntegerField()
    month = models.PositiveIntegerField()  # 1 to 12

    target_bdt = models.DecimalField(
        max_digits=14,
        decimal_places=0,
        default=Decimal("0"),
    )

    updated_at = models.DateTimeField(auto_now=True)

    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="monthly_targets_updated",
    )

    class Meta:
        unique_together = ("side", "year", "month")
        ordering = ("-year", "-month", "side")

    def __str__(self):
        return f"{self.side} {self.year}-{self.month} target {self.target_bdt} BDT"



from .models_email import EmailThread, EmailMessage
from .models_email_outbox import OutboundEmailLog
from .models_email_config import EmailInboxConfig
from .models_access import UserAccess
