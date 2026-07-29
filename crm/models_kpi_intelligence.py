from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


PERCENT_MIN = Decimal("0.00")
PERCENT_MAX = Decimal("100.00")
PERCENT_VALIDATORS = [
    MinValueValidator(PERCENT_MIN),
    MaxValueValidator(PERCENT_MAX),
]


class KPIIntelligenceRuleSet(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_PUBLISHED = "published"
    STATUS_RETIRED = "retired"
    STATUS_CHOICES = (
        (STATUS_DRAFT, "Draft"),
        (STATUS_PUBLISHED, "Published"),
        (STATUS_RETIRED, "Retired"),
    )

    SEVERITY_LOW = "low"
    SEVERITY_MEDIUM = "medium"
    SEVERITY_HIGH = "high"
    SEVERITY_CRITICAL = "critical"
    SEVERITY_CHOICES = (
        (SEVERITY_LOW, "Low"),
        (SEVERITY_MEDIUM, "Medium"),
        (SEVERITY_HIGH, "High"),
        (SEVERITY_CRITICAL, "Critical"),
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

    minimum_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=PERCENT_VALIDATORS,
    )
    health_green_threshold = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=PERCENT_VALIDATORS,
    )
    critical_red_count = models.PositiveSmallIntegerField(default=1)
    decline_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=PERCENT_VALIDATORS,
    )
    trend_periods = models.PositiveSmallIntegerField()
    overdue_days = models.PositiveSmallIntegerField()
    review_completion_target = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=PERCENT_VALIDATORS,
    )
    improvement_threshold = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=PERCENT_VALIDATORS,
    )
    department_risk_threshold = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=PERCENT_VALIDATORS,
    )
    manager_workload_threshold = models.PositiveSmallIntegerField()

    overall_kpi_weight = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=PERCENT_VALIDATORS,
    )
    review_completion_weight = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=PERCENT_VALIDATORS,
    )
    bonus_readiness_weight = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=PERCENT_VALIDATORS,
    )
    improvement_weight = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=PERCENT_VALIDATORS,
    )
    risk_control_weight = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=PERCENT_VALIDATORS,
    )

    critical_alert_severity = models.CharField(
        max_length=20,
        choices=SEVERITY_CHOICES,
        default=SEVERITY_CRITICAL,
    )
    red_alert_severity = models.CharField(
        max_length=20,
        choices=SEVERITY_CHOICES,
        default=SEVERITY_HIGH,
    )
    yellow_alert_severity = models.CharField(
        max_length=20,
        choices=SEVERITY_CHOICES,
        default=SEVERITY_MEDIUM,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_kpi_intelligence_rule_sets",
    )
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="published_kpi_intelligence_rule_sets",
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
                name="kpi_intel_rule_version_uniq",
            ),
            models.CheckConstraint(
                condition=Q(version__gte=1),
                name="kpi_intel_rule_version_pos",
            ),
            models.CheckConstraint(
                condition=Q(effective_end__isnull=True)
                | Q(effective_end__gte=F("effective_start")),
                name="kpi_intel_rule_dates",
            ),
            models.CheckConstraint(
                condition=Q(minimum_score__lte=F("health_green_threshold")),
                name="kpi_intel_health_thresholds",
            ),
            models.CheckConstraint(
                condition=Q(trend_periods__gte=2) & Q(trend_periods__lte=24),
                name="kpi_intel_trend_periods",
            ),
            models.CheckConstraint(
                condition=Q(manager_workload_threshold__gte=1),
                name="kpi_intel_manager_workload",
            ),
            models.CheckConstraint(
                condition=Q(critical_red_count__gte=1),
                name="kpi_intel_critical_count",
            ),
            models.CheckConstraint(
                condition=Q(
                    overall_kpi_weight=(
                        PERCENT_MAX
                        - F("review_completion_weight")
                        - F("bonus_readiness_weight")
                        - F("improvement_weight")
                        - F("risk_control_weight")
                    )
                ),
                name="kpi_intel_health_weight_total",
            ),
        ]
        indexes = [
            models.Index(
                fields=("code", "status", "effective_start"),
                name="kpi_intel_rule_lookup_idx",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        percent_fields = (
            "minimum_score",
            "health_green_threshold",
            "decline_percentage",
            "review_completion_target",
            "improvement_threshold",
            "department_risk_threshold",
            "overall_kpi_weight",
            "review_completion_weight",
            "bonus_readiness_weight",
            "improvement_weight",
            "risk_control_weight",
        )
        for field_name in percent_fields:
            value = getattr(self, field_name)
            if value is not None and not PERCENT_MIN <= value <= PERCENT_MAX:
                errors[field_name] = "Value must be between 0.00 and 100.00."
        if (
            self.minimum_score is not None
            and self.health_green_threshold is not None
            and self.minimum_score > self.health_green_threshold
        ):
            errors["health_green_threshold"] = (
                "Green threshold cannot be below the minimum score."
            )
        if self.effective_end and self.effective_end < self.effective_start:
            errors["effective_end"] = "Effective end cannot precede effective start."
        if self.trend_periods and not 2 <= self.trend_periods <= 24:
            errors["trend_periods"] = "Trend periods must be between 2 and 24."

        weights = (
            self.overall_kpi_weight,
            self.review_completion_weight,
            self.bonus_readiness_weight,
            self.improvement_weight,
            self.risk_control_weight,
        )
        if all(value is not None for value in weights):
            if sum(weights, PERCENT_MIN) != PERCENT_MAX:
                errors["overall_kpi_weight"] = (
                    "Company-health component weights must total exactly 100.00."
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
                errors["status"] = "Published intelligence rules are immutable."
            allowed = {previous.status}
            if previous.status == self.STATUS_PUBLISHED:
                allowed.add(self.STATUS_RETIRED)
            if self.status not in allowed:
                errors["status"] = "This intelligence-rule transition is not allowed."
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
            raise ValidationError("Published intelligence rules cannot be deleted.")
        return super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.name} v{self.version}"
