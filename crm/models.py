from django.db import models
from django.conf import settings
from decimal import Decimal
from .models_access import UserAccess

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
        db_table = "crm_aihealthcheck"
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
# crm/models.py
import string
import secrets
from django.db import models
from django.utils import timezone


# ----------------------------
# Helpers
# ----------------------------

def generate_lead_id():
    chars = string.ascii_uppercase + string.digits
    while True:
        lead_id = "L" + "".join(secrets.choice(chars) for _ in range(9))
        if not Lead.objects.filter(lead_id=lead_id).exists():
            return lead_id


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

LEAD_TYPE_CHOICES = [
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
    product_interest = models.CharField(max_length=200, blank=True)
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
        max_length=50,
        choices=LEAD_TYPE_CHOICES,
        default="Startup / New Brand",
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

    owner = models.CharField(max_length=100, blank=True)
    created_date = models.DateField(default=timezone.localdate)
    next_followup = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    def save(self, *args, **kwargs):
        if not self.lead_id:
            self.lead_id = generate_lead_id()

        if not self.created_date:
            self.created_date = timezone.localdate()

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.account_brand} ({self.lead_id})"


# ----------------------------
# Customer model
# ----------------------------

class Customer(models.Model):
    customer_code = models.CharField(max_length=50, unique=True, blank=True)
    lead = models.OneToOneField(Lead, on_delete=models.CASCADE, related_name="customer")

    account_brand = models.CharField(max_length=200)
    contact_name = models.CharField(max_length=200)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    market = models.CharField(max_length=10, blank=True)

    shipping_name = models.CharField(max_length=200, blank=True)
    shipping_address1 = models.CharField(max_length=255, blank=True)
    shipping_address2 = models.CharField(max_length=255, blank=True)
    shipping_city = models.CharField(max_length=100, blank=True)
    shipping_state = models.CharField(max_length=100, blank=True)
    shipping_postcode = models.CharField(max_length=20, blank=True)
    shipping_country = models.CharField(max_length=100, blank=True)

    is_active = models.BooleanField(default=True)
    created_date = models.DateField(default=timezone.localdate)
    notes = models.TextField(blank=True)

    def save(self, *args, **kwargs):
        # never overwrite lead_id if it already exists
        if not self.lead_id:
            self.lead_id = generate_lead_id()

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.account_brand} [{self.customer_code}]"

class LeadComment(models.Model):
    lead = models.ForeignKey(
        Lead,
        related_name="comments",
        on_delete=models.CASCADE,
    )
    opportunity = models.ForeignKey(
        "Opportunity",
        related_name="comments",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )

    author = models.CharField(max_length=100, blank=True, default="")
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    pinned = models.BooleanField(default=False)
    is_ai = models.BooleanField(default=False)

    class Meta:
        ordering = ["-pinned", "-created_at"]

    def __str__(self):
        return f"{self.author}: {self.content[:40]}"


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
    ]

    lead = models.ForeignKey(Lead, related_name="activities", on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    activity_type = models.CharField(max_length=40, choices=ACTIVITY_TYPE_CHOICES)
    description = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_activity_type_display()} for {self.lead.lead_id}"




# -----------------------------------
# Opportunity and related models
# -----------------------------------

class Opportunity(models.Model):
    STAGE_CHOICES = [
        ("Prospecting", "Prospecting"),
        ("Qualification", "Qualification"),
        ("Needs Analysis", "Needs Analysis"),
        ("Proposal", "Proposal or Quote"),
        ("Negotiation", "Negotiation"),
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

    PRODUCT_CATEGORY_CHOICES = [
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

    lead = models.ForeignKey(
        "Lead",
        on_delete=models.CASCADE,
        related_name="opportunities",
    )

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

    order_value = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )

    created_date = models.DateField(auto_now_add=True)
    next_followup = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    is_open = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        is_new = self.pk is None

        if not self.opportunity_id and self.lead and self.lead.lead_id:
            count_for_lead = Opportunity.objects.filter(lead=self.lead).count() + 1
            self.opportunity_id = f"OPP-{self.lead.lead_id}-{count_for_lead:03}"

        super().save(*args, **kwargs)

        if is_new:
            from .models import Customer

            Customer.objects.update_or_create(
                lead=self.lead,
                defaults={
                    "account_brand": self.lead.account_brand,
                    "contact_name": self.lead.contact_name,
                    "email": self.lead.email,
                    "phone": self.lead.phone,
                    "market": self.lead.market,
                    "notes": self.lead.notes,
                },
            )

    def __str__(self):
        return f"{self.opportunity_id} for {self.lead.account_brand}"


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

    name = models.CharField(max_length=200)
    category = models.CharField(
        max_length=50,
        choices=CATEGORY_CHOICES,
        default="other",
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


class InventoryReorder(models.Model):
    inventory_item = models.ForeignKey(
        "InventoryItem",
        on_delete=models.CASCADE,
        related_name="reorders",
        null=True,
        blank=True,
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)

    def __str__(self):
        return f"Reorder for {self.inventory_item} - {self.quantity}"


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

    FACTORY_CHOICES = [
        ("bd", "Bangladesh"),
        ("ca", "Canada"),
    ]

    STATUS_CHOICES = [
        ("planning", "Planning"),
        ("in_progress", "In progress"),
        ("hold", "On hold"),
        ("done", "Done"),
    ]

    # basic order info
    title = models.CharField(max_length=200)
    order_code = models.CharField(max_length=50, unique=True, blank=True)

    lead = models.ForeignKey("Lead", on_delete=models.SET_NULL, null=True, blank=True)
    opportunity = models.ForeignKey(
        "Opportunity", on_delete=models.SET_NULL, null=True, blank=True
    )
    customer = models.ForeignKey(
        "Customer", on_delete=models.SET_NULL, null=True, blank=True
    )
    product = models.ForeignKey(
        "Product", on_delete=models.SET_NULL, null=True, blank=True
    )

    factory_location = models.CharField(
        max_length=10, choices=FACTORY_CHOICES, default="bd"
    )
    order_type = models.CharField(
        max_length=20, choices=ORDER_TYPE_CHOICES, default="fob"
    )

    sample_deadline = models.DateField(null=True, blank=True)
    bulk_deadline = models.DateField(null=True, blank=True)

    qty_total = models.PositiveIntegerField(default=0)
    qty_reject = models.PositiveIntegerField(default=0)

    style_image = models.ImageField(
        upload_to="production_styles/", null=True, blank=True
    )

    # style and work order details
    style_name = models.CharField(max_length=200, blank=True)
    color_info = models.CharField(max_length=200, blank=True)
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

    def __str__(self):
        if self.order_code:
            return f"{self.order_code} - {self.title}"
        return self.title

    @property
    def percent_done(self):
        """
        Percent of stages marked as done.
        """
        stages = self.stages.all()
        total = stages.count()
        if total == 0:
            return 0
        done = stages.filter(status="done").count()
        return int((done / total) * 100)

    @property
    def is_delayed(self):
        """
        True if order is delayed.
        Delayed if:
        - bulk deadline passed and status not done, or
        - any stage planned_end is in past and not done.
        """
        today = timezone.now().date()

        if self.status == "done":
            return False

        if self.bulk_deadline and today > self.bulk_deadline:
            return True

        late_stage = self.stages.filter(
            planned_end__lt=today
        ).exclude(status="done").exists()

        return late_stage

    def update_cost_numbers(self):
        """
        Recalculate fabric, material, production, remake and final cost.
        """
        zero = Decimal("0")

        required = self.fabric_required_kg or zero
        received = self.fabric_received_kg or zero
        used = self.fabric_used_kg or zero
        cost_kg = self.fabric_cost_per_kg_bdt or zero

        # fabric waste and percent
        if used and required:
            self.fabric_waste_kg = used - required
            if required > zero:
                self.fabric_waste_percent = (self.fabric_waste_kg / required) * 100
            else:
                self.fabric_waste_percent = None
        else:
            self.fabric_waste_kg = None
            self.fabric_waste_percent = None

        # leftover
        if received and used:
            self.fabric_leftover_kg = received - used
        else:
            self.fabric_leftover_kg = None

        # fabric total cost
        if used and cost_kg:
            self.fabric_total_cost_bdt = used * cost_kg
        else:
            self.fabric_total_cost_bdt = None

        # material total cost
        fabric_cost = self.fabric_total_cost_bdt or zero
        mat = (
            fabric_cost
            + (self.material_thread_cost_bdt or zero)
            + (self.material_zipper_cost_bdt or zero)
            + (self.material_accessories_cost_bdt or zero)
            + (self.material_label_cost_bdt or zero)
            + (self.material_other_cost_bdt or zero)
        )
        self.material_total_cost_bdt = mat

        # production cost
        prod = (
            (self.production_cutting_cost_bdt or zero)
            + (self.production_sewing_cost_bdt or zero)
            + (self.production_finishing_cost_bdt or zero)
            + (self.production_packing_cost_bdt or zero)
            + (self.production_overhead_cost_bdt or zero)
            + (self.production_other_cost_bdt or zero)
        )
        self.production_total_cost_bdt = self.material_total_cost_bdt + prod

        # remake and final
        remake = self.remake_cost_bdt or zero
        self.actual_total_cost_bdt = self.production_total_cost_bdt + remake

        if self.qty_total and self.actual_total_cost_bdt is not None:
            self.actual_cost_per_piece_bdt = (
                self.actual_total_cost_bdt / Decimal(self.qty_total)
            )
        else:
            self.actual_cost_per_piece_bdt = None

    def save(self, *args, **kwargs):
        """
        Update cost numbers and create order code if needed.
        """
        self.update_cost_numbers()

        if not self.order_code:
            prefix = "PO"

            last = (
                ProductionOrder.objects
                .filter(order_code__startswith=prefix)
                .order_by("-order_code")
                .first()
            )

            if last and last.order_code.startswith(prefix):
                try:
                    last_num = int(last.order_code.replace(prefix, ""))
                except ValueError:
                    last_num = 0
            else:
                last_num = 0

            next_num = last_num + 1
            code = f"{prefix}{next_num:04}"

            while ProductionOrder.objects.filter(order_code=code).exists():
                next_num += 1
                code = f"{prefix}{next_num:04}"

            self.order_code = code

        super().save(*args, **kwargs)


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

    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-ship_date", "-created_at"]

    def __str__(self):
        base = self.tracking_number or f"Shipment {self.pk}"
        if self.order and self.order.order_code:
            return f"{self.order.order_code} - {base}"
        return base

    def update_cost_cad(self):
        """
        Use taka and rate to get Canadian dollar amount.
        """
        if self.cost_bdt and self.rate_bdt_per_cad:
            step = Decimal("0.01")
            self.cost_cad = (self.cost_bdt / self.rate_bdt_per_cad).quantize(step)
        else:
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


class Invoice(models.Model):
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("sent", "Sent to client"),
        ("partial", "Partly paid"),
        ("paid", "Paid"),
        ("cancelled", "Cancelled"),
    ]

    order = models.ForeignKey(
        "ProductionOrder",
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

    invoice_number = models.CharField(max_length=50, unique=True)
    issue_date = models.DateField(default=timezone.now)
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

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-issue_date", "-created_at"]

    def __str__(self):
        return f"Invoice {self.invoice_number}"

    @property
    def balance(self):
        return (self.total_amount or Decimal("0")) - (self.paid_amount or Decimal("0"))


## ==============================
# PRODUCTION ATTACHMENT
## ==============================

class ProductionOrderAttachment(models.Model):
    order = models.ForeignKey(
        "ProductionOrder",
        on_delete=models.CASCADE,
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
        return f"{self.order.order_code} - {self.get_stage_key_display()}"

    @property
    def is_late(self):
        if self.status == "done":
            return False
        if self.planned_end and timezone.now().date() > self.planned_end:
            return True
        return False


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

# other models above …

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


    class Meta:
        ordering = ["-uploaded_at", "-id"]

    def __str__(self):
        return self.original_name or f"Attachment {self.id}"

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
        on_delete=models.CASCADE,
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
        return f"Transfer {self.amount_cad} CAD → {self.amount_bdt} BDT"


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
        r_cad = self.rate_to_cad or Decimal("0")
        r_bdt = self.rate_to_bdt or Decimal("0")

        if r_cad > 0:
            self.amount_cad = (amt * r_cad).quantize(Decimal("0.01"))
        else:
            self.amount_cad = Decimal("0")

        if r_bdt > 0:
            self.amount_bdt = (amt * r_bdt).quantize(Decimal("0.01"))
        else:
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
