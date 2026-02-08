from datetime import datetime, time, timedelta

from django.conf import settings
from django.utils import timezone

from marketing.models import Contact, OutreachCampaign, OutreachSendLog


RAMP_PLAN = [
    (2, (20, 30)),
    (4, (40, 60)),
    (7, (60, 90)),
    (10, (90, 120)),
    (14, (120, 160)),
]


def _today_local():
    return timezone.localdate()


def get_site_base_url() -> str:
    base = getattr(settings, "SITE_BASE_URL", "") or ""
    return base.rstrip("/")


def allowed_daily_limit(campaign: OutreachCampaign) -> int:
    days = 0
    if campaign.created_at:
        days = (timezone.now().date() - campaign.created_at.date()).days + 1
    limit = campaign.daily_limit or 30
    for max_day, (low, high) in RAMP_PLAN:
        if days <= max_day:
            limit = min(limit, high)
            break
    return max(limit, 1)


def within_send_window(campaign: OutreachCampaign) -> bool:
    window = campaign.schedule_window_json or {}
    start = window.get("start", "09:00")
    end = window.get("end", "17:00")

    try:
        start_t = datetime.strptime(start, "%H:%M").time()
        end_t = datetime.strptime(end, "%H:%M").time()
    except ValueError:
        return True

    now = timezone.localtime().time()
    if start_t <= end_t:
        return start_t <= now <= end_t
    # Overnight window
    return now >= start_t or now <= end_t


def can_send_to_contact(contact: Contact) -> bool:
    if contact.do_not_contact:
        return False
    if contact.consent_status == "opted_out":
        return False
    return True


def get_unsubscribe_link(contact: Contact) -> str:
    base = get_site_base_url()
    if not base:
        return ""
    return f"{base}/marketing/unsubscribe/{contact.unsubscribe_token}/"


def build_email_body(*, contact: Contact, template_text: str) -> str:
    body = template_text or ""
    body = body.replace("{first_name}", contact.first_name or "there")
    body = body.replace("{last_name}", contact.last_name or "")
    body = body.replace("{company}", contact.company or "")

    unsub = get_unsubscribe_link(contact)
    if unsub:
        body = f"{body}\n\nUnsubscribe: {unsub}"
    return body


def queue_initial_send(campaign: OutreachCampaign, contact: Contact):
    return OutreachSendLog.objects.get_or_create(
        campaign=campaign,
        contact=contact,
        send_type="initial",
        defaults={
            "queued_at": timezone.now(),
            "status": "queued",
        },
    )
