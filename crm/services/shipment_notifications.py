import logging
import smtplib
import socket

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage, get_connection
from django.core.validators import validate_email
from django.utils import timezone


logger = logging.getLogger(__name__)

SHIPMENT_NOTIFY_STATUSES = {
    "shipped": "Dispatched",
    "out_for_delivery": "Out for delivery",
    "delivered": "Delivered",
}

SHIPMENT_EMAIL_TIMEOUT_EXCEPTIONS = (socket.timeout, TimeoutError)
SHIPMENT_EMAIL_RETRY_EXCEPTIONS = (
    smtplib.SMTPException,
    OSError,
    *SHIPMENT_EMAIL_TIMEOUT_EXCEPTIONS,
)


def shipment_email_target(shipment):
    if getattr(shipment, "customer", None) and shipment.customer and getattr(shipment.customer, "email", None):
        name = shipment.customer.contact_name or shipment.customer.account_brand or "Customer"
        return shipment.customer.email, name
    if getattr(shipment, "opportunity", None) and shipment.opportunity and getattr(shipment.opportunity, "lead", None):
        lead = shipment.opportunity.lead
        if lead and getattr(lead, "email", None):
            name = lead.contact_name or lead.account_brand or "Customer"
            return lead.email, name
    return None, None


def validate_shipment_email(email):
    if not email:
        return False
    try:
        validate_email(email)
    except ValidationError:
        return False
    return True


def build_shipment_status_email(shipment, status_key):
    email_to, name = shipment_email_target(shipment)
    if not email_to:
        return None
    if not validate_shipment_email(email_to):
        return {"invalid_email": email_to}

    status_label = SHIPMENT_NOTIFY_STATUSES.get(status_key, "Shipment update")
    carrier_name = shipment.get_carrier_display() if hasattr(shipment, "get_carrier_display") else "Carrier"
    ship_date = shipment.ship_date or timezone.localdate()
    tracking_line = shipment.tracking_number or "Tracking will be shared soon."

    subject = f"Shipment update: {status_label}"
    lines = [
        f"Hello {name},",
        "",
        f"Your shipment is now: {status_label}.",
        "",
        f"Carrier: {carrier_name}",
        f"Tracking: {tracking_line}",
        f"Ship date: {ship_date}",
    ]
    if getattr(shipment, "tracking_url", None):
        lines.append(f"Tracking link: {shipment.tracking_url}")
    lines += ["", "Thank you,", "Iconic Apparel House"]

    return {
        "to_email": email_to,
        "subject": subject,
        "body": "\n".join(lines),
        "from_email": getattr(settings, "DEFAULT_FROM_EMAIL", "info@iconicapparelhouse.com"),
    }


def send_shipment_status_email(shipment, status_key):
    payload = build_shipment_status_email(shipment, status_key)
    if not payload:
        logger.warning("Shipment notification skipped: no recipient", extra={"shipment_id": shipment.pk})
        return False, "missing_recipient"
    if payload.get("invalid_email"):
        logger.warning(
            "Shipment notification skipped: invalid recipient",
            extra={"shipment_id": shipment.pk, "email": payload["invalid_email"]},
        )
        return False, "invalid_recipient"

    try:
        timeout = int(getattr(settings, "SHIPMENT_EMAIL_TIMEOUT", getattr(settings, "EMAIL_TIMEOUT", 8)) or 8)
    except (TypeError, ValueError):
        timeout = 8
    host_user = getattr(settings, "EMAIL_HOST_USER", "") or ""
    host_password = getattr(settings, "EMAIL_HOST_PASSWORD", "") or ""
    if not host_user or not host_password:
        logger.warning("Shipment notification skipped: SMTP credentials missing", extra={"shipment_id": shipment.pk})
        return False, "missing_smtp_credentials"

    try:
        connection = get_connection(timeout=timeout)
        message = EmailMessage(
            payload["subject"],
            payload["body"],
            payload["from_email"],
            [payload["to_email"]],
            connection=connection,
        )
        sent_count = message.send(fail_silently=False)
    except SHIPMENT_EMAIL_TIMEOUT_EXCEPTIONS:
        logger.exception("Shipment notification SMTP timeout", extra={"shipment_id": shipment.pk, "status": status_key})
        raise
    except SHIPMENT_EMAIL_RETRY_EXCEPTIONS:
        logger.exception("Shipment notification SMTP failure", extra={"shipment_id": shipment.pk, "status": status_key})
        raise
    except Exception:
        logger.exception("Shipment notification unexpected failure", extra={"shipment_id": shipment.pk, "status": status_key})
        raise

    if sent_count > 0:
        return True, ""

    logger.warning("Shipment notification failed without exception", extra={"shipment_id": shipment.pk, "status": status_key})
    return False, "not_sent"
