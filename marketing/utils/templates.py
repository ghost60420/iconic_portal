from marketing.models import OutreachMessageTemplate


DEFAULT_TEMPLATES = [
    {
        "subject": "Quick question about your next production",
        "body": "Hi {first_name},\n\nI am reaching out from Iconic Apparel House. We are a Canadian owned clothing manufacturer with ethical production in Bangladesh.\nIf you are planning hoodies, activewear, or kidswear soon, I can share pricing, MOQ, and timelines based on your product.\nWould you like a quick call this week, or should I email a simple quote template first\n\nThank You,\nMd Refat\nAccount Executive\nIconic Apparel House Inc\nT: 604-500-6009\nE: refat@iconicapparelhouse.com\nWeb: iconicapparelhouse.com\n\nIf you do not want emails from me, reply with Unsubscribe and I will remove you.",
    },
    {
        "subject": "Following up",
        "body": "Hi {first_name},\n\nJust following up in case my last email got buried.\nIf you tell me what product you are working on and your target quantity, I can reply with a clear MOQ and price range.\n\nThank You,\nMd Refat\nAccount Executive\nIconic Apparel House Inc\nT: 604-500-6009\nE: refat@iconicapparelhouse.com\nWeb: iconicapparelhouse.com\n\nIf you do not want emails from me, reply with Unsubscribe and I will remove you.",
    },
    {
        "subject": "Last note from me",
        "body": "Hi {first_name},\n\nQuick yes or no. Are you open to adding a new manufacturer this year\nIf not, no problem. I will not follow up again.\n\nThank You,\nMd Refat\nAccount Executive\nIconic Apparel House Inc\nT: 604-500-6009\nE: refat@iconicapparelhouse.com\nWeb: iconicapparelhouse.com\n\nIf you do not want emails from me, reply with Unsubscribe and I will remove you.",
    },
]


def seed_default_templates(campaign):
    if campaign.templates.exists():
        return
    for item in DEFAULT_TEMPLATES:
        OutreachMessageTemplate.objects.create(
            campaign=campaign,
            subject_template=item["subject"],
            body_template=item["body"],
        )
