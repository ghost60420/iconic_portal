from django.conf import settings


def marketing_flags(request):
    return {
        "MARKETING_ENABLED": getattr(settings, "MARKETING_ENABLED", False),
        "MARKETING_SEO_ENABLED": getattr(settings, "MARKETING_SEO_ENABLED", False),
        "MARKETING_SOCIAL_ENABLED": getattr(settings, "MARKETING_SOCIAL_ENABLED", False),
        "MARKETING_OUTREACH_ENABLED": getattr(settings, "MARKETING_OUTREACH_ENABLED", False),
        "MARKETING_ADS_ENABLED": getattr(settings, "MARKETING_ADS_ENABLED", False),
        "MARKETING_AI_ENABLED": getattr(settings, "MARKETING_AI_ENABLED", False),
    }
