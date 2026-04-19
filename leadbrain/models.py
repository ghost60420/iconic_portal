from django.conf import settings
from django.db.models import Count, Q
from django.db import models


class LeadBrainUpload(models.Model):
    STATUS_QUEUED = "queued"
    STATUS_PENDING = STATUS_QUEUED
    STATUS_PARSING = "parsing"
    STATUS_PROCESSING = "processing"
    STATUS_COMPLETE = "complete"
    STATUS_FAILED = "failed"
    STATUS_PARTIAL = "partial"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_QUEUED, "Queued"),
        (STATUS_PARSING, "Parsing"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_PARTIAL, "Partial"),
        (STATUS_COMPLETE, "Complete"),
        (STATUS_FAILED, "Failed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    file = models.FileField(upload_to="leadbrain/uploads/")
    file_name = models.CharField(max_length=255, blank=True)
    file_size = models.PositiveBigIntegerField(default=0)
    file_hash = models.CharField(max_length=64, blank=True, db_index=True)
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
    source_row_count = models.PositiveIntegerField(default=0)
    total_rows = models.PositiveIntegerField(default=0)
    imported_rows = models.PositiveIntegerField(default=0)
    skipped_duplicate_rows = models.PositiveIntegerField(default=0)
    invalid_rows = models.PositiveIntegerField(default=0)
    pending_rows = models.PositiveIntegerField(default=0)
    processing_rows = models.PositiveIntegerField(default=0)
    completed_rows = models.PositiveIntegerField(default=0)
    failed_rows = models.PositiveIntegerField(default=0)
    progress_percent = models.PositiveSmallIntegerField(default=0)
    detected_columns_json = models.JSONField(default=list, blank=True)
    sample_rows_json = models.JSONField(default=list, blank=True)
    invalid_row_examples_json = models.JSONField(default=list, blank=True)
    status_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-uploaded_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["uploaded_by", "file_hash"],
                condition=Q(status__in=["queued", "parsing", "processing"]) & ~Q(file_hash=""),
                name="leadbrain_active_upload_per_user_hash",
            )
        ]

    def __str__(self):
        return self.file_name or f"Upload {self.id}"

    def refresh_progress(self, *, save=True):
        stats = self.companies.aggregate(
            total=Count("id"),
            pending=Count("id", filter=Q(research_status=LeadBrainCompany.STATUS_PENDING)),
            processing=Count("id", filter=Q(research_status=LeadBrainCompany.STATUS_PROCESSING)),
            completed=Count("id", filter=Q(research_status=LeadBrainCompany.STATUS_COMPLETE)),
            failed=Count("id", filter=Q(research_status=LeadBrainCompany.STATUS_FAILED)),
        )
        total_rows = stats["total"] or self.total_rows or self.row_count
        pending_rows = stats["pending"] or 0
        processing_rows = stats["processing"] or 0
        completed_rows = stats["completed"] or 0
        failed_rows = stats["failed"] or 0

        processed_rows = completed_rows + failed_rows
        if total_rows:
            progress_percent = min(100, int((processed_rows * 100) / total_rows))
        else:
            progress_percent = 0

        if self.status == self.STATUS_CANCELLED:
            status = self.STATUS_CANCELLED
        elif self.status == self.STATUS_QUEUED and not total_rows:
            status = self.STATUS_QUEUED
        elif self.status == self.STATUS_PARSING and not total_rows:
            status = self.STATUS_PARSING
        elif not total_rows:
            status = self.STATUS_FAILED
        elif completed_rows == total_rows and not failed_rows:
            status = self.STATUS_COMPLETE
        elif failed_rows == total_rows and not completed_rows:
            status = self.STATUS_FAILED
        elif processed_rows == total_rows and completed_rows and failed_rows:
            status = self.STATUS_PARTIAL
        elif pending_rows or processing_rows or completed_rows or failed_rows:
            status = self.STATUS_PROCESSING
        else:
            status = self.STATUS_QUEUED

        self.row_count = total_rows
        self.total_rows = total_rows
        self.pending_rows = pending_rows
        self.processing_rows = processing_rows
        self.completed_rows = completed_rows
        self.failed_rows = failed_rows
        self.progress_percent = progress_percent
        self.status = status

        if save:
            self.save(
                update_fields=[
                    "row_count",
                    "total_rows",
                    "pending_rows",
                    "processing_rows",
                    "completed_rows",
                    "failed_rows",
                    "progress_percent",
                    "status",
                    "updated_at",
                ]
            )
        return self


class LeadBrainCompany(models.Model):
    STATUS_PENDING = "pending"
    STATUS_PROCESSING = "processing"
    STATUS_COMPLETE = "complete"
    STATUS_FAILED = "failed"

    FIT_GOOD = "good_fit"
    FIT_POSSIBLE = "possible_fit"
    FIT_WEAK = "weak_fit"

    RESEARCH_STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_COMPLETE, "Complete"),
        (STATUS_FAILED, "Failed"),
    ]

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
    fit_label = models.CharField(max_length=20, choices=FIT_LABEL_CHOICES, blank=True, default="")
    fit_score = models.PositiveIntegerField(default=0)
    ai_summary = models.TextField(blank=True)
    fit_reason = models.TextField(blank=True)
    suggested_action = models.CharField(max_length=255, blank=True)
    raw_row_json = models.JSONField(default=dict, blank=True)
    research_json = models.JSONField(default=dict, blank=True)
    research_status = models.CharField(max_length=20, choices=RESEARCH_STATUS_CHOICES, default=STATUS_PENDING)
    research_claim_token = models.CharField(max_length=64, blank=True, db_index=True)
    research_claimed_at = models.DateTimeField(blank=True, null=True)
    research_error = models.TextField(blank=True)
    processed_at = models.DateTimeField(blank=True, null=True)
    reviewed = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-fit_score", "company_name", "id"]

    def __str__(self):
        return self.company_name or f"Company {self.id}"


class LeadBrainWorker(models.Model):
    STATUS_STARTING = "starting"
    STATUS_IDLE = "idle"
    STATUS_RUNNING = "running"
    STATUS_STOPPED = "stopped"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_STARTING, "Starting"),
        (STATUS_IDLE, "Idle"),
        (STATUS_RUNNING, "Running"),
        (STATUS_STOPPED, "Stopped"),
        (STATUS_FAILED, "Failed"),
    ]

    name = models.CharField(max_length=80, unique=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_STARTING)
    hostname = models.CharField(max_length=255, blank=True)
    pid = models.PositiveIntegerField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(blank=True, null=True)
    started_at = models.DateTimeField(blank=True, null=True)
    current_upload = models.ForeignKey(
        LeadBrainUpload,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="worker_assignments",
    )
    last_error = models.TextField(blank=True)
    processed_batches = models.PositiveIntegerField(default=0)
    processed_rows = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return self.name
