from django.conf import settings
from django.core.mail import send_mail


def send_outreach_email(*, to_email: str, subject: str, body: str, from_email: str | None = None) -> bool:
    if not to_email:
        return False

    sender = from_email or getattr(settings, "DEFAULT_FROM_EMAIL", "")
    try:
        send_mail(subject, body, sender, [to_email], fail_silently=False)
    except Exception:
        return False
    return True
