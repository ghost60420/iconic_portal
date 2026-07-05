import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from .services.metrics import calc_engagement_rate, calc_engagement_score, calc_engagement_total
from .utils.crypto import encrypt_value, decrypt_value


MARKETING_COUNTRY_CHOICES = [
    ("CA", "Canada"),
    ("US", "USA"),
    ("GB", "UK"),
    ("AU", "Australia"),
    ("AE", "UAE"),
    ("BD", "Bangladesh"),
]

MARKETING_PRODUCT_CATEGORY_CHOICES = [
    ("hoodies", "Hoodies"),
    ("t_shirts", "T shirts"),
    ("activewear", "Activewear"),
    ("streetwear", "Streetwear"),
    ("uniforms", "Uniforms"),
    ("kids_clothing", "Kids clothing"),
    ("private_label_apparel", "Private label apparel"),
    ("low_moq_manufacturing", "Low MOQ manufacturing"),
    ("bangladesh_garment_manufacturing", "Bangladesh garment manufacturing"),
]

MARKETING_PRIORITY_CHOICES = [
    ("high", "High"),
    ("medium", "Medium"),
    ("low", "Low"),
]


class SeoProperty(models.Model):
    name = models.CharField(max_length=200)
    gsc_site_url = models.CharField(max_length=255, blank=True, default="")
    ga4_property_id = models.CharField(max_length=60, blank=True, default="")
    is_active = models.BooleanField(default=True)

    last_sync_at = models.DateTimeField(null=True, blank=True)
    last_sync_status = models.CharField(max_length=30, blank=True, default="")
    last_sync_message = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class SeoQueryDaily(models.Model):
    property = models.ForeignKey(SeoProperty, on_delete=models.CASCADE, related_name="query_days")
    date = models.DateField(db_index=True)
    query = models.CharField(max_length=300, db_index=True)
    page = models.CharField(max_length=500, db_index=True)
    country = models.CharField(max_length=12, blank=True, default="")
    device = models.CharField(max_length=20, blank=True, default="")

    clicks = models.PositiveIntegerField(default=0)
    impressions = models.PositiveIntegerField(default=0)
    ctr = models.DecimalField(max_digits=6, decimal_places=4, default=Decimal("0"))
    position = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0"))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-date", "query")
        constraints = [
            models.UniqueConstraint(
                fields=["property", "date", "query", "page", "country", "device"],
                name="seo_query_daily_unique",
            )
        ]

    def __str__(self) -> str:
        return f"{self.query} {self.date}"


class SeoPageDaily(models.Model):
    property = models.ForeignKey(SeoProperty, on_delete=models.CASCADE, related_name="page_days")
    date = models.DateField(db_index=True)
    page = models.CharField(max_length=500, db_index=True)

    clicks = models.PositiveIntegerField(default=0)
    impressions = models.PositiveIntegerField(default=0)
    ctr = models.DecimalField(max_digits=6, decimal_places=4, default=Decimal("0"))
    position = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0"))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-date", "page")
        constraints = [
            models.UniqueConstraint(
                fields=["property", "date", "page"],
                name="seo_page_daily_unique",
            )
        ]

    def __str__(self) -> str:
        return f"{self.page} {self.date}"


class WebsiteTrafficDaily(models.Model):
    property = models.ForeignKey(SeoProperty, on_delete=models.CASCADE, related_name="traffic_days")
    date = models.DateField(db_index=True)
    channel = models.CharField(max_length=80, blank=True, default="", db_index=True)
    source = models.CharField(max_length=120, blank=True, default="")
    medium = models.CharField(max_length=120, blank=True, default="")
    campaign = models.CharField(max_length=160, blank=True, default="")

    visitors = models.PositiveIntegerField(default=0)
    sessions = models.PositiveIntegerField(default=0)
    engaged_sessions = models.PositiveIntegerField(default=0)
    page_views = models.PositiveIntegerField(default=0)
    events = models.PositiveIntegerField(default=0)
    conversions = models.PositiveIntegerField(default=0)
    engagement_rate = models.DecimalField(max_digits=6, decimal_places=4, default=Decimal("0"))
    avg_engagement_seconds = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-date", "channel", "source")
        constraints = [
            models.UniqueConstraint(
                fields=["property", "date", "channel", "source", "medium", "campaign"],
                name="website_traffic_daily_unique",
            )
        ]
        indexes = [
            models.Index(fields=["property", "date"]),
            models.Index(fields=["channel", "source"]),
        ]

    def __str__(self) -> str:
        label = self.channel or self.source or "Website"
        return f"{label} {self.date}"


class WebsitePageDaily(models.Model):
    property = models.ForeignKey(SeoProperty, on_delete=models.CASCADE, related_name="website_page_days")
    date = models.DateField(db_index=True)
    page_path = models.CharField(max_length=500, db_index=True)
    page_title = models.CharField(max_length=300, blank=True, default="")

    visitors = models.PositiveIntegerField(default=0)
    sessions = models.PositiveIntegerField(default=0)
    page_views = models.PositiveIntegerField(default=0)
    entrances = models.PositiveIntegerField(default=0)
    exits = models.PositiveIntegerField(default=0)
    conversions = models.PositiveIntegerField(default=0)
    avg_engagement_seconds = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-date", "page_path")
        constraints = [
            models.UniqueConstraint(
                fields=["property", "date", "page_path"],
                name="website_page_daily_unique",
            )
        ]
        indexes = [
            models.Index(fields=["property", "date"]),
            models.Index(fields=["page_path"]),
        ]

    def __str__(self) -> str:
        return f"{self.page_path} {self.date}"


class SocialAccount(models.Model):
    PLATFORM_CHOICES = [
        ("facebook", "Facebook"),
        ("meta_business", "Meta Business Suite"),
        ("instagram", "Instagram"),
        ("linkedin", "LinkedIn"),
        ("tiktok", "TikTok"),
        ("youtube", "YouTube"),
        ("google_business", "Google Business Profile"),
    ]

    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES)
    external_account_id = models.CharField(max_length=120, blank=True, default="")
    display_name = models.CharField(max_length=200, blank=True, default="")
    username = models.CharField(max_length=120, blank=True, default="")
    profile_url = models.URLField(blank=True, default="")
    timezone = models.CharField(max_length=50, blank=True, default="")
    is_active = models.BooleanField(default=True)

    last_sync_at = models.DateTimeField(null=True, blank=True)
    last_successful_sync = models.DateTimeField(null=True, blank=True)
    last_sync_status = models.CharField(max_length=30, blank=True, default="")
    last_sync_message = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("platform", "display_name")
        indexes = [
            models.Index(fields=["platform", "external_account_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_platform_display()} {self.display_name}".strip()

    @property
    def account_name(self) -> str:
        return self.display_name or ""


class SocialContent(models.Model):
    CONTENT_CHOICES = [
        ("post", "Post"),
        ("reel", "Reel"),
        ("short", "Short"),
        ("video", "Video"),
        ("long_video", "Long Video"),
        ("story", "Story"),
        ("ad", "Ad"),
    ]

    account = models.ForeignKey(SocialAccount, on_delete=models.CASCADE, related_name="contents")
    platform = models.CharField(max_length=20, choices=SocialAccount.PLATFORM_CHOICES)
    external_content_id = models.CharField(max_length=120)
    content_type = models.CharField(max_length=20, choices=CONTENT_CHOICES, default="post")
    title = models.CharField(max_length=300, blank=True, default="")
    message_text = models.TextField(blank=True, default="")
    permalink = models.URLField(blank=True, default="")
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-published_at", "-created_at")
        constraints = [
            models.UniqueConstraint(
                fields=["platform", "external_content_id"],
                name="social_content_unique",
            )
        ]

    def __str__(self) -> str:
        return self.title or self.external_content_id


class SocialMetricDaily(models.Model):
    content = models.ForeignKey(SocialContent, on_delete=models.CASCADE, related_name="daily_metrics")
    date = models.DateField(db_index=True)
    impressions = models.PositiveIntegerField(default=0)
    reach = models.PositiveIntegerField(default=0)
    views = models.PositiveIntegerField(default=0)
    likes = models.PositiveIntegerField(default=0)
    comments = models.PositiveIntegerField(default=0)
    shares = models.PositiveIntegerField(default=0)
    saves = models.PositiveIntegerField(default=0)
    clicks = models.PositiveIntegerField(default=0)
    watch_time_seconds = models.PositiveIntegerField(default=0)
    avg_view_duration_seconds = models.PositiveIntegerField(default=0)
    profile_visits = models.PositiveIntegerField(default=0)
    follows = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-date",)
        constraints = [
            models.UniqueConstraint(fields=["content", "date"], name="social_metric_daily_unique")
        ]


class SocialAudienceDaily(models.Model):
    account = models.ForeignKey(SocialAccount, on_delete=models.CASCADE, related_name="audience_days")
    date = models.DateField(db_index=True)
    country_json = models.JSONField(blank=True, default=dict)
    city_json = models.JSONField(blank=True, default=dict)
    gender_age_json = models.JSONField(blank=True, default=dict)
    language_json = models.JSONField(blank=True, default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-date",)
        constraints = [
            models.UniqueConstraint(fields=["account", "date"], name="social_audience_daily_unique")
        ]


class AccountMetricDaily(models.Model):
    account = models.ForeignKey(SocialAccount, on_delete=models.CASCADE, related_name="account_days")
    date = models.DateField(db_index=True)
    followers_total = models.PositiveIntegerField(default=0)
    followers_change = models.IntegerField(default=0)
    impressions = models.PositiveIntegerField(default=0)
    reach = models.PositiveIntegerField(default=0)
    views = models.PositiveIntegerField(default=0)
    clicks = models.PositiveIntegerField(default=0)
    engagement_total = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-date",)
        constraints = [
            models.UniqueConstraint(fields=["account", "date"], name="account_metric_daily_unique")
        ]


class AdAccount(models.Model):
    platform_account = models.ForeignKey(
        SocialAccount,
        on_delete=models.CASCADE,
        related_name="ad_accounts",
    )
    external_ad_account_id = models.CharField(max_length=120)
    currency = models.CharField(max_length=10, blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["platform_account", "external_ad_account_id"],
                name="ad_account_unique",
            )
        ]

    def __str__(self) -> str:
        return f"{self.platform_account} {self.external_ad_account_id}".strip()


class AdCampaign(models.Model):
    ad_account = models.ForeignKey(AdAccount, on_delete=models.CASCADE, related_name="campaigns")
    external_campaign_id = models.CharField(max_length=120)
    name = models.CharField(max_length=200, blank=True, default="")
    status = models.CharField(max_length=30, blank=True, default="")
    objective = models.CharField(max_length=80, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["ad_account", "external_campaign_id"],
                name="ad_campaign_unique",
            )
        ]

    def __str__(self) -> str:
        return self.name or self.external_campaign_id


class AdMetricDaily(models.Model):
    ad_campaign = models.ForeignKey(AdCampaign, on_delete=models.CASCADE, related_name="daily_metrics")
    date = models.DateField(db_index=True)
    spend = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    impressions = models.PositiveIntegerField(default=0)
    clicks = models.PositiveIntegerField(default=0)
    cpc = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("0"))
    cpm = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("0"))
    conversions = models.PositiveIntegerField(default=0)
    cost_per_conversion = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("0"))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-date",)
        constraints = [
            models.UniqueConstraint(
                fields=["ad_campaign", "date"],
                name="ad_metric_daily_unique",
            )
        ]


class Campaign(models.Model):
    GOAL_CHOICES = [
        ("leads", "Leads"),
        ("meetings", "Meetings"),
        ("quotes", "Quotes"),
        ("samples", "Samples"),
    ]

    name = models.CharField(max_length=200)
    goal = models.CharField(max_length=20, choices=GOAL_CHOICES, default="leads")
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    budget = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="marketing_campaigns",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return self.name


class TrackedLink(models.Model):
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="links")
    name = models.CharField(max_length=200)
    base_url = models.URLField()
    utm_source = models.CharField(max_length=120, blank=True, default="")
    utm_medium = models.CharField(max_length=120, blank=True, default="")
    utm_campaign = models.CharField(max_length=120, blank=True, default="")
    utm_content = models.CharField(max_length=120, blank=True, default="")
    utm_term = models.CharField(max_length=120, blank=True, default="")
    final_url = models.URLField(blank=True, default="")
    qr_png = models.ImageField(upload_to="marketing/qr/", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["campaign", "name"], name="tracked_link_unique")
        ]

    def __str__(self) -> str:
        return f"{self.campaign.name} - {self.name}"

    def build_final_url(self) -> str:
        if not self.base_url:
            return ""
        parts = []
        if self.utm_source:
            parts.append(f"utm_source={self.utm_source}")
        if self.utm_medium:
            parts.append(f"utm_medium={self.utm_medium}")
        if self.utm_campaign:
            parts.append(f"utm_campaign={self.utm_campaign}")
        if self.utm_content:
            parts.append(f"utm_content={self.utm_content}")
        if self.utm_term:
            parts.append(f"utm_term={self.utm_term}")
        if not parts:
            return self.base_url
        joiner = "&" if "?" in self.base_url else "?"
        return f"{self.base_url}{joiner}{'&'.join(parts)}"

    def save(self, *args, **kwargs):
        self.final_url = self.build_final_url()
        super().save(*args, **kwargs)


class ContactList(models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="contact_lists",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name


class Contact(models.Model):
    CONSENT_CHOICES = [
        ("unknown", "Unknown"),
        ("opted_in", "Opted In"),
        ("opted_out", "Opted Out"),
    ]

    SOURCE_CHOICES = [
        ("upload", "Upload"),
        ("website", "Website"),
        ("referral", "Referral"),
        ("manual", "Manual"),
    ]

    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=50, blank=True, default="")
    first_name = models.CharField(max_length=100, blank=True, default="")
    last_name = models.CharField(max_length=100, blank=True, default="")
    company = models.CharField(max_length=200, blank=True, default="")
    website = models.CharField(max_length=255, blank=True, default="")
    city = models.CharField(max_length=120, blank=True, default="")
    state = models.CharField(max_length=120, blank=True, default="")
    country = models.CharField(max_length=120, blank=True, default="")
    industry = models.CharField(max_length=120, blank=True, default="")
    job_title = models.CharField(max_length=120, blank=True, default="")
    tags = models.JSONField(blank=True, default=list)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default="upload")
    consent_status = models.CharField(max_length=20, choices=CONSENT_CHOICES, default="unknown")
    do_not_contact = models.BooleanField(default=False)
    unsubscribe_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    last_contacted_at = models.DateTimeField(null=True, blank=True)
    last_reply_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["email"]),
            models.Index(fields=["company"]),
            models.Index(fields=["country"]),
        ]

    def __str__(self) -> str:
        return self.email

    @property
    def full_name(self):
        return (f"{self.first_name} {self.last_name}").strip()


class ContactListMembership(models.Model):
    contact_list = models.ForeignKey(ContactList, on_delete=models.CASCADE, related_name="memberships")
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name="memberships")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["contact_list", "contact"], name="contact_list_member_unique")
        ]


class OutreachCampaign(models.Model):
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("active", "Active"),
        ("paused", "Paused"),
        ("completed", "Completed"),
    ]

    CHANNEL_CHOICES = [
        ("email", "Email"),
        ("phone", "Phone"),
    ]

    name = models.CharField(max_length=200)
    channel = models.CharField(max_length=10, choices=CHANNEL_CHOICES, default="email")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft")
    sending_account = models.CharField(max_length=200, blank=True, default="")
    daily_limit = models.PositiveIntegerField(default=30)
    hourly_limit = models.PositiveIntegerField(default=10)
    schedule_window_json = models.JSONField(blank=True, default=dict)
    followup_rules_json = models.JSONField(blank=True, default=dict)
    contact_list = models.ForeignKey(ContactList, null=True, blank=True, on_delete=models.SET_NULL)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="outreach_campaigns",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return self.name


class OutreachMessageTemplate(models.Model):
    campaign = models.ForeignKey(OutreachCampaign, on_delete=models.CASCADE, related_name="templates")
    subject_template = models.CharField(max_length=255)
    body_template = models.TextField()
    variables_json = models.JSONField(blank=True, default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.campaign.name} template"


class OutreachSendLog(models.Model):
    SEND_TYPE_CHOICES = [
        ("initial", "Initial"),
        ("followup1", "Follow up 1"),
        ("followup2", "Follow up 2"),
    ]

    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("sent", "Sent"),
        ("failed", "Failed"),
        ("bounced", "Bounced"),
        ("unsubscribed", "Unsubscribed"),
        ("replied", "Replied"),
        ("stopped", "Stopped"),
    ]

    campaign = models.ForeignKey(OutreachCampaign, on_delete=models.CASCADE, related_name="send_logs")
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name="send_logs")
    send_type = models.CharField(max_length=20, choices=SEND_TYPE_CHOICES, default="initial")
    queued_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued")
    provider_message_id = models.CharField(max_length=200, blank=True, default="")
    error_text = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["campaign", "contact", "send_type"], name="outreach_send_unique")
        ]
        indexes = [
            models.Index(fields=["status", "sent_at"]),
        ]


class UnsubscribeEvent(models.Model):
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name="unsubscribe_events")
    channel = models.CharField(max_length=20, default="email")
    event_at = models.DateTimeField(auto_now_add=True)
    reason = models.CharField(max_length=255, blank=True, default="")


class CallTask(models.Model):
    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("called", "Called"),
        ("no_answer", "No answer"),
        ("callback", "Call back"),
        ("not_interested", "Not interested"),
        ("interested", "Interested"),
        ("meeting_booked", "Meeting booked"),
    ]

    campaign = models.ForeignKey(OutreachCampaign, on_delete=models.CASCADE, related_name="call_tasks")
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name="call_tasks")
    priority_score = models.IntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued")
    next_call_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class BestPracticeLibrary(models.Model):
    CATEGORY_CHOICES = [
        ("hooks", "Hooks"),
        ("captions", "Captions"),
        ("hashtags", "Hashtags"),
        ("posting_times", "Posting times"),
        ("creative", "Creative checklist"),
        ("offers", "Offers"),
        ("dos_donts", "Do and don't"),
    ]

    platform = models.CharField(max_length=20, choices=SocialAccount.PLATFORM_CHOICES)
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES, default="hooks")
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True, default="")
    examples_json = models.JSONField(blank=True, default=list)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="marketing_best_practices",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return self.title


class InsightItem(models.Model):
    SOURCE_CHOICES = [
        ("seo", "SEO"),
        ("content", "Content"),
        ("audience", "Audience"),
        ("ads", "Ads"),
        ("outreach", "Outreach"),
        ("social", "Social"),
    ]

    STATUS_CHOICES = [
        ("open", "Open"),
        ("done", "Done"),
        ("snoozed", "Snoozed"),
    ]

    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default="seo")
    title = models.CharField(max_length=200)
    reason = models.TextField(blank=True, default="")
    recommended_action = models.TextField(blank=True, default="")
    note = models.TextField(blank=True, default="")
    priority_score = models.IntegerField(default=50)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="open")
    platform = models.CharField(max_length=20, choices=SocialAccount.PLATFORM_CHOICES, blank=True, default="")

    related_object_type = models.CharField(max_length=60, blank=True, default="")
    related_object_id = models.CharField(max_length=60, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-priority_score", "-created_at")


class MarketingKeywordPlan(models.Model):
    INTENT_CHOICES = [
        ("informational", "Informational"),
        ("commercial", "Commercial"),
        ("transactional", "Transactional"),
        ("navigational", "Navigational"),
    ]
    TREND_CHOICES = [
        ("rising", "Rising"),
        ("stable", "Stable"),
        ("declining", "Declining"),
        ("unknown", "Unknown"),
    ]
    DIFFICULTY_CHOICES = [
        ("easy", "Easy"),
        ("medium", "Medium"),
        ("hard", "Hard"),
        ("unknown", "Unknown"),
    ]
    CONTENT_TYPE_CHOICES = [
        ("landing_page", "Landing page"),
        ("blog", "Blog"),
        ("video", "Video"),
        ("case_study", "Case study"),
        ("social", "Social content"),
    ]
    STATUS_CHOICES = [
        ("idea", "Idea"),
        ("approved", "Approved"),
        ("in_progress", "In progress"),
        ("published", "Published"),
        ("paused", "Paused"),
    ]

    keyword = models.CharField(max_length=240, db_index=True)
    target_country = models.CharField(max_length=2, choices=MARKETING_COUNTRY_CHOICES, default="CA")
    target_audience = models.CharField(max_length=240, blank=True, default="")
    product_category = models.CharField(
        max_length=50,
        choices=MARKETING_PRODUCT_CATEGORY_CHOICES,
        default="private_label_apparel",
    )
    search_intent = models.CharField(max_length=20, choices=INTENT_CHOICES, default="commercial")
    priority = models.CharField(max_length=10, choices=MARKETING_PRIORITY_CHOICES, default="medium")
    trend_status = models.CharField(max_length=20, choices=TREND_CHOICES, default="unknown")
    difficulty_estimate = models.CharField(max_length=20, choices=DIFFICULTY_CHOICES, default="unknown")
    content_type = models.CharField(max_length=20, choices=CONTENT_TYPE_CHOICES, default="landing_page")
    landing_page_suggestion = models.CharField(max_length=300, blank=True, default="")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="idea")
    notes = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="marketing_keyword_plans",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["status", "priority"]),
            models.Index(fields=["target_country", "product_category"]),
        ]

    def __str__(self) -> str:
        return self.keyword


class MarketingContentIdea(models.Model):
    CONTENT_TYPE_CHOICES = [
        ("blog", "Blog"),
        ("video", "Video"),
        ("reel", "Reel"),
        ("linkedin_post", "LinkedIn post"),
        ("instagram_carousel", "Instagram carousel"),
        ("tiktok_video", "TikTok video"),
        ("google_business_post", "Google Business post"),
        ("email_campaign", "Email campaign"),
        ("case_study", "Case study"),
    ]
    PLATFORM_CHOICES = [
        ("website", "Website"),
        ("linkedin", "LinkedIn"),
        ("instagram", "Instagram"),
        ("tiktok", "TikTok"),
        ("google_business", "Google Business"),
        ("youtube", "YouTube"),
        ("email", "Email"),
    ]
    FUNNEL_CHOICES = [
        ("awareness", "Awareness"),
        ("consideration", "Consideration"),
        ("conversion", "Conversion"),
        ("retention", "Retention"),
    ]
    STATUS_CHOICES = [
        ("idea", "Idea"),
        ("approved", "Approved"),
        ("assigned", "Assigned"),
        ("in_progress", "In progress"),
        ("ready_for_review", "Ready for review"),
        ("published", "Published"),
        ("archived", "Archived"),
    ]

    title = models.CharField(max_length=300)
    content_type = models.CharField(max_length=30, choices=CONTENT_TYPE_CHOICES, default="blog")
    target_platform = models.CharField(max_length=30, choices=PLATFORM_CHOICES, default="website")
    keyword = models.CharField(max_length=240, blank=True, default="")
    audience = models.CharField(max_length=240, blank=True, default="")
    funnel_stage = models.CharField(max_length=20, choices=FUNNEL_CHOICES, default="awareness")
    priority = models.CharField(max_length=10, choices=MARKETING_PRIORITY_CHOICES, default="medium")
    due_date = models.DateField(null=True, blank=True, db_index=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_marketing_content_ideas",
    )
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="idea", db_index=True)
    notes = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_marketing_content_ideas",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("due_date", "-created_at")
        indexes = [models.Index(fields=["target_platform", "status"])]

    def __str__(self) -> str:
        return self.title


class MarketingVideoIdea(models.Model):
    PLATFORM_CHOICES = [
        ("youtube", "YouTube"),
        ("linkedin", "LinkedIn"),
        ("instagram", "Instagram"),
        ("tiktok", "TikTok"),
        ("facebook", "Facebook"),
    ]
    STATUS_CHOICES = MarketingContentIdea.STATUS_CHOICES

    video_title = models.CharField(max_length=300)
    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES, default="youtube")
    hook = models.CharField(max_length=500, blank=True, default="")
    main_talking_points = models.TextField(blank=True, default="")
    product_category = models.CharField(
        max_length=50,
        choices=MARKETING_PRODUCT_CATEGORY_CHOICES,
        default="private_label_apparel",
    )
    target_keyword = models.CharField(max_length=240, blank=True, default="")
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="idea", db_index=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_marketing_video_ideas",
    )
    due_date = models.DateField(null=True, blank=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_marketing_video_ideas",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("due_date", "-created_at")
        indexes = [models.Index(fields=["platform", "status"])]

    def __str__(self) -> str:
        return self.video_title


class OAuthCredential(models.Model):
    PLATFORM_CHOICES = [
        ("gsc", "Google Search Console"),
        ("ga4", "Google Analytics 4"),
        ("meta", "Meta"),
        ("facebook", "Facebook"),
        ("meta_business", "Meta Business Suite"),
        ("instagram", "Instagram"),
        ("linkedin", "LinkedIn"),
        ("tiktok", "TikTok"),
        ("youtube", "YouTube"),
        ("google_business", "Google Business Profile"),
    ]

    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES)
    social_account = models.ForeignKey(
        SocialAccount,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="credentials",
    )
    platform_account = models.ForeignKey(
        SocialAccount,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="platform_credentials",
    )

    encrypted_access_token = models.TextField(blank=True, default="")
    encrypted_refresh_token = models.TextField(blank=True, default="")
    account_name = models.CharField(max_length=200, blank=True, default="")
    account_id = models.CharField(max_length=120, blank=True, default="")
    expires_at = models.DateTimeField(null=True, blank=True)
    scopes = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_sync_status = models.CharField(max_length=30, blank=True, default="")
    last_error = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("platform", "account_name", "account_id", "-updated_at")
        indexes = [
            models.Index(fields=["platform", "is_active"]),
            models.Index(fields=["platform", "account_id"]),
        ]

    def set_tokens(self, access_token: str = "", refresh_token: str = "", expires_at=None):
        self.encrypted_access_token = encrypt_value(access_token)
        self.encrypted_refresh_token = encrypt_value(refresh_token)
        self.expires_at = expires_at

    def get_access_token(self) -> str:
        return decrypt_value(self.encrypted_access_token)

    def get_refresh_token(self) -> str:
        return decrypt_value(self.encrypted_refresh_token)

    @property
    def token_expires_at(self):
        return self.expires_at

    @property
    def has_access_token(self) -> bool:
        return bool(self.get_access_token())

    @property
    def has_refresh_token(self) -> bool:
        return bool(self.get_refresh_token())

    def __str__(self) -> str:
        label = self.account_name or self.account_id or self.get_platform_display()
        return f"{self.get_platform_display()} {label}".strip()


class OAuthConnectionRequest(models.Model):
    STATUS_CHOICES = [
        ("initiated", "Initiated"),
        ("received", "Received"),
        ("completed", "Completed"),
        ("error", "Error"),
    ]

    platform = models.CharField(max_length=20, choices=OAuthCredential.PLATFORM_CHOICES)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    state = models.CharField(max_length=120, unique=True)
    code = models.TextField(blank=True, default="")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="initiated")
    error_message = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["platform", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.platform} {self.status}"


class MarketingCompetitor(models.Model):
    STATUS_CHOICES = [
        ("watching", "Watching"),
        ("paused", "Paused"),
        ("archived", "Archived"),
    ]

    name = models.CharField(max_length=200)
    website = models.URLField(blank=True, default="")
    industry = models.CharField(max_length=120, blank=True, default="")
    country = models.CharField(max_length=2, choices=MARKETING_COUNTRY_CHOICES, blank=True, default="")
    category = models.CharField(max_length=120, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    last_checked_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="watching")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class MarketingCompetitorAccount(models.Model):
    PLATFORM_CHOICES = [
        ("instagram", "Instagram"),
        ("facebook", "Facebook"),
        ("linkedin", "LinkedIn"),
        ("tiktok", "TikTok"),
        ("youtube", "YouTube"),
        ("google_business", "Google Business"),
    ]

    competitor = models.ForeignKey(
        MarketingCompetitor,
        on_delete=models.CASCADE,
        related_name="accounts",
    )
    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES)
    profile_url = models.URLField(blank=True, default="")
    handle = models.CharField(max_length=120, blank=True, default="")
    followers_count = models.PositiveIntegerField(default=0)
    following_count = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("competitor__name", "platform", "handle")
        constraints = [
            models.UniqueConstraint(
                fields=["competitor", "platform", "handle"],
                name="marketing_competitor_account_unique",
            )
        ]

    def __str__(self) -> str:
        label = self.handle or self.profile_url or self.get_platform_display()
        return f"{self.competitor.name} {label}".strip()


class MarketingCompetitorPost(models.Model):
    competitor_account = models.ForeignKey(
        MarketingCompetitorAccount,
        on_delete=models.CASCADE,
        related_name="posts",
    )
    post_url = models.URLField(blank=True, default="")
    caption_text = models.TextField(blank=True, default="")
    content_type = models.CharField(max_length=20, choices=SocialContent.CONTENT_CHOICES, default="post")
    published_at = models.DateTimeField(null=True, blank=True)
    likes = models.PositiveIntegerField(default=0)
    comments = models.PositiveIntegerField(default=0)
    shares = models.PositiveIntegerField(default=0)
    views = models.PositiveIntegerField(default=0)
    saves = models.PositiveIntegerField(default=0)
    engagement_score = models.PositiveIntegerField(default=0)
    engagement_rate = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal("0"))
    detected_theme = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-published_at", "-created_at")

    def __str__(self) -> str:
        return self.post_url or f"{self.competitor_account} post"

    def save(self, *args, **kwargs):
        engagement_total = calc_engagement_total(
            likes=self.likes,
            comments=self.comments,
            shares=self.shares,
            saves=self.saves,
        )
        self.engagement_score = calc_engagement_score(
            likes=self.likes,
            comments=self.comments,
            shares=self.shares,
            saves=self.saves,
            clicks=0,
        )
        self.engagement_rate = Decimal(
            str(
                calc_engagement_rate(
                    reach=0,
                    views=self.views,
                    engagement_total=engagement_total,
                )
            )
        )
        super().save(*args, **kwargs)


class MarketingCompetitorInsight(models.Model):
    competitor = models.ForeignKey(
        MarketingCompetitor,
        on_delete=models.CASCADE,
        related_name="insights",
    )
    title = models.CharField(max_length=200)
    reason = models.TextField(blank=True, default="")
    recommended_action = models.TextField(blank=True, default="")
    priority_score = models.IntegerField(default=50)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-priority_score", "-created_at")

    def __str__(self) -> str:
        return f"{self.competitor.name}: {self.title}"
