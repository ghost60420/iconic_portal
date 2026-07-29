from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q

from .models_employee import EmployeeProfile
from .models_kpi_reviews import KPIReview


PERCENT_MIN = Decimal("0.00")
PERCENT_MAX = Decimal("100.00")
PERCENT_VALIDATORS = [
    MinValueValidator(PERCENT_MIN),
]


class KPIBonusWeightProfile(models.Model):
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
    team_scope_code = models.SlugField(max_length=80)
    team_scope_name = models.CharField(max_length=140)
    individual_weight = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=PERCENT_VALIDATORS,
    )
    team_weight = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=PERCENT_VALIDATORS,
    )
    company_weight = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=PERCENT_VALIDATORS,
    )
    effective_start = models.DateField()
    effective_end = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_kpi_bonus_weight_profiles",
    )
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="published_kpi_bonus_weight_profiles",
    )
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("code", "-version")
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["code", "version"],
                name="kpi_bonus_weight_version_uniq",
            ),
            models.CheckConstraint(
                condition=Q(version__gte=1),
                name="kpi_bonus_weight_version_pos",
            ),
            models.CheckConstraint(
                condition=(
                    Q(individual_weight__gte=0)
                    & Q(individual_weight__lte=100)
                    & Q(team_weight__gte=0)
                    & Q(team_weight__lte=100)
                    & Q(company_weight__gte=0)
                    & Q(company_weight__lte=100)
                ),
                name="kpi_bonus_weight_ranges",
            ),
            models.CheckConstraint(
                condition=Q(
                    individual_weight=(
                        PERCENT_MAX - F("team_weight") - F("company_weight")
                    )
                ),
                name="kpi_bonus_weight_total",
            ),
            models.CheckConstraint(
                condition=Q(effective_end__isnull=True)
                | Q(effective_end__gte=F("effective_start")),
                name="kpi_bonus_weight_dates",
            ),
        ]
        indexes = [
            models.Index(
                fields=["code", "status", "effective_start"],
                name="kpi_bonus_weight_lookup_idx",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        total = (
            (self.individual_weight or PERCENT_MIN)
            + (self.team_weight or PERCENT_MIN)
            + (self.company_weight or PERCENT_MIN)
        )
        if total != PERCENT_MAX:
            errors["individual_weight"] = (
                "Individual, team, and company weights must total exactly 100.00."
            )
        for field in ("individual_weight", "team_weight", "company_weight"):
            value = getattr(self, field)
            if value is not None and not PERCENT_MIN <= value <= PERCENT_MAX:
                errors[field] = "Bonus weights must be between 0.00 and 100.00."
        if self.effective_end and self.effective_end < self.effective_start:
            errors["effective_end"] = "Effective end cannot precede effective start."

        previous = type(self).objects.filter(pk=self.pk).first() if self.pk else None
        if previous and previous.status in {
            self.STATUS_PUBLISHED,
            self.STATUS_RETIRED,
        }:
            immutable_fields = (
                "code",
                "name",
                "version",
                "team_scope_code",
                "team_scope_name",
                "individual_weight",
                "team_weight",
                "company_weight",
                "effective_start",
                "effective_end",
                "created_by_id",
                "published_by_id",
                "published_at",
            )
            if any(
                getattr(previous, field) != getattr(self, field)
                for field in immutable_fields
            ):
                errors["status"] = "Published bonus weight profiles are immutable."
            allowed = {previous.status}
            if previous.status == self.STATUS_PUBLISHED:
                allowed.add(self.STATUS_RETIRED)
            if self.status not in allowed:
                errors["status"] = "This weight-profile transition is not allowed."
        if self.status == self.STATUS_PUBLISHED and not self.published_by_id:
            errors["published_by"] = "Published by is required."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        from django.utils import timezone

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
            raise ValidationError("Published bonus weight profiles cannot be deleted.")
        return super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.name} v{self.version}"


class KPIBonusRuleSet(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_PUBLISHED = "published"
    STATUS_RETIRED = "retired"
    STATUS_CHOICES = (
        (STATUS_DRAFT, "Draft"),
        (STATUS_PUBLISHED, "Published"),
        (STATUS_RETIRED, "Retired"),
    )

    CRITICAL_BLOCK = "block"
    CRITICAL_NOT_ELIGIBLE = "not_eligible"
    CRITICAL_ALLOW = "allow"
    CRITICAL_CHOICES = (
        (CRITICAL_BLOCK, "Block"),
        (CRITICAL_NOT_ELIGIBLE, "Not Eligible"),
        (CRITICAL_ALLOW, "Allow"),
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
    weight_profile = models.ForeignKey(
        KPIBonusWeightProfile,
        on_delete=models.PROTECT,
        related_name="rule_sets",
    )
    bonus_enabled = models.BooleanField(default=True)
    minimum_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=PERCENT_VALIDATORS,
    )
    attendance_multiplier_default = models.DecimalField(
        max_digits=7,
        decimal_places=4,
        default=Decimal("1.0000"),
        validators=[MinValueValidator(Decimal("0.0000"))],
    )
    attendance_multiplier_min = models.DecimalField(
        max_digits=7,
        decimal_places=4,
        default=Decimal("0.0000"),
        validators=[MinValueValidator(Decimal("0.0000"))],
    )
    attendance_multiplier_max = models.DecimalField(
        max_digits=7,
        decimal_places=4,
        default=Decimal("1.0000"),
        validators=[MinValueValidator(Decimal("0.0000"))],
    )
    critical_red_behavior = models.CharField(
        max_length=20,
        choices=CRITICAL_CHOICES,
        default=CRITICAL_BLOCK,
    )
    bonus_floor = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    bonus_cap = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    currency = models.CharField(max_length=3)
    eligible_employee_statuses = models.JSONField(default=list)
    approval_required = models.BooleanField(default=True)
    effective_start = models.DateField()
    effective_end = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_kpi_bonus_rule_sets",
    )
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="published_kpi_bonus_rule_sets",
    )
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("code", "-version")
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["code", "version"],
                name="kpi_bonus_rule_version_uniq",
            ),
            models.CheckConstraint(
                condition=Q(version__gte=1),
                name="kpi_bonus_rule_version_pos",
            ),
            models.CheckConstraint(
                condition=Q(minimum_score__gte=0) & Q(minimum_score__lte=100),
                name="kpi_bonus_rule_min_score",
            ),
            models.CheckConstraint(
                condition=(
                    Q(attendance_multiplier_min__gte=0)
                    & Q(
                        attendance_multiplier_default__gte=F(
                            "attendance_multiplier_min"
                        )
                    )
                    & Q(
                        attendance_multiplier_default__lte=F(
                            "attendance_multiplier_max"
                        )
                    )
                ),
                name="kpi_bonus_rule_attendance",
            ),
            models.CheckConstraint(
                condition=Q(bonus_cap__isnull=True)
                | Q(bonus_cap__gte=F("bonus_floor")),
                name="kpi_bonus_rule_payout_range",
            ),
            models.CheckConstraint(
                condition=Q(effective_end__isnull=True)
                | Q(effective_end__gte=F("effective_start")),
                name="kpi_bonus_rule_dates",
            ),
        ]
        indexes = [
            models.Index(
                fields=["code", "status", "effective_start"],
                name="kpi_bonus_rule_lookup_idx",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        self.currency = (self.currency or "").strip().upper()
        if len(self.currency) != 3 or not self.currency.isalpha():
            errors["currency"] = "Currency must be a three-letter code."
        valid_statuses = {value for value, _label in EmployeeProfile.STATUS_CHOICES}
        configured_statuses = self.eligible_employee_statuses
        if (
            not isinstance(configured_statuses, list)
            or not configured_statuses
            or any(status not in valid_statuses for status in configured_statuses)
        ):
            errors["eligible_employee_statuses"] = (
                "Configure at least one valid employee status."
            )
        if self.minimum_score is not None and not PERCENT_MIN <= self.minimum_score <= PERCENT_MAX:
            errors["minimum_score"] = "Minimum score must be between 0 and 100."
        if not (
            self.attendance_multiplier_min
            <= self.attendance_multiplier_default
            <= self.attendance_multiplier_max
        ):
            errors["attendance_multiplier_default"] = (
                "Default attendance multiplier must fall inside its configured range."
            )
        if self.bonus_cap is not None and self.bonus_cap < self.bonus_floor:
            errors["bonus_cap"] = "Bonus cap cannot be below the bonus floor."
        if self.effective_end and self.effective_end < self.effective_start:
            errors["effective_end"] = "Effective end cannot precede effective start."
        if (
            self.status == self.STATUS_PUBLISHED
            and self.weight_profile.status != KPIBonusWeightProfile.STATUS_PUBLISHED
        ):
            errors["weight_profile"] = (
                "Published rules require a published weight profile."
            )

        previous = type(self).objects.filter(pk=self.pk).first() if self.pk else None
        if previous and previous.status in {
            self.STATUS_PUBLISHED,
            self.STATUS_RETIRED,
        }:
            immutable_fields = (
                "code",
                "name",
                "version",
                "weight_profile_id",
                "bonus_enabled",
                "minimum_score",
                "attendance_multiplier_default",
                "attendance_multiplier_min",
                "attendance_multiplier_max",
                "critical_red_behavior",
                "bonus_floor",
                "bonus_cap",
                "currency",
                "eligible_employee_statuses",
                "approval_required",
                "effective_start",
                "effective_end",
                "created_by_id",
                "published_by_id",
                "published_at",
            )
            if any(
                getattr(previous, field) != getattr(self, field)
                for field in immutable_fields
            ):
                errors["status"] = "Published bonus rule sets are immutable."
            allowed = {previous.status}
            if previous.status == self.STATUS_PUBLISHED:
                allowed.add(self.STATUS_RETIRED)
            if self.status not in allowed:
                errors["status"] = "This bonus-rule transition is not allowed."
        if self.status == self.STATUS_PUBLISHED and not self.published_by_id:
            errors["published_by"] = "Published by is required."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        from django.utils import timezone

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
            raise ValidationError("Published bonus rule sets cannot be deleted.")
        return super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.name} v{self.version}"


class ProtectedKPIBonusCalculationQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("KPI bonus calculations are immutable.")

    def delete(self):
        raise ValidationError("KPI bonus calculations cannot be deleted.")

    def bulk_create(self, objs, **kwargs):
        raise ValidationError("KPI bonus calculations must use the bonus service.")

    def bulk_update(self, objs, fields, **kwargs):
        raise ValidationError("KPI bonus calculations are immutable.")


class KPIBonusCalculation(models.Model):
    ELIGIBLE = "eligible"
    NOT_ELIGIBLE = "not_eligible"
    BLOCKED = "blocked"
    STATUS_CHOICES = (
        (ELIGIBLE, "Eligible"),
        (NOT_ELIGIBLE, "Not Eligible"),
        (BLOCKED, "Blocked"),
    )

    review = models.OneToOneField(
        KPIReview,
        on_delete=models.PROTECT,
        related_name="bonus_calculation",
    )
    team_review = models.ForeignKey(
        KPIReview,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="team_bonus_source_calculations",
    )
    company_review = models.ForeignKey(
        KPIReview,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="company_bonus_source_calculations",
    )
    rule_set = models.ForeignKey(
        KPIBonusRuleSet,
        on_delete=models.PROTECT,
        related_name="calculations",
    )
    weight_profile = models.ForeignKey(
        KPIBonusWeightProfile,
        on_delete=models.PROTECT,
        related_name="calculations",
    )
    employee = models.ForeignKey(
        EmployeeProfile,
        on_delete=models.PROTECT,
        related_name="kpi_bonus_calculations",
    )
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="managed_kpi_bonus_calculations",
    )
    department_code = models.CharField(max_length=80, blank=True, default="")
    department_name = models.CharField(max_length=140, blank=True, default="")
    team_scope_code = models.CharField(max_length=80)
    team_scope_name = models.CharField(max_length=140)
    review_date = models.DateField()
    eligibility_status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        db_index=True,
    )
    reason_codes = models.JSONField(default=list, blank=True)
    formula_version = models.CharField(max_length=80)
    bonus_rule_version = models.PositiveIntegerField()
    calculation_version = models.CharField(max_length=80)
    review_snapshot_digest = models.CharField(max_length=64)
    result_snapshot = models.JSONField()
    result_digest = models.CharField(max_length=64)
    calculated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_kpi_bonus_calculations",
    )
    calculated_at = models.DateTimeField()

    objects = ProtectedKPIBonusCalculationQuerySet.as_manager()

    class Meta:
        ordering = ("-review_date", "-id")
        default_permissions = ()
        constraints = [
            models.CheckConstraint(
                condition=Q(bonus_rule_version__gte=1),
                name="kpi_bonus_calc_rule_version_pos",
            ),
        ]
        indexes = [
            models.Index(
                fields=["employee", "eligibility_status", "-review_date"],
                name="kpi_bonus_calc_employee_idx",
            ),
            models.Index(
                fields=["rule_set", "-review_date"],
                name="kpi_bonus_calc_rule_idx",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.review_id:
            review = self.review
            if review.status not in {
                KPIReview.STATUS_APPROVED,
                KPIReview.STATUS_LOCKED,
            }:
                errors["review"] = "Stored bonus calculations require an approved review."
            if review.employee_id != self.employee_id:
                errors["employee"] = "Bonus employee must match the source review."
            if review.snapshot_digest != self.review_snapshot_digest:
                errors["review_snapshot_digest"] = (
                    "Review snapshot digest must match the approved review."
                )
        if self.rule_set_id and self.weight_profile_id:
            if self.rule_set.weight_profile_id != self.weight_profile_id:
                errors["weight_profile"] = (
                    "Weight profile must match the versioned rule set."
                )
        if not self.result_snapshot or not self.result_digest:
            errors["result_snapshot"] = "An immutable bonus result snapshot is required."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        internal_create = kwargs.pop("internal_create", False)
        if not internal_create:
            raise ValidationError("KPI bonus calculations must use the bonus service.")
        if not self._state.adding or self.pk:
            raise ValidationError("KPI bonus calculations are immutable.")
        self.full_clean()
        kwargs["force_insert"] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("KPI bonus calculations cannot be deleted.")

    def __str__(self):
        return f"Review {self.review_id} bonus ({self.eligibility_status})"
