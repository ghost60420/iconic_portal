from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.urls import reverse
from urllib.parse import urlencode


class Position(models.Model):
    code = models.SlugField(max_length=60, unique=True)
    name = models.CharField(max_length=120, unique=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("sort_order", "name")

    def __str__(self):
        return self.name


class Department(models.Model):
    code = models.SlugField(max_length=60, unique=True)
    name = models.CharField(max_length=120, unique=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("sort_order", "name")

    def __str__(self):
        return self.name


class UserDashboardPreference(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="dashboard_preference",
    )
    hidden_widgets = models.JSONField(default=list, blank=True)
    widget_order = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Dashboard preferences for {self.user}"


class SavedFilter(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="crm_saved_filters",
    )
    module = models.CharField(max_length=40, db_index=True)
    name = models.CharField(max_length=120)
    query_params = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("module", "name")
        constraints = [
            models.UniqueConstraint(fields=("user", "module", "name"), name="crm_saved_filter_user_module_name"),
        ]

    def __str__(self):
        return f"{self.user}: {self.name}"

    @property
    def target_url(self):
        routes = {
            "leads": "leads_list",
            "opportunities": "opportunities_list",
            "quotations": "cost_sheet_list",
            "production": "production_list",
            "invoices": "invoice_list",
            "customers": "customers_list",
        }
        route = routes.get(self.module)
        if not route:
            return "#"
        pairs = []
        for key, values in (self.query_params or {}).items():
            for value in values if isinstance(values, list) else [values]:
                pairs.append((key, value))
        query = urlencode(pairs)
        return f"{reverse(route)}?{query}" if query else reverse(route)


class FavoriteRecord(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="crm_favorites",
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    record = GenericForeignKey("content_type", "object_id")
    record_type = models.CharField(max_length=40)
    record_label = models.CharField(max_length=220)
    target_url = models.CharField(max_length=300)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [
            models.UniqueConstraint(fields=("user", "content_type", "object_id"), name="crm_favorite_user_record"),
        ]
        indexes = [models.Index(fields=("user", "-created_at"))]

    def __str__(self):
        return f"{self.user}: {self.record_label}"


class RecentlyViewedRecord(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="crm_recent_records",
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    record = GenericForeignKey("content_type", "object_id")
    record_type = models.CharField(max_length=40)
    record_label = models.CharField(max_length=220)
    target_url = models.CharField(max_length=300)
    viewed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-viewed_at", "-id")
        constraints = [
            models.UniqueConstraint(fields=("user", "content_type", "object_id"), name="crm_recent_user_record"),
        ]
        indexes = [models.Index(fields=("user", "-viewed_at"))]

    def __str__(self):
        return f"{self.user}: {self.record_label}"


class RecentSearch(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="crm_recent_searches",
    )
    query = models.CharField(max_length=160)
    normalized_query = models.CharField(max_length=160)
    search_count = models.PositiveIntegerField(default=1)
    searched_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-searched_at", "-id")
        constraints = [
            models.UniqueConstraint(fields=("user", "normalized_query"), name="crm_recent_search_user_query"),
        ]
        indexes = [models.Index(fields=("user", "-searched_at"))]

    def __str__(self):
        return f"{self.user}: {self.query}"


class CRMSetting(models.Model):
    category = models.CharField(max_length=40, db_index=True)
    key = models.SlugField(max_length=80)
    label = models.CharField(max_length=140)
    value = models.JSONField(default=dict, blank=True)
    description = models.CharField(max_length=240, blank=True, default="")
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="updated_crm_settings",
    )

    class Meta:
        ordering = ("category", "label")
        constraints = [
            models.UniqueConstraint(fields=("category", "key"), name="crm_setting_category_key"),
        ]

    def __str__(self):
        return f"{self.category}: {self.label}"
