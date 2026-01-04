# crm/email_outbox.py
from django.conf import settings
from django.core.mail import send_mail

from crm.models_email_outbox import OutboundEmailLog


def send_and_log_email(*, lead, message_type: str, subject: str, body: str, user=None):
    to_email = (getattr(lead, "email", "") or "").strip()

    log = OutboundEmailLog.objects.create(
        lead=lead,
        to_email=to_email,
        subject=(subject or "")[:255],
        body=(body or ""),
        message_type=message_type or "",
        sent_ok=False,
        error="",
        created_by=user,
    )

    if not to_email or "@" not in to_email:
        log.error = "Lead email is missing or invalid"
        log.save(update_fields=["error"])
        return False, "Lead email is missing or invalid"

    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "") or getattr(settings, "EMAIL_HOST_USER", "") or None

    try:
        send_mail(
            subject=(subject or "").strip()[:200],
            message=(body or "").strip(),
            from_email=from_email,
            recipient_list=[to_email],
            fail_silently=False,
        )
        log.sent_ok = True
        log.save(update_fields=["sent_ok"])
        return True, ""
    except Exception as e:
        log.error = str(e)[:300]
        log.save(update_fields=["error"])
        return False, log.error