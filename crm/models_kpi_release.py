from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from .models_kpi import KPISettings, KPITemplateVersion
from .models_kpi_bonus import KPIBonusRuleSet, KPIBonusWeightProfile
from .models_kpi_intelligence import KPIIntelligenceRuleSet
from .models_kpi_notifications import KPINotificationRule


class ProtectedKPIPolicyApprovalQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("KPI policy approvals must use the release service.")

    def delete(self):
        raise ValidationError("KPI policy approval history cannot be deleted.")

    def bulk_create(self, objs, **kwargs):
        raise ValidationError("KPI policy approvals must use the release service.")

    def bulk_update(self, objs, fields, **kwargs):
        raise ValidationError("KPI policy approvals must use the release service.")


class KPIPolicyApproval(models.Model):
    TYPE_TEMPLATE = "template"
    TYPE_SETTINGS = "settings"
    TYPE_BONUS_WEIGHT = "bonus_weight"
    TYPE_BONUS_RULE = "bonus_rule"
    TYPE_INTELLIGENCE = "intelligence"
    TYPE_NOTIFICATION = "notification"
    TYPE_CHOICES = (
        (TYPE_TEMPLATE, "KPI Template"),
        (TYPE_SETTINGS, "KPI Status Settings"),
        (TYPE_BONUS_WEIGHT, "Bonus Weight Profile"),
        (TYPE_BONUS_RULE, "Bonus Rule Set"),
        (TYPE_INTELLIGENCE, "Intelligence Rule Set"),
        (TYPE_NOTIFICATION, "Notification Rule"),
    )

    STATUS_DRAFT = "draft"
    STATUS_UNDER_REVIEW = "under_review"
    STATUS_APPROVED = "approved"
    STATUS_PUBLISHED = "published"
    STATUS_RETIRED = "retired"
    STATUS_CHOICES = (
        (STATUS_DRAFT, "Draft"),
        (STATUS_UNDER_REVIEW, "Under Review"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_PUBLISHED, "Published"),
        (STATUS_RETIRED, "Retired"),
    )

    policy_type = models.CharField(max_length=24, choices=TYPE_CHOICES)
    status = models.CharField(
        max_length=24,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
        db_index=True,
    )
    template_version = models.OneToOneField(
        KPITemplateVersion,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="release_approval",
    )
    kpi_settings = models.OneToOneField(
        KPISettings,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="release_approval",
    )
    bonus_weight_profile = models.OneToOneField(
        KPIBonusWeightProfile,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="release_approval",
    )
    bonus_rule_set = models.OneToOneField(
        KPIBonusRuleSet,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="release_approval",
    )
    intelligence_rule_set = models.OneToOneField(
        KPIIntelligenceRuleSet,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="release_approval",
    )
    notification_rule = models.OneToOneField(
        KPINotificationRule,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="release_approval",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_kpi_policy_approvals",
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="submitted_kpi_policy_approvals",
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_kpi_policy_approvals",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="published_kpi_policy_approvals",
    )
    published_at = models.DateTimeField(null=True, blank=True)
    retired_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="retired_kpi_policy_approvals",
    )
    retired_at = models.DateTimeField(null=True, blank=True)
    review_reason = models.TextField(blank=True, default="")
    approval_reason = models.TextField(blank=True, default="")
    retirement_reason = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ProtectedKPIPolicyApprovalQuerySet.as_manager()
    service_objects = models.Manager()

    class Meta:
        ordering = ("policy_type", "id")
        default_permissions = ()
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(
                        policy_type="template",
                        template_version__isnull=False,
                        kpi_settings__isnull=True,
                        bonus_weight_profile__isnull=True,
                        bonus_rule_set__isnull=True,
                        intelligence_rule_set__isnull=True,
                        notification_rule__isnull=True,
                    )
                    | Q(
                        policy_type="settings",
                        template_version__isnull=True,
                        kpi_settings__isnull=False,
                        bonus_weight_profile__isnull=True,
                        bonus_rule_set__isnull=True,
                        intelligence_rule_set__isnull=True,
                        notification_rule__isnull=True,
                    )
                    | Q(
                        policy_type="bonus_weight",
                        template_version__isnull=True,
                        kpi_settings__isnull=True,
                        bonus_weight_profile__isnull=False,
                        bonus_rule_set__isnull=True,
                        intelligence_rule_set__isnull=True,
                        notification_rule__isnull=True,
                    )
                    | Q(
                        policy_type="bonus_rule",
                        template_version__isnull=True,
                        kpi_settings__isnull=True,
                        bonus_weight_profile__isnull=True,
                        bonus_rule_set__isnull=False,
                        intelligence_rule_set__isnull=True,
                        notification_rule__isnull=True,
                    )
                    | Q(
                        policy_type="intelligence",
                        template_version__isnull=True,
                        kpi_settings__isnull=True,
                        bonus_weight_profile__isnull=True,
                        bonus_rule_set__isnull=True,
                        intelligence_rule_set__isnull=False,
                        notification_rule__isnull=True,
                    )
                    | Q(
                        policy_type="notification",
                        template_version__isnull=True,
                        kpi_settings__isnull=True,
                        bonus_weight_profile__isnull=True,
                        bonus_rule_set__isnull=True,
                        intelligence_rule_set__isnull=True,
                        notification_rule__isnull=False,
                    )
                ),
                name="kpi_policy_approval_one_target",
            ),
            models.CheckConstraint(
                condition=(
                    Q(submitted_by__isnull=True, submitted_at__isnull=True)
                    | Q(submitted_by__isnull=False, submitted_at__isnull=False)
                ),
                name="kpi_policy_submission_pair",
            ),
            models.CheckConstraint(
                condition=(
                    Q(approved_by__isnull=True, approved_at__isnull=True)
                    | Q(approved_by__isnull=False, approved_at__isnull=False)
                ),
                name="kpi_policy_approval_pair",
            ),
            models.CheckConstraint(
                condition=(
                    Q(published_by__isnull=True, published_at__isnull=True)
                    | Q(published_by__isnull=False, published_at__isnull=False)
                ),
                name="kpi_policy_publication_pair",
            ),
            models.CheckConstraint(
                condition=(
                    Q(retired_by__isnull=True, retired_at__isnull=True)
                    | Q(retired_by__isnull=False, retired_at__isnull=False)
                ),
                name="kpi_policy_retirement_pair",
            ),
        ]
        indexes = [
            models.Index(
                fields=("policy_type", "status", "-updated_at"),
                name="kpi_policy_status_lookup_idx",
            ),
        ]

    TARGET_FIELDS = {
        TYPE_TEMPLATE: "template_version",
        TYPE_SETTINGS: "kpi_settings",
        TYPE_BONUS_WEIGHT: "bonus_weight_profile",
        TYPE_BONUS_RULE: "bonus_rule_set",
        TYPE_INTELLIGENCE: "intelligence_rule_set",
        TYPE_NOTIFICATION: "notification_rule",
    }

    @property
    def target_object(self):
        return getattr(self, self.TARGET_FIELDS[self.policy_type])

    @property
    def target_label(self):
        return str(self.target_object)

    def clean(self):
        super().clean()
        populated = [
            field
            for field in self.TARGET_FIELDS.values()
            if getattr(self, f"{field}_id") is not None
        ]
        expected = self.TARGET_FIELDS.get(self.policy_type)
        if len(populated) != 1 or populated[0] != expected:
            raise ValidationError(
                {"policy_type": "Policy type must match exactly one target record."}
            )

    def save(self, *args, **kwargs):
        service_create = kwargs.pop("service_create", False)
        service_transition = kwargs.pop("service_transition", False)
        if self._state.adding and not service_create:
            raise ValidationError("KPI policy approvals must use the release service.")
        if not self._state.adding and not service_transition:
            raise ValidationError("KPI policy approvals must use the release service.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("KPI policy approval history cannot be deleted.")

    def __str__(self):
        return f"{self.get_policy_type_display()}: {self.target_label}"
