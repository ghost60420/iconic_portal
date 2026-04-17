from django.conf import settings
from django.db import models


class LeadBrainUpload(models.Model):
    STATUS_PENDING = "pending"
    STATUS_PROCESSING = "processing"
    STATUS_COMPLETE = "complete"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_COMPLETE, "Complete"),
        (STATUS_FAILED, "Failed"),
    ]

    file = models.FileField(upload_to="leadbrain/uploads/")
    file_name = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="leadbrain_uploads",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    row_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-uploaded_at", "-id"]

    def __str__(self):
        return self.file_name or f"Upload {self.id}"


class LeadBrainCompany(models.Model):
    FIT_GOOD = "good_fit"
    FIT_POSSIBLE = "possible_fit"
    FIT_WEAK = "weak_fit"

    FIT_LABEL_CHOICES = [
        (FIT_GOOD, "Good Fit"),
        (FIT_POSSIBLE, "Possible Fit"),
        (FIT_WEAK, "Weak Fit"),
    ]

    upload = models.ForeignKey(
        LeadBrainUpload,
        on_delete=models.CASCADE,
        related_name="companies",
    )
    row_number = models.PositiveIntegerField(default=0)
    company_name = models.CharField(max_length=255, blank=True)
    website = models.URLField(blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=100, blank=True)
    linkedin_url = models.URLField(blank=True)
    best_contact_name = models.CharField(max_length=255, blank=True)
    best_contact_title = models.CharField(max_length=255, blank=True)
    business_type = models.CharField(max_length=255, blank=True)
    fit_label = models.CharField(max_length=20, choices=FIT_LABEL_CHOICES, default=FIT_WEAK)
    fit_score = models.PositiveIntegerField(default=0)
    ai_summary = models.TextField(blank=True)
    fit_reason = models.TextField(blank=True)
    suggested_action = models.CharField(max_length=255, blank=True)
    raw_row_json = models.JSONField(default=dict, blank=True)
    research_json = models.JSONField(default=dict, blank=True)
    reviewed = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-fit_score", "company_name", "id"]

    def __str__(self):
        return self.company_name or f"Company {self.id}"

