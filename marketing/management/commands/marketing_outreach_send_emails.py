from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from marketing.models import OutreachCampaign, OutreachSendLog
from marketing.services.email_provider import send_outreach_email
from marketing.utils.outreach import allowed_daily_limit, within_send_window, can_send_to_contact, build_email_body


class Command(BaseCommand):
    help = "Send queued outreach emails with safety limits."

    def handle(self, *args, **options):
        if not getattr(settings, "MARKETING_OUTREACH_ENABLED", False):
            self.stdout.write("MARKETING_OUTREACH_ENABLED is off. Skipping.")
            return

        today = timezone.localdate()
        total_sent = 0

        campaigns = OutreachCampaign.objects.filter(status="active", channel="email")
        for campaign in campaigns:
            if not within_send_window(campaign):
                continue

            daily_limit = allowed_daily_limit(campaign)
            sent_today = OutreachSendLog.objects.filter(
                campaign=campaign, status="sent", sent_at__date=today
            ).count()
            remaining = max(daily_limit - sent_today, 0)
            if remaining <= 0:
                continue

            hourly_limit = campaign.hourly_limit or daily_limit
            sent_hour = OutreachSendLog.objects.filter(
                campaign=campaign,
                status="sent",
                sent_at__gte=timezone.now() - timedelta(hours=1),
            ).count()
            remaining = min(remaining, max(hourly_limit - sent_hour, 0))
            if remaining <= 0:
                continue

            templates = list(campaign.templates.all().order_by("id"))
            if not templates:
                continue

            logs = (
                OutreachSendLog.objects.filter(campaign=campaign, status="queued")
                .select_related("contact")
                .order_by("queued_at", "id")[:remaining]
            )

            for log in logs:
                contact = log.contact
                if not can_send_to_contact(contact):
                    log.status = "stopped"
                    log.error_text = "Do not contact"
                    log.save(update_fields=["status", "error_text"])
                    continue

                # Stop if replied previously
                if OutreachSendLog.objects.filter(campaign=campaign, contact=contact, status="replied").exists():
                    log.status = "stopped"
                    log.error_text = "Contact replied"
                    log.save(update_fields=["status", "error_text"])
                    continue

                template = templates[0]
                if log.send_type == "followup1" and len(templates) > 1:
                    template = templates[1]
                if log.send_type == "followup2" and len(templates) > 2:
                    template = templates[2]

                subject = template.subject_template.replace("{first_name}", contact.first_name or "there")
                body = build_email_body(contact=contact, template_text=template.body_template)

                ok = send_outreach_email(
                    to_email=contact.email,
                    subject=subject,
                    body=body,
                    from_email=campaign.sending_account or None,
                )

                if ok:
                    log.status = "sent"
                    log.sent_at = timezone.now()
                    log.error_text = ""
                    contact.last_contacted_at = log.sent_at
                    contact.save(update_fields=["last_contacted_at"])
                    log.save(update_fields=["status", "sent_at", "error_text"])
                    total_sent += 1
                else:
                    log.status = "failed"
                    log.error_text = "Send failed"
                    log.save(update_fields=["status", "error_text"])

        self.stdout.write(self.style.SUCCESS(f"Sent {total_sent} outreach emails."))
