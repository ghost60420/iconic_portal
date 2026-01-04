from django.conf import settings
from django.core.mail import send_mail


WHATSAPP_LINK = "https://wa.me/16045006009"


def send_thank_you(*, to_email: str, contact_name: str = ""):
    to_email = (to_email or "").strip()
    if not to_email:
        return

    name = (contact_name or "").strip() or "there"

    subject = "Thanks for your message"
    body = f"""Hi {name},

Thank you for reaching out to Iconic Apparel House.
We received your message and our team will contact you shortly.

WhatsApp: {WHATSAPP_LINK}

Thank you,
Iconic Apparel House
""".strip()

    send_mail(
        subject=subject,
        message=body,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", ""),
        recipient_list=[to_email],
        fail_silently=True,
    )