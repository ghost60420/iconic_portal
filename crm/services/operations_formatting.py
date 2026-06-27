from django.utils import timezone


def relative_time_label(value, *, now=None):
    if not value:
        return ""
    now = now or timezone.now()
    if timezone.is_aware(value) and timezone.is_naive(now):
        now = timezone.make_aware(now, timezone.get_current_timezone())
    elapsed = max(0, int((now - value).total_seconds()))
    local_value = timezone.localtime(value) if timezone.is_aware(value) else value
    local_now = timezone.localtime(now) if timezone.is_aware(now) else now
    day_delta = (local_now.date() - local_value.date()).days

    if day_delta == 1:
        return "Yesterday"
    if day_delta > 1:
        return f"{day_delta} days ago"
    if elapsed < 60:
        return "Just now"
    if elapsed < 3600:
        minutes = elapsed // 60
        return f"{minutes} min ago"
    hours = elapsed // 3600
    return f"{hours} hour{'s' if hours != 1 else ''} ago"


def initials_for_name(name):
    parts = [part for part in (name or "").strip().split() if part]
    if not parts:
        return "SY"
    return "".join(part[0] for part in parts[:2]).upper()


def activity_time_label(value, *, now=None):
    if not value:
        return ""
    now = now or timezone.now()
    local_value = timezone.localtime(value) if timezone.is_aware(value) else value
    local_now = timezone.localtime(now) if timezone.is_aware(now) else now
    day_delta = (local_now.date() - local_value.date()).days
    elapsed = max(0, int((now - value).total_seconds()))
    if day_delta == 0 and elapsed < 3600:
        return relative_time_label(value, now=now)
    if day_delta == 0:
        return f"Today {local_value:%-I:%M %p}"
    if day_delta == 1:
        return f"Yesterday {local_value:%-I:%M %p}"
    return relative_time_label(value, now=now)
