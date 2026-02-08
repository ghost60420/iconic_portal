from django.conf import settings


def whatsapp_flags(request):
    return {
        "WHATSAPP_ENABLED": getattr(settings, "WHATSAPP_ENABLED", False),
        "WHATSAPP_AUTOMATION_ENABLED": getattr(settings, "WHATSAPP_AUTOMATION_ENABLED", False),
        "WHATSAPP_OUTBOUND_ENABLED": getattr(settings, "WHATSAPP_OUTBOUND_ENABLED", False),
    }
