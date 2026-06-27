from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from crm.models import CostingHeader, ProductionOrder
from crm.services.audit_log import is_tracked_model, model_snapshot, schedule_audit


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
    if before.get("quotation_number") == instance.quotation_number:
        return

    def emit():
        try:
            from crm.services.operations_notifications import notify_quotation_waiting_approval

            notify_quotation_waiting_approval(instance)
        except Exception:
            # Notification failure must never roll back quotation submission.
            return

    transaction.on_commit(emit)


@receiver(post_save, sender=ProductionOrder)
def notify_when_order_ready_to_ship(sender, instance, created=False, raw=False, **kwargs):
    if raw or instance.operational_status != "ready_to_ship":
        return
    before = getattr(instance, "_crm_audit_before", {})
    if before.get("operational_status") == "ready_to_ship":
        return

    def emit():
        try:
            from crm.services.operations_notifications import notify_ready_to_ship

            notify_ready_to_ship(instance)
        except Exception:
            # Operational status is authoritative even if a notification cannot be written.
            return

    transaction.on_commit(emit)
