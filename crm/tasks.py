import logging

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.core.cache import cache
from django.db import close_old_connections
from django.utils import timezone

from crm.models import Shipment
from crm.services.shipment_notifications import (
    SHIPMENT_EMAIL_RETRY_EXCEPTIONS,
    SHIPMENT_EMAIL_TIMEOUT_EXCEPTIONS,
    SHIPMENT_NOTIFY_STATUSES,
    send_shipment_status_email,
)


logger = logging.getLogger(__name__)


def _int_setting(name, default):
    try:
        return int(getattr(settings, name, default) or default)
    except (TypeError, ValueError):
        return default


SHIPMENT_NOTIFICATION_MAX_RETRIES = _int_setting("SHIPMENT_EMAIL_MAX_RETRIES", 3)
SHIPMENT_NOTIFICATION_SOFT_LIMIT = _int_setting("SHIPMENT_EMAIL_TASK_SOFT_TIME_LIMIT", 30)
SHIPMENT_NOTIFICATION_HARD_LIMIT = _int_setting("SHIPMENT_EMAIL_TASK_TIME_LIMIT", 45)
SHIPMENT_NOTIFICATION_LOCK_SECONDS = _int_setting("SHIPMENT_EMAIL_LOCK_SECONDS", 300)


def _shipment_notification_task_options():
    return {
        "bind": True,
        "autoretry_for": SHIPMENT_EMAIL_RETRY_EXCEPTIONS + (SoftTimeLimitExceeded,),
        "retry_backoff": True,
        "retry_jitter": True,
        "retry_kwargs": {"max_retries": SHIPMENT_NOTIFICATION_MAX_RETRIES},
        "soft_time_limit": SHIPMENT_NOTIFICATION_SOFT_LIMIT,
        "time_limit": SHIPMENT_NOTIFICATION_HARD_LIMIT,
    }


def _retry_count(task):
    try:
        return int(getattr(task.request, "retries", 0) or 0)
    except Exception:
        return 0


def _send_shipment_notification(task, shipment_id, status_key=None, force=False):
    close_old_connections()
    shipment = (
        Shipment.objects.select_related("customer", "opportunity", "opportunity__lead")
        .filter(pk=shipment_id)
        .first()
    )
    retry_count = _retry_count(task)
    base_extra = {
        "shipment_id": shipment_id,
        "status": status_key,
        "retry_count": retry_count,
        "max_retries": SHIPMENT_NOTIFICATION_MAX_RETRIES,
        "task_name": getattr(task, "name", "shipment_notification"),
    }

    if not shipment:
        logger.warning("Shipment notification skipped: shipment missing", extra=base_extra)
        return {"status": "missing"}

    status_key = status_key or shipment.status
    base_extra["shipment_id"] = shipment.pk
    base_extra["status"] = status_key

    if status_key not in SHIPMENT_NOTIFY_STATUSES and not force:
        logger.info(
            "Shipment notification skipped: non-notifiable status",
            extra=base_extra,
        )
        return {"status": "skipped", "reason": "non_notifiable_status"}

    if shipment.last_notified_status == status_key and not force:
        logger.info(
            "Shipment notification skipped: status already notified",
            extra=base_extra,
        )
        return {"status": "skipped", "reason": "already_notified"}

    lock_key = f"shipment-notification:{shipment.pk}:{status_key}"
    lock_acquired = False
    if not force:
        lock_acquired = cache.add(lock_key, "1", timeout=SHIPMENT_NOTIFICATION_LOCK_SECONDS)
    if not force and not lock_acquired:
        logger.info("Shipment notification skipped: duplicate task already running", extra=base_extra)
        return {"status": "skipped", "reason": "already_running"}

    try:
        try:
            sent, reason = send_shipment_status_email(shipment, status_key)
        except SoftTimeLimitExceeded:
            logger.exception("Shipment notification task timed out; retrying", extra=base_extra)
            raise
        except SHIPMENT_EMAIL_TIMEOUT_EXCEPTIONS:
            logger.exception("Shipment notification SMTP timeout; retrying", extra=base_extra)
            raise
        except SHIPMENT_EMAIL_RETRY_EXCEPTIONS:
            logger.exception(
                "Shipment notification will retry after email transport failure",
                extra=base_extra,
            )
            raise
        except Exception:
            logger.exception(
                "Shipment notification failed without retry",
                extra=base_extra,
            )
            return {"status": "failed", "reason": "unexpected_error"}

        if not sent:
            logger.warning(
                "Shipment notification not sent",
                extra={**base_extra, "reason": reason},
            )
            return {"status": "failed", "reason": reason}

        update_fields = ["last_notified_status"]
        shipment.last_notified_status = status_key
        if status_key == "delivered" and not shipment.delivered_at:
            shipment.delivered_at = timezone.now()
            update_fields.append("delivered_at")
        shipment.save(update_fields=update_fields)
        logger.info("Shipment notification sent", extra=base_extra)
        return {"status": "sent"}
    finally:
        if lock_acquired:
            cache.delete(lock_key)


@shared_task(**_shipment_notification_task_options())
def send_shipment_notification_async(self, shipment_id, status_key=None, force=False):
    return _send_shipment_notification(self, shipment_id, status_key=status_key, force=force)


@shared_task(**_shipment_notification_task_options())
def send_shipment_status_notification(self, shipment_id, status_key=None, force=False):
    return _send_shipment_notification(self, shipment_id, status_key=status_key, force=force)
