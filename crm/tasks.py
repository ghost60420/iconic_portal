import logging

from celery import shared_task
from django.db import close_old_connections
from django.utils import timezone

from crm.models import Shipment
from crm.services.shipment_notifications import (
    SHIPMENT_EMAIL_RETRY_EXCEPTIONS,
    SHIPMENT_NOTIFY_STATUSES,
    send_shipment_status_email,
)


logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=SHIPMENT_EMAIL_RETRY_EXCEPTIONS,
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
    soft_time_limit=30,
    time_limit=45,
)
def send_shipment_status_notification(self, shipment_id, status_key=None, force=False):
    close_old_connections()
    shipment = (
        Shipment.objects.select_related("customer", "opportunity", "opportunity__lead")
        .filter(pk=shipment_id)
        .first()
    )
    if not shipment:
        logger.warning("Shipment notification skipped: shipment missing", extra={"shipment_id": shipment_id})
        return {"status": "missing"}

    status_key = status_key or shipment.status
    if status_key not in SHIPMENT_NOTIFY_STATUSES and not force:
        logger.info(
            "Shipment notification skipped: non-notifiable status",
            extra={"shipment_id": shipment.pk, "status": status_key},
        )
        return {"status": "skipped", "reason": "non_notifiable_status"}

    if shipment.last_notified_status == status_key and not force:
        logger.info(
            "Shipment notification skipped: status already notified",
            extra={"shipment_id": shipment.pk, "status": status_key},
        )
        return {"status": "skipped", "reason": "already_notified"}

    try:
        sent, reason = send_shipment_status_email(shipment, status_key)
    except SHIPMENT_EMAIL_RETRY_EXCEPTIONS:
        logger.exception(
            "Shipment notification will retry after email transport failure",
            extra={"shipment_id": shipment.pk, "status": status_key, "retries": self.request.retries},
        )
        raise
    except Exception:
        logger.exception(
            "Shipment notification failed without retry",
            extra={"shipment_id": shipment.pk, "status": status_key},
        )
        return {"status": "failed", "reason": "unexpected_error"}

    if not sent:
        logger.warning(
            "Shipment notification not sent",
            extra={"shipment_id": shipment.pk, "status": status_key, "reason": reason},
        )
        return {"status": "failed", "reason": reason}

    update_fields = ["last_notified_status"]
    shipment.last_notified_status = status_key
    if status_key == "delivered" and not shipment.delivered_at:
        shipment.delivered_at = timezone.now()
        update_fields.append("delivered_at")
    shipment.save(update_fields=update_fields)
    logger.info("Shipment notification sent", extra={"shipment_id": shipment.pk, "status": status_key})
    return {"status": "sent"}
