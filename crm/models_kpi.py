from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q, Sum
from django.utils import timezone


PERCENT_MIN = Decimal("0.00")
PERCENT_MAX = Decimal("100.00")
PERCENT_STEP = Decimal("0.01")
PERCENT_VALIDATORS = [
    MinValueValidator(PERCENT_MIN),
    MaxValueValidator(PERCENT_MAX),
]


def _validate_score_ranges(instance):
    ranges = (
        ("Red", instance.red_min, instance.red_max),
        ("Yellow", instance.yellow_min, instance.yellow_max),
        ("Green", instance.green_min, instance.green_max),
    )
    errors = {}
    for label, minimum, maximum in ranges:
        if minimum is None or maximum is None:
            continue
        if minimum < PERCENT_MIN or maximum > PERCENT_MAX or minimum > maximum:
            errors[f"{label.lower()}_min"] = (
                f"{label} range must stay between 0 and 100 with minimum "
                "less than or equal to maximum."
            )
    if not errors and instance.red_min != PERCENT_MIN:
        errors["red_min"] = "Red range must start at 0.00."
    if not errors and instance.green_max != PERCENT_MAX:
        errors["green_max"] = "Green range must end at 100.00."
    if not errors and instance.yellow_min != instance.red_max + PERCENT_STEP:
        errors["yellow_min"] = "Yellow range must begin 0.01 above the Red range."
    if not errors and instance.green_min != instance.yellow_max + PERCENT_STEP:
        errors["green_min"] = "Green range must begin 0.01 above the Yellow range."
    if errors:
        raise ValidationError(errors)


class KPIRoleTemplate(models.Model):
    code = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=140, unique=True)
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_kpi_role_templates",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        indexes = [
            models.Index(
                fields=["is_active", "name"],
                name="kpi_role_active_name_idx",
            ),
        ]

    def clean(self):
        super().clean()
        self.code = (self.code or "").strip().lower()
        self.name = " ".join((self.name or "").split())
        if not self.code:
            raise ValidationError({"code": "Template code is required."})
        if not self.name:
            raise ValidationError({"name": "Template name is required."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class KPITemplateVersion(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_PUBLISHED = "published"
    STATUS_RETIRED = "retired"
    STATUS_CHOICES = (
        (STATUS_DRAFT, "Draft"),
        (STATUS_PUBLISHED, "Published"),
        (STATUS_RETIRED, "Retired"),
    )

    template = models.ForeignKey(
        KPIRoleTemplate,
        on_delete=models.PROTECT,
        related_name="versions",
    )
    version = models.PositiveIntegerField()
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
        db_index=True,
    )
    effective_start = models.DateField(null=True, blank=True)
    effective_end = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_kpi_template_versions",
    )
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="published_kpi_template_versions",
    )
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("template__name", "-version")
        constraints = [
            models.UniqueConstraint(
                fields=["template", "version"],
                name="kpi_template_version_uniq",
            ),
            models.CheckConstraint(
                condition=Q(version__gte=1),
                name="kpi_template_version_pos",
            ),
        ]
        indexes = [
            models.Index(
                fields=["template", "status", "effective_start"],
                name="kpi_version_lookup_idx",
            ),
        ]

    @property
    def active_weight_total(self):
        return (
            self.items.filter(is_active=True).aggregate(total=Sum("weight"))[
                "total"
            ]
            or Decimal("0.00")
        )

    def clean(self):
        super().clean()
        errors = {}
        if self.effective_start and self.effective_end:
            if self.effective_end < self.effective_start:
                errors["effective_end"] = (
                    "Effective end cannot be before effective start."
                )

        previous = None
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).first()
        if previous and previous.status in {
            self.STATUS_PUBLISHED,
            self.STATUS_RETIRED,
        }:
            immutable_fields = (
                "template_id",
                "version",
                "effective_start",
                "effective_end",
                "notes",
                "created_by_id",
                "published_by_id",
                "published_at",
            )
            if any(
                getattr(previous, field) != getattr(self, field)
                for field in immutable_fields
            ):
                errors["status"] = (
                    "Published and retired template versions are immutable."
                )
            allowed_statuses = {previous.status}
            if previous.status == self.STATUS_PUBLISHED:
                allowed_statuses.add(self.STATUS_RETIRED)
            if self.status not in allowed_statuses:
                errors["status"] = "This template version status transition is not allowed."

        if self.status == self.STATUS_PUBLISHED:
            if not self.pk:
                errors["status"] = (
                    "Save the draft and its KPI items before publishing."
                )
            elif self.active_weight_total != PERCENT_MAX:
                errors["status"] = (
                    "Active KPI item weights must total exactly 100.00 percent."
                )
            if not self.published_by_id:
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
            raise ValidationError(
                "Published and retired template versions cannot be deleted."
            )
        return super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.template.name} v{self.version}"


class KPIItemDefinition(models.Model):
    FREQUENCY_DAILY = "daily"
    FREQUENCY_WEEKLY = "weekly"
    FREQUENCY_MONTHLY = "monthly"
    FREQUENCY_QUARTERLY = "quarterly"
    FREQUENCY_ANNUAL = "annual"
    FREQUENCY_CHOICES = (
        (FREQUENCY_DAILY, "Daily"),
        (FREQUENCY_WEEKLY, "Weekly"),
        (FREQUENCY_MONTHLY, "Monthly"),
        (FREQUENCY_QUARTERLY, "Quarterly"),
        (FREQUENCY_ANNUAL, "Annual"),
    )

    template_version = models.ForeignKey(
        KPITemplateVersion,
        on_delete=models.CASCADE,
        related_name="items",
    )
    name = models.CharField(max_length=180)
    description = models.TextField(blank=True, default="")
    purpose = models.TextField(blank=True, default="")
    measurement_method = models.TextField(blank=True, default="")
    target = models.TextField(blank=True, default="")
    weight = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=PERCENT_VALIDATORS,
    )
    review_frequency = models.CharField(
        max_length=20,
        choices=FREQUENCY_CHOICES,
        default=FREQUENCY_MONTHLY,
    )
    data_source = models.CharField(max_length=240, blank=True, default="")
    green_min = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("85.00"),
        validators=PERCENT_VALIDATORS,
    )
    green_max = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=PERCENT_MAX,
        validators=PERCENT_VALIDATORS,
    )
    yellow_min = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("70.00"),
        validators=PERCENT_VALIDATORS,
    )
    yellow_max = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("84.99"),
        validators=PERCENT_VALIDATORS,
    )
    red_min = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=PERCENT_MIN,
        validators=PERCENT_VALIDATORS,
    )
    red_max = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("69.99"),
        validators=PERCENT_VALIDATORS,
    )
    manager_approval_required = models.BooleanField(default=True)
    evidence_required = models.BooleanField(default=False)
    bonus_eligible = models.BooleanField(default=True)
    critical_failure_rule = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True, db_index=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("sort_order", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["template_version", "name"],
                name="kpi_item_version_name_uniq",
            ),
            models.CheckConstraint(
                condition=Q(weight__gte=0) & Q(weight__lte=100),
                name="kpi_item_weight_range",
            ),
            models.CheckConstraint(
                condition=(
                    Q(red_min=PERCENT_MIN)
                    & Q(red_min__lte=F("red_max"))
                    & Q(yellow_min=F("red_max") + PERCENT_STEP)
                    & Q(yellow_min__lte=F("yellow_max"))
                    & Q(green_min=F("yellow_max") + PERCENT_STEP)
                    & Q(green_min__lte=F("green_max"))
                    & Q(green_max=PERCENT_MAX)
                ),
                name="kpi_item_ranges_ordered",
            ),
        ]
        indexes = [
            models.Index(
                fields=["template_version", "is_active", "sort_order"],
                name="kpi_item_lookup_idx",
            ),
        ]

    def clean(self):
        super().clean()
        self.name = " ".join((self.name or "").split())
        errors = {}
        if not self.name:
            errors["name"] = "KPI item name is required."
        if self.template_version_id:
            persisted_status = (
                KPITemplateVersion.objects.filter(pk=self.template_version_id)
                .values_list("status", flat=True)
                .first()
            )
            if persisted_status != KPITemplateVersion.STATUS_DRAFT:
                errors["template_version"] = (
                    "KPI items on published or retired versions are immutable."
                )
        if errors:
            raise ValidationError(errors)
        _validate_score_ranges(self)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        persisted_status = (
            KPITemplateVersion.objects.filter(pk=self.template_version_id)
            .values_list("status", flat=True)
            .first()
        )
        if persisted_status != KPITemplateVersion.STATUS_DRAFT:
            raise ValidationError(
                "KPI items on published or retired versions cannot be deleted."
            )
        return super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.template_version}: {self.name}"


class KPISettings(models.Model):
    version = models.PositiveIntegerField(unique=True)
    is_active = models.BooleanField(default=False, db_index=True)
    green_min = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("85.00"),
        validators=PERCENT_VALIDATORS,
    )
    green_max = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=PERCENT_MAX,
        validators=PERCENT_VALIDATORS,
    )
    yellow_min = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("70.00"),
        validators=PERCENT_VALIDATORS,
    )
    yellow_max = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("84.99"),
        validators=PERCENT_VALIDATORS,
    )
    red_min = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=PERCENT_MIN,
        validators=PERCENT_VALIDATORS,
    )
    red_max = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("69.99"),
        validators=PERCENT_VALIDATORS,
    )
    individual_bonus_weight = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("60.00"),
        validators=PERCENT_VALIDATORS,
    )
    team_bonus_weight = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("25.00"),
        validators=PERCENT_VALIDATORS,
    )
    company_bonus_weight = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("15.00"),
        validators=PERCENT_VALIDATORS,
    )
    notes = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_kpi_settings_versions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-version",)
        constraints = [
            models.CheckConstraint(
                condition=Q(version__gte=1),
                name="kpi_settings_version_pos",
            ),
            models.UniqueConstraint(
                fields=["is_active"],
                condition=Q(is_active=True),
                name="kpi_settings_one_active",
            ),
            models.CheckConstraint(
                condition=(
                    Q(red_min=PERCENT_MIN)
                    & Q(red_min__lte=F("red_max"))
                    & Q(yellow_min=F("red_max") + PERCENT_STEP)
                    & Q(yellow_min__lte=F("yellow_max"))
                    & Q(green_min=F("yellow_max") + PERCENT_STEP)
                    & Q(green_min__lte=F("green_max"))
                    & Q(green_max=PERCENT_MAX)
                ),
                name="kpi_settings_ranges_ordered",
            ),
            models.CheckConstraint(
                condition=(
                    Q(individual_bonus_weight__gte=0)
                    & Q(individual_bonus_weight__lte=100)
                    & Q(team_bonus_weight__gte=0)
                    & Q(team_bonus_weight__lte=100)
                    & Q(company_bonus_weight__gte=0)
                    & Q(company_bonus_weight__lte=100)
                ),
                name="kpi_settings_bonus_ranges",
            ),
            models.CheckConstraint(
                condition=Q(
                    individual_bonus_weight=(
                        PERCENT_MAX
                        - F("team_bonus_weight")
                        - F("company_bonus_weight")
                    )
                ),
                name="kpi_settings_bonus_total",
            ),
        ]

    def clean(self):
        super().clean()
        _validate_score_ranges(self)
        total = (
            (self.individual_bonus_weight or Decimal("0.00"))
            + (self.team_bonus_weight or Decimal("0.00"))
            + (self.company_bonus_weight or Decimal("0.00"))
        )
        if total != PERCENT_MAX:
            raise ValidationError(
                {
                    "individual_bonus_weight": (
                        "Individual, team, and company bonus weights must total "
                        "exactly 100.00 percent."
                    )
                }
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        suffix = " (active)" if self.is_active else ""
        return f"KPI Settings v{self.version}{suffix}"
