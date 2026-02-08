from datetime import timedelta
from django.conf import settings
from django.utils import timezone

from whatsapp.models import WhatsAppAutomationRule, WhatsAppSendQueue, WhatsAppMessage
from whatsapp.utils.templates import render_template
from whatsapp.utils.limits import contact_daily_count


def _within_business_hours(now=None) -> bool:
    now = now or timezone.localtime()
    hours = getattr(settings, "WHATSAPP_BUSINESS_HOURS_JSON", {"start": "09:00", "end": "17:00"})
    start = hours.get("start", "09:00")
    end = hours.get("end", "17:00")

    try:
        start_h, start_m = [int(x) for x in start.split(":")]
        end_h, end_m = [int(x) for x in end.split(":")]
    except Exception:
        return True

    start_t = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    end_t = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    if start_t <= end_t:
        return start_t <= now <= end_t
    return now >= start_t or now <= end_t


def enqueue_rule(thread, rule, lead=None, delay_seconds=0):
    if not rule.is_active:
        return

    today_count = contact_daily_count(thread)
    if today_count >= rule.max_per_contact_per_day:
        return

    body = render_template(rule.response_template, lead=lead)
    if not body:
        return

    scheduled = timezone.now() + timedelta(seconds=delay_seconds or rule.send_delay_seconds or 0)
    WhatsAppSendQueue.objects.create(
        account=thread.account,
        thread=thread,
        message_body=body,
        scheduled_at=scheduled,
    )


def run_inbound_automation(thread, inbound_text: str, lead=None):
    rules = WhatsAppAutomationRule.objects.filter(is_active=True)
    if not rules.exists():
        return

    inbound_text = (inbound_text or "").lower()

    # First inbound
    inbound_count = WhatsAppMessage.objects.filter(
        thread=thread, direction="inbound"
    ).exclude(received_at__isnull=True).count()
    if inbound_count <= 1:
        rule = rules.filter(trigger="first_inbound").first()
        if rule and _within_business_hours():
            enqueue_rule(thread, rule, lead=lead)

    # After hours
    if not _within_business_hours():
        rule = rules.filter(trigger="after_hours").first()
        if rule:
            enqueue_rule(thread, rule, lead=lead)

    # Keyword match
    rule = rules.filter(trigger="keyword_match").first()
    if rule:
        keywords = [k.lower() for k in (rule.keyword_list_json or [])]
        if any(k in inbound_text for k in keywords):
            enqueue_rule(thread, rule, lead=lead)
