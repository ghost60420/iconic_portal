from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q

from .models_employee import EmployeeProfile
from .models_kpi import KPIItemDefinition
from .models_kpi_assignments import EmployeeKPIRoleAssignment


class ProtectedKPIReviewQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("KPI reviews must be changed through the review service.")

    def delete(self):
        raise ValidationError("KPI reviews cannot be deleted.")

    def bulk_create(self, objs, **kwargs):
        raise ValidationError("KPI reviews must be created through the review service.")

    def bulk_update(self, objs, fields, **kwargs):
        raise ValidationError("KPI reviews must be changed through the review service.")


class KPIReview(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_SUBMITTED = "submitted"
    STATUS_UNDER_REVIEW = "under_review"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_LOCKED = "locked"
    STATUS_CHOICES = (
        (STATUS_DRAFT, "Draft"),
        (STATUS_SUBMITTED, "Submitted"),
        (STATUS_UNDER_REVIEW, "Under Review"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_LOCKED, "Locked"),
    )

    PERIOD_MONTHLY = "monthly"
    PERIOD_QUARTERLY = "quarterly"
    PERIOD_ANNUAL = "annual"
    PERIOD_CHOICES = (
        (PERIOD_MONTHLY, "Monthly"),
        (PERIOD_QUARTERLY, "Quarterly"),
        (PERIOD_ANNUAL, "Annual"),
    )

    employee = models.ForeignKey(
        EmployeeProfile,
        on_delete=models.PROTECT,
        related_name="kpi_reviews",
    )
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="managed_kpi_reviews",
    )
    period_type = models.CharField(max_length=20, choices=PERIOD_CHOICES)
    period_start = models.DateField()
    period_end = models.DateField()
    review_date = models.DateField()
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
        db_index=True,
    )
    manager_comment = models.TextField(blank=True, default="")
    approval_comment = models.TextField(blank=True, default="")
    rejection_comment = models.TextField(blank=True, default="")

    definition_snapshot = models.JSONField(default=dict)
    calculation_snapshot = models.JSONField(default=dict, blank=True)
    approved_snapshot = models.JSONField(default=dict, blank=True)
    snapshot_digest = models.CharField(max_length=64, blank=True, default="")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_kpi_reviews",
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="submitted_kpi_reviews",
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    review_started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="started_kpi_reviews",
    )
    review_started_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_kpi_reviews",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="rejected_kpi_reviews",
    )
    rejected_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="locked_kpi_reviews",
    )
    locked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ProtectedKPIReviewQuerySet.as_manager()

    class Meta:
        ordering = ("-period_end", "-review_date", "-id")
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["employee", "period_type", "period_start", "period_end"],
                name="kpi_review_employee_period_uniq",
            ),
            models.CheckConstraint(
                condition=Q(period_end__gte=F("period_start")),
                name="kpi_review_period_ordered",
            ),
            models.CheckConstraint(
                condition=Q(review_date__gte=F("period_start"))
                & Q(review_date__lte=F("period_end")),
                name="kpi_review_date_in_period",
            ),
        ]
        indexes = [
            models.Index(
                fields=["employee", "status", "-period_end"],
                name="kpi_review_employee_status_idx",
            ),
            models.Index(
                fields=["manager", "status", "-period_end"],
                name="kpi_review_manager_status_idx",
            ),
            models.Index(
                fields=["status", "-submitted_at"],
                name="kpi_review_queue_idx",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.period_start and self.period_end:
            if self.period_end < self.period_start:
                errors["period_end"] = "Period end cannot be before period start."
            if self.review_date and not (
                self.period_start <= self.review_date <= self.period_end
            ):
                errors["review_date"] = "Review date must fall inside the period."
        if not self.definition_snapshot:
            errors["definition_snapshot"] = "A versioned review definition is required."
        if (
            self.approved_snapshot
            and self.status not in {self.STATUS_APPROVED, self.STATUS_LOCKED}
        ):
            errors["approved_snapshot"] = (
                "Approved snapshots are only valid for Approved or Locked reviews."
            )
        if self.status in {self.STATUS_APPROVED, self.STATUS_LOCKED}:
            if not self.approved_snapshot or not self.snapshot_digest:
                errors["approved_snapshot"] = (
                    "Approved and locked reviews require an immutable snapshot."
                )
            if not self.approved_by_id or not self.approved_at:
                errors["approved_by"] = "Approved review identity and time are required."
        if self.status == self.STATUS_LOCKED:
            if not self.locked_by_id or not self.locked_at:
                errors["locked_by"] = "Locked review identity and time are required."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        workflow_authorized = kwargs.pop("workflow_authorized", False)
        previous = None
        if self.pk:
            previous = type(self)._base_manager.filter(pk=self.pk).first()
        if previous:
            if previous.definition_snapshot != self.definition_snapshot:
                raise ValidationError("KPI review definitions are immutable.")
            if previous.approved_snapshot:
                if previous.approved_snapshot != self.approved_snapshot:
                    raise ValidationError("Approved KPI snapshots are immutable.")
                if previous.snapshot_digest != self.snapshot_digest:
                    raise ValidationError("Approved KPI snapshot digests are immutable.")
            if previous.status == self.STATUS_LOCKED:
                raise ValidationError("Locked KPI reviews are immutable.")
            if previous.status == self.STATUS_APPROVED:
                allowed_lock = (
                    workflow_authorized
                    and self.status == self.STATUS_LOCKED
                    and self.locked_by_id
                    and self.locked_at
                )
                if not allowed_lock:
                    raise ValidationError("Approved KPI reviews are read only.")
                lock_fields = {"status", "locked_by_id", "locked_at", "updated_at"}
                changed_fields = {
                    field.attname
                    for field in self._meta.concrete_fields
                    if field.attname not in lock_fields
                    and getattr(previous, field.attname) != getattr(self, field.attname)
                }
                if changed_fields:
                    raise ValidationError(
                        "Locking cannot change approved KPI review data."
                    )
            if previous.status != self.status and not workflow_authorized:
                raise ValidationError(
                    "KPI review status must be changed through the workflow service."
                )
        elif self.status != self.STATUS_DRAFT:
            raise ValidationError("New KPI reviews must begin in Draft.")

        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("KPI reviews cannot be deleted.")

    @property
    def is_read_only(self):
        return self.status in {self.STATUS_APPROVED, self.STATUS_LOCKED}

    @property
    def result_snapshot(self):
        if self.status in {self.STATUS_APPROVED, self.STATUS_LOCKED}:
            return (self.approved_snapshot or {}).get("result", {})
        return self.calculation_snapshot or {}

    def __str__(self):
        return (
            f"{self.employee} {self.get_period_type_display()} "
            f"{self.period_start:%Y-%m-%d}"
        )


class ProtectedKPIReviewItemQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("KPI review entries must use the review service.")

    def delete(self):
        raise ValidationError("KPI review entries cannot be deleted.")

    def bulk_create(self, objs, **kwargs):
        raise ValidationError("KPI review entries must use the review service.")

    def bulk_update(self, objs, fields, **kwargs):
        raise ValidationError("KPI review entries must use the review service.")


class KPIReviewItemEntry(models.Model):
    review = models.ForeignKey(
        KPIReview,
        on_delete=models.PROTECT,
        related_name="item_entries",
    )
    assignment = models.ForeignKey(
        EmployeeKPIRoleAssignment,
        on_delete=models.PROTECT,
        related_name="kpi_review_entries",
    )
    kpi_item = models.ForeignKey(
        KPIItemDefinition,
        on_delete=models.PROTECT,
        related_name="review_entries",
    )
    actual_value = models.DecimalField(
        max_digits=20,
        decimal_places=6,
        null=True,
        blank=True,
    )
    critical_red = models.BooleanField(default=False)
    critical_reason = models.TextField(blank=True, default="")
    critical_trigger = models.CharField(max_length=160, blank=True, default="")
    item_comment = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ProtectedKPIReviewItemQuerySet.as_manager()

    class Meta:
        ordering = ("assignment_id", "kpi_item__sort_order", "kpi_item_id")
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["review", "assignment", "kpi_item"],
                name="kpi_review_item_assignment_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=["review", "assignment", "kpi_item"],
                name="kpi_review_item_lookup_idx",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.review_id and self.assignment_id:
            if self.assignment.employee_id != self.review.employee_id:
                errors["assignment"] = "Assignment employee must match the review."
        if self.kpi_item_id and self.assignment_id:
            if (
                self.kpi_item.template_version.template_id
                != self.assignment.kpi_template_id
            ):
                errors["kpi_item"] = "KPI item template must match the assignment."
        if self.critical_red:
            if not self.critical_reason.strip():
                errors["critical_reason"] = "Critical Red requires a reason."
            if not self.critical_trigger.strip():
                errors["critical_trigger"] = "Critical Red requires a trigger."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        internal_create = kwargs.pop("internal_create", False)
        service_authorized = kwargs.pop("service_authorized", False)
        if self.review_id:
            status = KPIReview._base_manager.filter(pk=self.review_id).values_list(
                "status", flat=True
            ).first()
            if status != KPIReview.STATUS_DRAFT:
                raise ValidationError("Only Draft KPI review entries can be changed.")
        if self._state.adding and not internal_create:
            raise ValidationError("KPI review entries must use the review service.")
        if not self._state.adding and not service_authorized:
            raise ValidationError("KPI review entries must use the review service.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("KPI review entries cannot be deleted.")

    def __str__(self):
        return f"Review {self.review_id}: {self.kpi_item}"


class ProtectedKPIReviewTransitionQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("KPI review history is immutable.")

    def delete(self):
        raise ValidationError("KPI review history is immutable.")

    def bulk_update(self, objs, fields, **kwargs):
        raise ValidationError("KPI review history is immutable.")

    def bulk_create(self, objs, **kwargs):
        raise ValidationError("KPI review history must use the review service.")


class KPIReviewTransition(models.Model):
    ACTION_CREATED = "created"
    ACTION_SAVED = "saved"
    ACTION_SUBMITTED = "submitted"
    ACTION_REVIEW_STARTED = "review_started"
    ACTION_APPROVED = "approved"
    ACTION_REJECTED = "rejected"
    ACTION_RETURNED_TO_DRAFT = "returned_to_draft"
    ACTION_LOCKED = "locked"
    ACTION_CHOICES = (
        (ACTION_CREATED, "Created"),
        (ACTION_SAVED, "Draft Saved"),
        (ACTION_SUBMITTED, "Submitted"),
        (ACTION_REVIEW_STARTED, "Review Started"),
        (ACTION_APPROVED, "Approved"),
        (ACTION_REJECTED, "Rejected"),
        (ACTION_RETURNED_TO_DRAFT, "Returned to Draft"),
        (ACTION_LOCKED, "Locked"),
    )

    review = models.ForeignKey(
        KPIReview,
        on_delete=models.PROTECT,
        related_name="transitions",
    )
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    from_status = models.CharField(
        max_length=20,
        choices=KPIReview.STATUS_CHOICES,
        blank=True,
        default="",
    )
    to_status = models.CharField(max_length=20, choices=KPIReview.STATUS_CHOICES)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="kpi_review_transitions",
    )
    comment = models.TextField(blank=True, default="")
    snapshot_digest = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ProtectedKPIReviewTransitionQuerySet.as_manager()

    class Meta:
        ordering = ("created_at", "id")
        default_permissions = ()
        indexes = [
            models.Index(
                fields=["review", "created_at"],
                name="kpi_review_transition_idx",
            ),
            models.Index(
                fields=["action", "created_at"],
                name="kpi_review_action_idx",
            ),
        ]

    def save(self, *args, **kwargs):
        internal_create = kwargs.pop("internal_create", False)
        if not internal_create:
            raise ValidationError("KPI review history must use the review service.")
        if not self._state.adding or self.pk:
            raise ValidationError("KPI review history is immutable.")
        kwargs["force_insert"] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("KPI review history is immutable.")

    def __str__(self):
        return f"Review {self.review_id}: {self.action}"
