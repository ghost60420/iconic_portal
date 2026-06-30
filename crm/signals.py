from django.db import transaction
from django.contrib.auth import get_user_model
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from crm.models import CostingHeader, LeadComment, LeadTask, OpportunityTask, ProductionOrder, QuickCosting
from crm.services.audit_log import is_tracked_model, model_snapshot, schedule_audit
from crm.audit_context import get_current_actor
from crm.services.employee_profiles import employee_audit


User = get_user_model()


@receiver(pre_save, sender=User)
def capture_employee_password_change(sender, instance, raw=False, **kwargs):
    if raw or not instance.pk:
        return
    previous_password = sender.objects.filter(pk=instance.pk).values_list("password", flat=True).first()
    instance._crm_employee_password_changed = bool(
        previous_password and previous_password != instance.password
    )


@receiver(post_save, sender=User)
def audit_employee_password_change(sender, instance, created=False, raw=False, **kwargs):
    if raw or created or not getattr(instance, "_crm_employee_password_changed", False):
        return
    employee_audit(
        get_current_actor(),
        instance,
        "password_reset",
        "",
        "Password reset",
    )


@receiver(pre_save)
def capture_audit_before_save(sender, instance, raw=False, **kwargs):
    if raw or not is_tracked_model(sender) or not getattr(instance, "pk", None):
        return
    try:
        previous = sender.objects.filter(pk=instance.pk).first()
        instance._crm_audit_before = model_snapshot(previous) if previous else {}
    except Exception:
        instance._crm_audit_before = {}


@receiver(post_save)
def audit_after_save(sender, instance, created=False, raw=False, **kwargs):
    if raw or not is_tracked_model(sender):
        return
    schedule_audit(
        instance,
        created=created,
        before=getattr(instance, "_crm_audit_before", {}),
    )


@receiver(post_delete)
def audit_after_delete(sender, instance, **kwargs):
    if is_tracked_model(sender):
        schedule_audit(instance, deleted=True)


@receiver(post_save, sender=CostingHeader)
def notify_ceo_on_quotation_submission(sender, instance, created=False, raw=False, **kwargs):
    if raw or created or not instance.quotation_number:
        return
    before = getattr(instance, "_crm_audit_before", {})
    is_initial_submission = before.get("quotation_number") != instance.quotation_number
    is_resubmission = (
        before.get("quotation_status") == CostingHeader.QUOTATION_STATUS_REJECTED
        and instance.quotation_status == CostingHeader.QUOTATION_STATUS_DRAFT
    )
    if not is_initial_submission and not is_resubmission:
        return

    def emit():
        try:
            from crm.services.operations_notifications import notify_quotation_waiting_approval

            notify_quotation_waiting_approval(instance)
        except Exception:
            # Notification failure must never roll back quotation submission.
            return

    transaction.on_commit(emit, robust=True)


@receiver(post_save, sender=ProductionOrder)
def notify_when_production_order_created(sender, instance, created=False, raw=False, **kwargs):
    if raw or not created:
        return

    def emit():
        from crm.services.operations_notifications import notify_production_order_created

        order = ProductionOrder.objects.select_related(
            "assigned_production_manager",
            "created_by",
            "lead__assigned_to",
            "source_quotation__quoted_by",
        ).filter(pk=instance.pk).first()
        if order:
            notify_production_order_created(order)

    transaction.on_commit(emit, robust=True)


@receiver(post_save, sender=CostingHeader)
def notify_quotation_status_decision(sender, instance, created=False, raw=False, **kwargs):
    if raw or created or instance.quotation_status not in {
        CostingHeader.QUOTATION_STATUS_APPROVED,
        CostingHeader.QUOTATION_STATUS_REJECTED,
    }:
        return
    before = getattr(instance, "_crm_audit_before", {})
    if before.get("quotation_status") == instance.quotation_status:
        return

    decision = instance.quotation_status

    def emit():
        from crm.services.operations_notifications import notify_quotation_decision

        costing = CostingHeader.objects.select_related(
            "quoted_by",
            "quotation_approved_by",
            "quotation_rejected_by",
            "opportunity__lead__assigned_to",
        ).filter(pk=instance.pk).first()
        if costing:
            notify_quotation_decision(costing, decision)

    transaction.on_commit(emit, robust=True)


@receiver(post_save, sender=QuickCosting)
def notify_ceo_on_quick_costing_submission(sender, instance, created=False, raw=False, **kwargs):
    if raw or created or not instance.approval_submitted_at:
        return
    before = getattr(instance, "_crm_audit_before", {})
    if before.get("approval_submitted_at") == str(instance.approval_submitted_at):
        return

    def emit():
        from crm.services.operations_notifications import notify_quotation_waiting_approval

        quick_costing = QuickCosting.objects.select_related("approval_submitted_by").filter(pk=instance.pk).first()
        if quick_costing:
            notify_quotation_waiting_approval(quick_costing)

    transaction.on_commit(emit, robust=True)


@receiver(post_save, sender=QuickCosting)
def notify_quick_costing_decision(sender, instance, created=False, raw=False, **kwargs):
    if raw or created or instance.status not in {QuickCosting.STATUS_APPROVED, QuickCosting.STATUS_REJECTED}:
        return
    before = getattr(instance, "_crm_audit_before", {})
    if before.get("status") == instance.status:
        return
    decision = "approved" if instance.status == QuickCosting.STATUS_APPROVED else "rejected"

    def emit():
        from crm.services.operations_notifications import notify_quotation_decision

        quick_costing = QuickCosting.objects.select_related(
            "approval_submitted_by",
            "approved_by",
            "rejected_by",
            "opportunity__lead__assigned_to",
        ).filter(pk=instance.pk).first()
        if quick_costing:
            notify_quotation_decision(quick_costing, decision)

    transaction.on_commit(emit, robust=True)


@receiver(pre_save, sender=LeadTask)
@receiver(pre_save, sender=OpportunityTask)
def capture_task_status_before_save(sender, instance, raw=False, **kwargs):
    if raw or not instance.pk:
        return
    instance._notification_before_status = sender.objects.filter(pk=instance.pk).values_list(
        "status", flat=True
    ).first()


@receiver(post_save, sender=LeadTask)
@receiver(post_save, sender=OpportunityTask)
def notify_task_change(sender, instance, created=False, raw=False, **kwargs):
    if raw:
        return
    if created and instance.assigned_to:
        event = "assigned"
    elif instance.status == "Done" and getattr(instance, "_notification_before_status", None) != "Done":
        event = "completed"
    else:
        return

    def emit():
        from crm.services.operations_notifications import notify_task_event

        task = sender.objects.select_related(
            "lead__assigned_to" if sender is LeadTask else "opportunity__lead__assigned_to"
        ).filter(pk=instance.pk).first()
        if task:
            notify_task_event(task, event)

    transaction.on_commit(emit, robust=True)


@receiver(post_save, sender=LeadComment)
def notify_record_owner_on_comment(sender, instance, created=False, raw=False, **kwargs):
    if raw or not created or not instance.author_user_id or instance.is_ai:
        return

    def emit():
        from crm.services.operations_notifications import notify_comment_added

        comment = LeadComment.objects.select_related(
            "author_user",
            "lead__assigned_to__employee_profile",
            "opportunity__lead__assigned_to__employee_profile",
            "production__assigned_production_manager__employee_profile",
        ).filter(pk=instance.pk).first()
        if comment:
            notify_comment_added(comment)

    transaction.on_commit(emit, robust=True)
