from datetime import timedelta
from django.utils import timezone

from whatsapp.models import WhatsAppMessage, DoNotContactPhone


def is_dnc(phone: str) -> bool:
    return DoNotContactPhone.objects.filter(phone=phone).exists()


def contact_daily_count(thread, day=None) -> int:
    day = day or timezone.localdate()
    return WhatsAppMessage.objects.filter(
        thread=thread,
        direction="outbound",
        sent_at__date=day,
    ).count()


def account_daily_count(account, day=None) -> int:
    day = day or timezone.localdate()
    return WhatsAppMessage.objects.filter(
        thread__account=account,
        direction="outbound",
        sent_at__date=day,
    ).count()


def account_hourly_count(account) -> int:
    since = timezone.now() - timedelta(hours=1)
    return WhatsAppMessage.objects.filter(
        thread__account=account,
        direction="outbound",
        sent_at__gte=since,
    ).count()
