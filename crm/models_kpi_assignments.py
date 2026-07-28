from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.db.models import F, Q
from django.utils import timezone

from .models_employee import EmployeeProfile
from .models_kpi import KPIRoleTemplate


ASSIGNMENT_WEIGHT_MIN = Decimal("0.01")
ASSIGNMENT_WEIGHT_MAX = Decimal("100.00")


class ProtectedAssignmentQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError(
            "KPI assignments must be changed through the assignment service."
        )

    def delete(self):
        raise ValidationError(
            "KPI assignments cannot be deleted; deactivate or archive them."
        )

    def bulk_create(self, objs, **kwargs):
        raise ValidationError(
            "KPI assignments must be created through the assignment service."
        )

    def bulk_update(self, objs, fields, **kwargs):
        raise ValidationError(
            "KPI assignments must be changed through the assignment service."
        )


class EmployeeKPIRoleAssignment(models.Model):
    employee = models.ForeignKey(
        EmployeeProfile,
        on_delete=models.PROTECT,
        related_name="kpi_role_assignments",
    )
    kpi_template = models.ForeignKey(
        KPIRoleTemplate,
        on_delete=models.PROTECT,
        related_name="employee_assignments",
    )
    role_weight = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[
            MinValueValidator(ASSIGNMENT_WEIGHT_MIN),
            MaxValueValidator(ASSIGNMENT_WEIGHT_MAX),
        ],
    )
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="managed_kpi_role_assignments",
    )
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    bonus_eligible = models.BooleanField(default=True, db_index=True)
    notes = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_kpi_role_assignments",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="updated_kpi_role_assignments",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    assignment_version = models.PositiveIntegerField(default=1)
    is_archived = models.BooleanField(default=False, db_index=True)

    objects = ProtectedAssignmentQuerySet.as_manager()

    class Meta:
        ordering = ("employee_id", "start_date", "kpi_template__name", "id")
        default_permissions = ()
        constraints = [
            models.CheckConstraint(
                condition=Q(role_weight__gt=0) & Q(role_weight__lte=100),
                name="kpi_asn_weight_range",
            ),
            models.CheckConstraint(
                condition=Q(end_date__isnull=True) | Q(end_date__gte=F("start_date")),
                name="kpi_asn_dates_ordered",
            ),
            models.CheckConstraint(
                condition=Q(is_archived=False) | Q(is_active=False),
                name="kpi_asn_archived_inactive",
            ),
            models.CheckConstraint(
                condition=Q(assignment_version__gte=1),
                name="kpi_asn_version_positive",
            ),
        ]
        indexes = [
            models.Index(
                fields=["employee", "is_active", "is_archived", "start_date"],
                name="kpi_asn_employee_current_idx",
            ),
            models.Index(
                fields=["employee", "start_date", "end_date"],
                name="kpi_asn_employee_dates_idx",
            ),
            models.Index(
                fields=["manager", "is_active", "is_archived"],
                name="kpi_asn_manager_current_idx",
            ),
            models.Index(
                fields=["kpi_template", "start_date"],
                name="kpi_asn_template_date_idx",
            ),
        ]

    def _persisted_instance(self, *, lock=False):
        if not self.pk:
            return None
        queryset = (
            type(self)
            .objects.select_related(
                "employee__user",
                "kpi_template",
                "manager__employee_profile",
            )
        )
        if lock:
            queryset = queryset.select_for_update()
        return queryset.filter(pk=self.pk).first()

    def clean(self):
        super().clean()
        errors = {}

        if self.end_date and self.start_date and self.end_date < self.start_date:
            errors["end_date"] = "End date cannot be before start date."
        if self.is_archived and self.is_active:
            errors["is_archived"] = "An archived assignment cannot remain active."

        employee = None
        if self.employee_id:
            employee = (
                EmployeeProfile.objects.select_related("user")
                .filter(pk=self.employee_id)
                .first()
            )
            if employee is None:
                errors["employee"] = "The selected employee does not exist."
            elif employee.is_archived and (self._state.adding or self.is_active):
                errors["employee"] = (
                    "Archived employees cannot receive active KPI assignments."
                )

        template = None
        if self.kpi_template_id:
            template = KPIRoleTemplate.objects.filter(pk=self.kpi_template_id).first()
            if template is None:
                errors["kpi_template"] = "The selected KPI template does not exist."
            elif self._state.adding and not template.is_active:
                errors["kpi_template"] = (
                    "Inactive KPI templates cannot receive new assignments."
                )

        if self.manager_id:
            manager_model = self._meta.get_field("manager").remote_field.model
            manager = (
                manager_model.objects.select_related("employee_profile")
                .filter(pk=self.manager_id)
                .first()
            )
            if employee and employee.user_id == self.manager_id:
                errors["manager"] = "An employee cannot be their own KPI manager."
            elif manager is None:
                errors["manager"] = "The selected KPI manager does not exist."
            elif self._state.adding or (self.is_active and not self.is_archived):
                if not manager.is_active:
                    errors["manager"] = "An inactive user cannot be a KPI manager."
                else:
                    manager_profile = getattr(manager, "employee_profile", None)
                    if manager_profile is None or manager_profile.is_archived:
                        errors["manager"] = (
                            "An archived or missing employee profile cannot be a "
                            "KPI manager."
                        )
                    elif manager_profile.status not in (
                        EmployeeProfile.STATUS_ACTIVE,
                        EmployeeProfile.STATUS_ON_LEAVE,
                    ):
                        errors["manager"] = (
                            "An inactive employee profile cannot be a KPI manager."
                        )

        if errors:
            raise ValidationError(errors)

    def is_current(self, as_of=None):
        as_of = as_of or timezone.localdate()
        return (
            self.is_active
            and not self.is_archived
            and self.start_date <= as_of
            and (self.end_date is None or self.end_date >= as_of)
        )

    def is_future(self, as_of=None):
        as_of = as_of or timezone.localdate()
        return self.start_date > as_of

    def is_expired(self, as_of=None):
        as_of = as_of or timezone.localdate()
        return self.end_date is not None and self.end_date < as_of

    def effective_weight(self, as_of=None):
        return self.role_weight if self.is_current(as_of) else Decimal("0.00")

    def history_snapshot(self):
        return {
            "employee_id": self.employee_id,
            "kpi_template_id": self.kpi_template_id,
            "role_weight": str(self.role_weight),
            "manager_id": self.manager_id,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "is_active": self.is_active,
            "bonus_eligible": self.bonus_eligible,
            "notes": self.notes,
            "assignment_version": self.assignment_version,
            "is_archived": self.is_archived,
        }

    def save(self, *args, **kwargs):
        history_reason = kwargs.pop("history_reason", "")
        history_change_type = kwargs.pop("history_change_type", "")
        validate_timeline = kwargs.pop("validate_timeline", True)

        with transaction.atomic():
            previous = self._persisted_instance(lock=True)
            if previous:
                if previous.employee_id != self.employee_id:
                    raise ValidationError(
                        {"employee": "Archive and recreate to change the employee."}
                    )
                if previous.kpi_template_id != self.kpi_template_id:
                    raise ValidationError(
                        {
                            "kpi_template": (
                                "Archive and recreate to change the KPI template."
                            )
                        }
                    )
                self.assignment_version = previous.assignment_version + 1
            else:
                self.assignment_version = max(int(self.assignment_version or 1), 1)

            self.full_clean()
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = set(kwargs["update_fields"]) | {
                    "assignment_version",
                    "updated_at",
                }

            EmployeeProfile.objects.select_for_update().get(pk=self.employee_id)
            if validate_timeline:
                from crm.services.kpi_assignments import validate_projected_assignment

                validate_projected_assignment(self)

            super().save(*args, **kwargs)
            persisted = type(self).objects.get(pk=self.pk)
            self.assignment_version = persisted.assignment_version
            change_type = history_change_type or (
                EmployeeKPIRoleAssignmentHistory.CHANGE_UPDATED
                if previous
                else EmployeeKPIRoleAssignmentHistory.CHANGE_CREATED
            )
            EmployeeKPIRoleAssignmentHistory.objects.create(
                assignment=persisted,
                employee=persisted.employee,
                kpi_template=persisted.kpi_template,
                assignment_version=persisted.assignment_version,
                change_type=change_type,
                old_values=previous.history_snapshot() if previous else {},
                new_values=persisted.history_snapshot(),
                changed_by=persisted.updated_by or persisted.created_by,
                reason=history_reason,
            )

    def delete(self, *args, **kwargs):
        raise ValidationError(
            "KPI assignments cannot be deleted; deactivate or archive them."
        )

    def __str__(self):
        return (
            f"{self.employee} - {self.kpi_template} "
            f"({self.role_weight.quantize(Decimal('0.01'))}%)"
        )


class ProtectedAssignmentHistoryQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("KPI assignment history is immutable.")

    def delete(self):
        raise ValidationError("KPI assignment history is immutable.")

    def bulk_update(self, objs, fields, **kwargs):
        raise ValidationError("KPI assignment history is immutable.")


class EmployeeKPIRoleAssignmentHistory(models.Model):
    CHANGE_CREATED = "created"
    CHANGE_UPDATED = "updated"
    CHANGE_DEACTIVATED = "deactivated"
    CHANGE_ARCHIVED = "archived"
    CHANGE_CHOICES = (
        (CHANGE_CREATED, "Created"),
        (CHANGE_UPDATED, "Updated"),
        (CHANGE_DEACTIVATED, "Deactivated"),
        (CHANGE_ARCHIVED, "Archived"),
    )

    assignment = models.ForeignKey(
        EmployeeKPIRoleAssignment,
        on_delete=models.PROTECT,
        related_name="history_entries",
    )
    employee = models.ForeignKey(
        EmployeeProfile,
        on_delete=models.PROTECT,
        related_name="kpi_assignment_history",
    )
    kpi_template = models.ForeignKey(
        KPIRoleTemplate,
        on_delete=models.PROTECT,
        related_name="employee_assignment_history",
    )
    assignment_version = models.PositiveIntegerField()
    change_type = models.CharField(max_length=20, choices=CHANGE_CHOICES)
    old_values = models.JSONField(default=dict, blank=True)
    new_values = models.JSONField(default=dict)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="kpi_assignment_history_changes",
    )
    reason = models.TextField(blank=True, default="")
    changed_at = models.DateTimeField(auto_now_add=True)

    objects = ProtectedAssignmentHistoryQuerySet.as_manager()

    class Meta:
        ordering = ("assignment_id", "assignment_version")
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(
                fields=["assignment", "assignment_version"],
                name="kpi_asn_history_version_uniq",
            ),
            models.CheckConstraint(
                condition=Q(assignment_version__gte=1),
                name="kpi_asn_hist_version_positive",
            ),
        ]
        indexes = [
            models.Index(
                fields=["employee", "changed_at"],
                name="kpi_asn_hist_employee_idx",
            ),
            models.Index(
                fields=["change_type", "changed_at"],
                name="kpi_asn_hist_change_idx",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding or self.pk:
            raise ValidationError("KPI assignment history is immutable.")
        kwargs["force_insert"] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("KPI assignment history is immutable.")

    def __str__(self):
        return (
            f"Assignment {self.assignment_id} v{self.assignment_version} "
            f"{self.change_type}"
        )
