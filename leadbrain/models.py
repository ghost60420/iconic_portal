from datetime import datetime, time, timedelta

from django.conf import settings
from django.db.models import Count, Q
from django.db import models
from django.utils import timezone


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
    blank_rows = models.PositiveIntegerField(default=0)
    pending_rows = models.PositiveIntegerField(default=0)
    processing_rows = models.PositiveIntegerField(default=0)
    completed_rows = models.PositiveIntegerField(default=0)
    failed_rows = models.PositiveIntegerField(default=0)
    progress_percent = models.PositiveSmallIntegerField(default=0)
    detected_columns_json = models.JSONField(default=list, blank=True)
    sample_rows_json = models.JSONField(default=list, blank=True)
    invalid_row_examples_json = models.JSONField(default=list, blank=True)
    duplicate_row_examples_json = models.JSONField(default=list, blank=True)
    status_note = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    inactive_at = models.DateTimeField(blank=True, null=True)
    inactive_reason = models.TextField(blank=True)
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


class LeadBrainDiscoveryJob(models.Model):
    STATUS_QUEUED = "queued"
    STATUS_PROCESSING = "processing"
    STATUS_COMPLETE = "complete"
    STATUS_PARTIAL = "partial"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_QUEUED, "Queued"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_COMPLETE, "Complete"),
        (STATUS_PARTIAL, "Partial"),
        (STATUS_FAILED, "Failed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    SCHEDULE_MANUAL = "manual"
    SCHEDULE_DAILY = "daily"
    SCHEDULE_WEEKLY = "weekly"

    SCHEDULE_CHOICES = [
        (SCHEDULE_MANUAL, "Manual"),
        (SCHEDULE_DAILY, "Daily"),
        (SCHEDULE_WEEKLY, "Weekly"),
    ]

    SOURCE_WEB = "web_search"
    SOURCE_DIRECTORIES = "business_directories"
    SOURCE_SHOPIFY = "shopify_stores"
    SOURCE_CHOICES = [
        (SOURCE_WEB, "Google Search Patterns"),
        (SOURCE_DIRECTORIES, "Business Directories"),
        (SOURCE_SHOPIFY, "Shopify Store Detection"),
    ]

    COUNTRY_CANADA = "Canada"
    COUNTRY_USA = "USA"

    COUNTRY_CHOICES = [
        (COUNTRY_CANADA, "Canada"),
        (COUNTRY_USA, "USA"),
    ]

    NICHE_STREETWEAR = "streetwear"
    NICHE_ACTIVEWEAR = "activewear"
    NICHE_KIDSWEAR = "kidswear"
    NICHE_ECOMMERCE = "ecommerce_apparel"
    NICHE_BOUTIQUE = "boutique_fashion"
    NICHE_PRIVATE_LABEL = "private_label"
    NICHE_UNIFORMS = "uniforms"
    NICHE_MERCH = "merch"
    NICHE_CHOICES = [
        (NICHE_STREETWEAR, "Streetwear"),
        (NICHE_ACTIVEWEAR, "Activewear"),
        (NICHE_KIDSWEAR, "Kidswear"),
        (NICHE_ECOMMERCE, "Ecommerce Apparel"),
        (NICHE_BOUTIQUE, "Boutique Fashion"),
        (NICHE_PRIVATE_LABEL, "Private Label"),
        (NICHE_UNIFORMS, "Uniforms"),
        (NICHE_MERCH, "Merch"),
    ]

    TRIGGER_MANUAL = "manual"
    TRIGGER_SCHEDULED = "scheduled"

    TRIGGER_CHOICES = [
        (TRIGGER_MANUAL, "Manual"),
        (TRIGGER_SCHEDULED, "Scheduled"),
    ]

    SLOT_CANADA_MORNING = "canada_morning"
    SLOT_USA_AFTERNOON = "usa_afternoon"

    SLOT_CHOICES = [
        (SLOT_CANADA_MORNING, "Canada Morning"),
        (SLOT_USA_AFTERNOON, "USA Afternoon"),
    ]

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="leadbrain_discovery_jobs",
    )
    name = models.CharField(max_length=255, blank=True, default="")
    is_active = models.BooleanField(default=True, db_index=True)
    source_types_json = models.JSONField(default=list, blank=True)
    countries_json = models.JSONField(default=list, blank=True)
    niches_json = models.JSONField(default=list, blank=True)
    run_time = models.TimeField(blank=True, null=True)
    max_results_per_run = models.PositiveIntegerField(default=25)
    min_fit_score = models.PositiveSmallIntegerField(default=65)
    source_type = models.CharField(max_length=40, choices=SOURCE_CHOICES)
    selected_sources_json = models.JSONField(default=list, blank=True)
    country = models.CharField(max_length=40, choices=COUNTRY_CHOICES)
    niche = models.CharField(max_length=40, choices=NICHE_CHOICES)
    max_results = models.PositiveIntegerField(default=25)
    schedule_type = models.CharField(max_length=20, choices=SCHEDULE_CHOICES, default=SCHEDULE_MANUAL)
    max_runs_per_day = models.PositiveSmallIntegerField(default=1)
    apparel_only = models.BooleanField(default=True)
    minimum_score = models.PositiveSmallIntegerField(default=65)
    is_paused = models.BooleanField(default=False, db_index=True)
    next_run_at = models.DateTimeField(blank=True, null=True, db_index=True)
    trigger_mode = models.CharField(max_length=20, choices=TRIGGER_CHOICES, default=TRIGGER_MANUAL)
    scheduled_slot = models.CharField(max_length=40, choices=SLOT_CHOICES, blank=True, default="")
    scheduled_for_date = models.DateField(blank=True, null=True, db_index=True)
    query_plan_json = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_QUEUED)
    total_runs = models.PositiveIntegerField(default=0)
    total_leads_found = models.PositiveIntegerField(default=0)
    total_strong_fits = models.PositiveIntegerField(default=0)
    results_found = models.PositiveIntegerField(default=0)
    candidates_saved = models.PositiveIntegerField(default=0)
    duplicates_skipped = models.PositiveIntegerField(default=0)
    weak_skipped = models.PositiveIntegerField(default=0)
    failed_candidates = models.PositiveIntegerField(default=0)
    status_note = models.TextField(blank=True)
    sample_results_json = models.JSONField(default=list, blank=True)
    duplicate_examples_json = models.JSONField(default=list, blank=True)
    saved_examples_json = models.JSONField(default=list, blank=True)
    upload = models.ForeignKey(
        LeadBrainUpload,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="discovery_jobs",
    )
    last_run_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        if self.name:
            return self.name
        countries = self.effective_countries
        niches = self.effective_niches
        country_label = countries[0] if len(countries) == 1 else f"{len(countries)} markets"
        niche_label = (
            dict(self.NICHE_CHOICES).get(niches[0], niches[0]) if len(niches) == 1 else f"{len(niches)} niches"
        )
        return f"{country_label} / {niche_label} / {self.primary_source_label}"

    @property
    def selected_sources(self):
        values = self.source_types_json or self.selected_sources_json or []
        return [item for item in values if item]

    @property
    def effective_sources(self):
        return self.selected_sources or ([self.source_type] if self.source_type else [])

    @property
    def effective_source_types(self):
        return self.effective_sources

    @property
    def effective_countries(self):
        values = [item for item in (self.countries_json or []) if item]
        return values or ([self.country] if self.country else [])

    @property
    def effective_niches(self):
        values = [item for item in (self.niches_json or []) if item]
        return values or ([self.niche] if self.niche else [])

    @property
    def effective_max_results_per_run(self):
        return int(self.max_results_per_run or self.max_results or 25)

    @property
    def effective_min_fit_score(self):
        return int(self.min_fit_score or self.minimum_score or 65)

    @property
    def primary_source_label(self):
        source_map = dict(self.SOURCE_CHOICES)
        return source_map.get(self.effective_sources[0], self.get_source_type_display()) if self.effective_sources else self.get_source_type_display()

    @property
    def source_summary(self):
        source_map = dict(self.SOURCE_CHOICES)
        labels = [source_map.get(item, item) for item in self.effective_sources]
        return ", ".join(labels) if labels else self.get_source_type_display()

    def compute_next_run_at(self, *, reference=None):
        if not self.is_active or self.is_paused or self.schedule_type == self.SCHEDULE_MANUAL:
            return None
        reference = reference or timezone.now()
        scheduled_time = self.run_time or time(hour=9, minute=0)
        tz = timezone.get_current_timezone()
        candidate = timezone.make_aware(datetime.combine(timezone.localtime(reference).date(), scheduled_time), tz)
        if candidate <= reference:
            candidate += timedelta(days=7 if self.schedule_type == self.SCHEDULE_WEEKLY else 1)
        return candidate


class LeadBrainDiscoveryRun(models.Model):
    STATUS_CHOICES = LeadBrainDiscoveryJob.STATUS_CHOICES
    ACTIVE_STATUSES = [
        LeadBrainDiscoveryJob.STATUS_QUEUED,
        LeadBrainDiscoveryJob.STATUS_PROCESSING,
    ]

    job = models.ForeignKey(
        LeadBrainDiscoveryJob,
        on_delete=models.CASCADE,
        related_name="runs",
    )
    upload = models.ForeignKey(
        LeadBrainUpload,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="discovery_runs",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=LeadBrainDiscoveryJob.STATUS_QUEUED)
    queries_json = models.JSONField(default=list, blank=True)
    total_candidates_found = models.PositiveIntegerField(default=0)
    total_candidates_saved = models.PositiveIntegerField(default=0)
    total_duplicates_skipped = models.PositiveIntegerField(default=0)
    total_weak_skipped = models.PositiveIntegerField(default=0)
    total_failed = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    results_found = models.PositiveIntegerField(default=0)
    candidates_saved = models.PositiveIntegerField(default=0)
    duplicates_skipped = models.PositiveIntegerField(default=0)
    weak_skipped = models.PositiveIntegerField(default=0)
    failed_candidates = models.PositiveIntegerField(default=0)
    strong_fits_found = models.PositiveIntegerField(default=0)
    status_note = models.TextField(blank=True)
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    finished_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["job"],
                condition=Q(status__in=[LeadBrainDiscoveryJob.STATUS_QUEUED, LeadBrainDiscoveryJob.STATUS_PROCESSING]),
                name="leadbrain_one_active_discovery_run_per_job",
            )
        ]

    def __str__(self):
        return f"{self.job.display_name} run {self.pk}"


class LeadBrainDiscoveryCandidate(models.Model):
    STATUS_QUEUED = "queued"
    STATUS_PROCESSING = "processing"
    STATUS_SAVED = "saved"
    STATUS_DUPLICATE = "duplicate"
    STATUS_WEAK = "weak"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_QUEUED, "Queued"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_SAVED, "Saved"),
        (STATUS_DUPLICATE, "Duplicate"),
        (STATUS_WEAK, "Weak"),
        (STATUS_FAILED, "Failed"),
    ]

    run = models.ForeignKey(
        LeadBrainDiscoveryRun,
        on_delete=models.CASCADE,
        related_name="candidates",
    )
    company_name = models.CharField(max_length=255, blank=True)
    website = models.URLField(blank=True)
    source_type = models.CharField(max_length=40, blank=True)
    source_url = models.URLField(blank=True)
    country = models.CharField(max_length=100, blank=True)
    niche = models.CharField(max_length=100, blank=True)
    discovery_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_QUEUED, db_index=True)
    research_json = models.JSONField(default=dict, blank=True)
    fit_score = models.PositiveIntegerField(default=0)
    fit_label = models.CharField(max_length=20, blank=True, default="")
    skip_reason = models.CharField(max_length=255, blank=True)
    created_leadbrain_company = models.ForeignKey(
        "LeadBrainCompany",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="discovery_candidates",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["run_id", "id"]
        indexes = [
            models.Index(fields=["run", "discovery_status"]),
            models.Index(fields=["website"]),
        ]

    def __str__(self):
        return self.company_name or self.website or f"Candidate {self.pk}"


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
    is_active = models.BooleanField(default=True, db_index=True)
    inactive_at = models.DateTimeField(blank=True, null=True)
    inactive_reason = models.TextField(blank=True)
    duplicate_of = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="inactive_duplicates",
    )
    moved_to_leads = models.BooleanField(default=False, db_index=True)
    moved_to_lead = models.ForeignKey(
        "crm.Lead",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="leadbrain_companies",
    )
    moved_to_lead_code = models.CharField(max_length=20, blank=True)
    moved_to_leads_at = models.DateTimeField(blank=True, null=True)
    source_type = models.CharField(max_length=40, blank=True, db_index=True)
    source_detail = models.CharField(max_length=255, blank=True)
    discovery_job = models.ForeignKey(
        LeadBrainDiscoveryJob,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="discovered_companies",
    )
    discovery_run = models.ForeignKey(
        LeadBrainDiscoveryRun,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="discovered_companies",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-fit_score", "company_name", "id"]

    def __str__(self):
        return self.company_name or f"Company {self.id}"

    @property
    def is_discovery_source(self):
        if self.discovery_run_id or self.discovery_job_id or self.source_type:
            return True
        return (self.raw_row_json or {}).get("leadbrain_source") == "discovery"

    @property
    def is_new_discovery(self):
        if not self.is_discovery_source:
            return False
        return self.created_at >= timezone.now() - timedelta(days=7)

    @property
    def discovery_fit_badge(self):
        if self.fit_score >= 80:
            return "strong"
        if self.fit_score >= 65:
            return "possible"
        return ""

    @property
    def leadbrain_source_label(self):
        source_map = dict(LeadBrainDiscoveryJob.SOURCE_CHOICES)
        if self.source_type:
            return source_map.get(self.source_type, self.source_type)
        if self.discovery_run_id or self.discovery_job_id:
            return "Discovery"
        return "Uploaded File"

    @property
    def contact_availability(self):
        channels = []
        if self.email:
            channels.append("Email")
        if self.phone:
            channels.append("Phone")
        if self.best_contact_name:
            channels.append("Contact Name")
        if self.linkedin_url:
            channels.append("LinkedIn")
        return ", ".join(channels) if channels else "No contact info"


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
