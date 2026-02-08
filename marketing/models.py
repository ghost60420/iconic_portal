import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from .utils.crypto import encrypt_value, decrypt_value


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
    expires_at = models.DateTimeField(null=True, blank=True)
    scopes = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def set_tokens(self, access_token: str = "", refresh_token: str = "", expires_at=None):
        self.encrypted_access_token = encrypt_value(access_token)
        self.encrypted_refresh_token = encrypt_value(refresh_token)
        self.expires_at = expires_at

    def get_access_token(self) -> str:
        return decrypt_value(self.encrypted_access_token)

    def get_refresh_token(self) -> str:
        return decrypt_value(self.encrypted_refresh_token)


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
