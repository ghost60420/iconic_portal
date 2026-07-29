from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from .models_employee import EmployeeProfile
from .models_kpi_reviews import KPIReview


KPI_NOTIFICATION_EVENT_CHOICES = (
    ("review_open", "Review Open"),
    ("review_due_soon", "Review Due Soon"),
    ("review_due_today", "Review Due Today"),
    ("review_overdue", "Review Overdue"),
    ("approval_pending", "Approval Pending"),
    ("rejected_correction", "Rejected Review Correction"),
    ("locked_confirmation", "Locked Review Confirmation"),
    ("improvement_action", "Improvement Action"),
    ("critical_red", "Critical Red"),
    ("yellow_attention", "Yellow Attention"),
    ("green_recognition", "Green Recognition"),
    ("bonus_ready", "Bonus Ready"),
    ("bonus_pending", "Bonus Pending Approval"),
    ("bonus_blocked", "Bonus Blocked"),
    ("manager_queue", "Manager Queue"),
    ("executive_intelligence", "Executive Intelligence"),
    ("data_quality", "Data Quality"),
)
KPI_NOTIFICATION_EVENT_TYPES = {
    value for value, _label in KPI_NOTIFICATION_EVENT_CHOICES
}

KPI_NOTIFICATION_SEVERITY_CHOICES = (
    ("critical", "Critical"),
    ("high", "High"),
    ("normal", "Normal"),
    ("information", "Information"),
)
KPI_NOTIFICATION_SEVERITIES = {
    value for value, _label in KPI_NOTIFICATION_SEVERITY_CHOICES
}

KPI_NOTIFICATION_SCHEDULE_CHOICES = (
    ("daily", "Daily"),
    ("weekly", "Weekly"),
    ("monthly", "Monthly"),
    ("quarterly", "Quarterly"),
    ("annual", "Annual"),
)
KPI_NOTIFICATION_SCHEDULES = {
    value for value, _label in KPI_NOTIFICATION_SCHEDULE_CHOICES
}

KPI_NOTIFICATION_RECIPIENT_SCOPES = {
    "employee",
    "manager",
    "director",
    "hr",
    "executive",
}


class KPINotificationRule(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_PUBLISHED = "published"
    STATUS_RETIRED = "retired"
    STATUS_CHOICES = (
        (STATUS_DRAFT, "Draft"),
        (STATUS_PUBLISHED, "Published"),
        (STATUS_RETIRED, "Retired"),
    )

    code = models.SlugField(max_length=80)
    name = models.CharField(max_length=140)
    version = models.PositiveIntegerField()
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
        db_index=True,
    )
    effective_start = models.DateField()
    effective_end = models.DateField(null=True, blank=True)
    enabled_event_types = models.JSONField(default=list)
    reminder_offsets = models.JSONField(default=list)
    improvement_reminder_days = models.JSONField(default=list)
    enabled_schedules = models.JSONField(default=list)
    positive_recognition_enabled = models.BooleanField(default=False)
    retry_limit = models.PositiveSmallIntegerField(default=0)
    batch_size = models.PositiveSmallIntegerField(default=100)
    source_lookback_days = models.PositiveSmallIntegerField(default=90)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_kpi_notification_rules",
    )
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="published_kpi_notification_rules",
    )
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("code", "-version")
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=("code", "version"),
                name="kpi_notify_rule_version_uniq",
            ),
            models.CheckConstraint(
                condition=Q(version__gte=1),
                name="kpi_notify_rule_version_pos",
            ),
            models.CheckConstraint(
                condition=Q(effective_end__isnull=True)
                | Q(effective_end__gte=F("effective_start")),
                name="kpi_notify_rule_dates",
            ),
            models.CheckConstraint(
                condition=Q(retry_limit__lte=5),
                name="kpi_notify_rule_retry_limit",
            ),
            models.CheckConstraint(
                condition=Q(batch_size__gte=1) & Q(batch_size__lte=1000),
                name="kpi_notify_rule_batch_size",
            ),
            models.CheckConstraint(
                condition=Q(source_lookback_days__gte=1)
                & Q(source_lookback_days__lte=3650),
                name="kpi_notify_rule_lookback",
            ),
        ]
        indexes = [
            models.Index(
                fields=("code", "status", "effective_start"),
                name="kpi_notify_rule_lookup_idx",
            ),
        ]

    @staticmethod
    def _integer_list(value, field, minimum, maximum):
        if (
            not isinstance(value, list)
            or any(
                isinstance(item, bool)
                or not isinstance(item, int)
                or not minimum <= item <= maximum
                for item in value
            )
            or len(set(value)) != len(value)
        ):
            raise ValidationError(
                {field: f"Use unique integer values from {minimum} through {maximum}."}
            )

    def clean(self):
        super().clean()
        errors = {}
        if self.effective_end and self.effective_end < self.effective_start:
            errors["effective_end"] = "Effective end cannot precede effective start."
        if (
            not isinstance(self.enabled_event_types, list)
            or not self.enabled_event_types
            or len(set(self.enabled_event_types)) != len(self.enabled_event_types)
            or any(
                value not in KPI_NOTIFICATION_EVENT_TYPES
                for value in self.enabled_event_types
            )
        ):
            errors["enabled_event_types"] = (
                "Configure one or more unique supported notification types."
            )
        if (
            not isinstance(self.enabled_schedules, list)
            or not self.enabled_schedules
            or len(set(self.enabled_schedules)) != len(self.enabled_schedules)
            or any(
                value not in KPI_NOTIFICATION_SCHEDULES
                for value in self.enabled_schedules
            )
        ):
            errors["enabled_schedules"] = (
                "Configure one or more unique supported schedules."
            )
        try:
            self._integer_list(
                self.reminder_offsets,
                "reminder_offsets",
                -365,
                365,
            )
        except ValidationError as exc:
            errors.update(exc.message_dict)
        try:
            self._integer_list(
                self.improvement_reminder_days,
                "improvement_reminder_days",
                0,
                365,
            )
        except ValidationError as exc:
            errors.update(exc.message_dict)
        if not 0 <= self.retry_limit <= 5:
            errors["retry_limit"] = "Retry limit must be between 0 and 5."
        if not 1 <= self.batch_size <= 1000:
            errors["batch_size"] = "Batch size must be between 1 and 1000."
        if not 1 <= self.source_lookback_days <= 3650:
            errors["source_lookback_days"] = (
                "Source lookback must be between 1 and 3650 days."
            )

        previous = type(self).objects.filter(pk=self.pk).first() if self.pk else None
        if previous and previous.status in {
            self.STATUS_PUBLISHED,
            self.STATUS_RETIRED,
        }:
            mutable_fields = {"status", "updated_at"}
            changed = {
                field.attname
                for field in self._meta.concrete_fields
                if field.attname not in mutable_fields
                and getattr(previous, field.attname) != getattr(self, field.attname)
            }
            if changed:
                errors["status"] = "Published notification rules are immutable."
            allowed = {previous.status}
            if previous.status == self.STATUS_PUBLISHED:
                allowed.add(self.STATUS_RETIRED)
            if self.status not in allowed:
                errors["status"] = "This notification-rule transition is not allowed."
        if self.status == self.STATUS_PUBLISHED and not self.published_by_id:
            errors["published_by"] = "Published by is required."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        if self.status == self.STATUS_PUBLISHED and not self.published_at:
            self.published_at = timezone.now()
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = set(kwargs["update_fields"]) | {
                    "published_at"
                }
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != self.STATUS_DRAFT:
            raise ValidationError("Published notification rules cannot be deleted.")
        return super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.name} v{self.version}"


class KPIEscalationRule(models.Model):
    notification_rule = models.ForeignKey(
        KPINotificationRule,
        on_delete=models.PROTECT,
        related_name="escalation_rules",
    )
    event_type = models.CharField(
        max_length=40,
        choices=KPI_NOTIFICATION_EVENT_CHOICES,
    )
    stage = models.PositiveSmallIntegerField()
    trigger_offset_days = models.IntegerField(default=0)
    severity = models.CharField(
        max_length=20,
        choices=KPI_NOTIFICATION_SEVERITY_CHOICES,
    )
    recipient_scopes = models.JSONField(default=list)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("event_type", "stage")
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=("notification_rule", "event_type", "stage"),
                name="kpi_escalation_rule_stage_uniq",
            ),
            models.CheckConstraint(
                condition=Q(stage__lte=20),
                name="kpi_escalation_stage_limit",
            ),
            models.CheckConstraint(
                condition=Q(trigger_offset_days__gte=-3650)
                & Q(trigger_offset_days__lte=365),
                name="kpi_escalation_offset",
            ),
        ]
        indexes = [
            models.Index(
                fields=("notification_rule", "event_type", "is_active"),
                name="kpi_escalation_lookup_idx",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if (
            not isinstance(self.recipient_scopes, list)
            or not self.recipient_scopes
            or len(set(self.recipient_scopes)) != len(self.recipient_scopes)
            or any(
                value not in KPI_NOTIFICATION_RECIPIENT_SCOPES
                for value in self.recipient_scopes
            )
        ):
            errors["recipient_scopes"] = (
                "Configure one or more unique supported recipient scopes."
            )
        if self.notification_rule_id and self.notification_rule.status != (
            KPINotificationRule.STATUS_DRAFT
        ):
            previous = type(self).objects.filter(pk=self.pk).first() if self.pk else None
            if previous is None or any(
                getattr(previous, field) != getattr(self, field)
                for field in (
                    "event_type",
                    "stage",
                    "trigger_offset_days",
                    "severity",
                    "recipient_scopes",
                    "is_active",
                )
            ):
                errors["notification_rule"] = (
                    "Published notification escalation rules are immutable."
                )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.notification_rule.status != KPINotificationRule.STATUS_DRAFT:
            raise ValidationError(
                "Published notification escalation rules cannot be deleted."
            )
        return super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.event_type} stage {self.stage}"


class ProtectedKPINotificationEventQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("KPI notification events must use the service.")

    def delete(self):
        raise ValidationError("KPI notification events cannot be deleted.")

    def bulk_create(self, objs, **kwargs):
        raise ValidationError("KPI notification events must use the service.")

    def bulk_update(self, objs, fields, **kwargs):
        raise ValidationError("KPI notification events must use the service.")


class KPINotificationEvent(models.Model):
    notification_rule = models.ForeignKey(
        KPINotificationRule,
        on_delete=models.PROTECT,
        related_name="notification_events",
    )
    escalation_rule = models.ForeignKey(
        KPIEscalationRule,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="notification_events",
    )
    automation_notification = models.OneToOneField(
        "crm.AutomationNotification",
        on_delete=models.PROTECT,
        related_name="kpi_event",
    )
    automation_run = models.ForeignKey(
        "KPIAutomationRun",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="notification_events",
    )
    notification_type = models.CharField(
        max_length=40,
        choices=KPI_NOTIFICATION_EVENT_CHOICES,
        db_index=True,
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="kpi_notification_events",
    )
    related_employee = models.ForeignKey(
        EmployeeProfile,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="kpi_notification_events",
    )
    related_manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="managed_kpi_notification_events",
    )
    related_review = models.ForeignKey(
        KPIReview,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="notification_events",
    )
    related_department_code = models.CharField(max_length=80, blank=True, default="")
    related_department_name = models.CharField(
        max_length=140,
        blank=True,
        default="",
    )
    severity = models.CharField(
        max_length=20,
        choices=KPI_NOTIFICATION_SEVERITY_CHOICES,
        db_index=True,
    )
    title = models.CharField(max_length=220)
    message = models.TextField(blank=True, default="")
    action_link = models.CharField(max_length=300, blank=True, default="")
    due_date = models.DateField(null=True, blank=True)
    notification_version = models.CharField(max_length=80)
    deduplication_key = models.CharField(max_length=64, unique=True)
    source_event = models.CharField(max_length=120)
    source_record_type = models.CharField(max_length=80)
    source_record_id = models.CharField(max_length=80)
    source_period = models.CharField(max_length=80, blank=True, default="")
    source_state = models.CharField(max_length=64)
    dismissed_at = models.DateTimeField(null=True, blank=True)
    dismissed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="dismissed_kpi_notification_events",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ProtectedKPINotificationEventQuerySet.as_manager()
    service_objects = models.Manager()

    class Meta:
        ordering = ("-created_at", "-id")
        default_permissions = ()
        constraints = [
            models.CheckConstraint(
                condition=Q(dismissed_at__isnull=True, dismissed_by__isnull=True)
                | Q(dismissed_at__isnull=False, dismissed_by__isnull=False),
                name="kpi_notify_event_dismissal",
            ),
        ]
        indexes = [
            models.Index(
                fields=("recipient", "dismissed_at", "-created_at"),
                name="kpi_notify_event_recipient_idx",
            ),
            models.Index(
                fields=("related_review", "notification_type", "-created_at"),
                name="kpi_notify_event_review_idx",
            ),
            models.Index(
                fields=("source_event", "source_record_type", "source_record_id"),
                name="kpi_notify_event_source_idx",
            ),
            models.Index(
                fields=("severity", "dismissed_at", "-created_at"),
                name="kpi_notify_event_severity_idx",
            ),
        ]

    def save(self, *args, **kwargs):
        service_create = kwargs.pop("service_create", False)
        service_dismiss = kwargs.pop("service_dismiss", False)
        if self._state.adding and not service_create:
            raise ValidationError("KPI notification events must use the service.")
        if not self._state.adding:
            previous = type(self).service_objects.get(pk=self.pk)
            changed = {
                field.attname
                for field in self._meta.concrete_fields
                if getattr(previous, field.attname) != getattr(self, field.attname)
            }
            if not service_dismiss or not changed.issubset(
                {"dismissed_at", "dismissed_by_id"}
            ):
                raise ValidationError("KPI notification events are immutable.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("KPI notification events cannot be deleted.")

    @property
    def is_read(self):
        return self.automation_notification.is_read

    @property
    def is_dismissed(self):
        return self.dismissed_at is not None

    def __str__(self):
        return f"{self.notification_type} for {self.recipient_id}"


class KPIAutomationRun(models.Model):
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = (
        (STATUS_RUNNING, "Running"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
    )

    task_name = models.CharField(max_length=120)
    schedule = models.CharField(
        max_length=20,
        choices=KPI_NOTIFICATION_SCHEDULE_CHOICES,
        db_index=True,
    )
    source_date = models.DateField()
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_RUNNING,
        db_index=True,
    )
    retry_count = models.PositiveSmallIntegerField(default=0)
    retry_limit = models.PositiveSmallIntegerField(default=0)
    batch_size = models.PositiveSmallIntegerField(default=100)
    candidates_count = models.PositiveIntegerField(default=0)
    created_count = models.PositiveIntegerField(default=0)
    duplicate_count = models.PositiveIntegerField(default=0)
    failure_type = models.CharField(max_length=120, blank=True, default="")
    failure_message = models.CharField(max_length=300, blank=True, default="")
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="initiated_kpi_automation_runs",
    )

    class Meta:
        ordering = ("-started_at", "-id")
        default_permissions = ()
        constraints = [
            models.CheckConstraint(
                condition=Q(retry_count__lte=F("retry_limit")),
                name="kpi_automation_retry_order",
            ),
            models.CheckConstraint(
                condition=Q(retry_limit__lte=5),
                name="kpi_automation_retry_limit",
            ),
            models.CheckConstraint(
                condition=Q(batch_size__gte=1) & Q(batch_size__lte=1000),
                name="kpi_automation_batch_size",
            ),
        ]
        indexes = [
            models.Index(
                fields=("task_name", "status", "-started_at"),
                name="kpi_automation_task_status_idx",
            ),
            models.Index(
                fields=("schedule", "source_date", "-started_at"),
                name="kpi_automation_schedule_idx",
            ),
        ]

    def __str__(self):
        return f"{self.task_name} {self.source_date} ({self.status})"
