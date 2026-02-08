from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from marketing.models import OutreachCampaign, ContactListMembership, OutreachSendLog
from marketing.utils.outreach import can_send_to_contact, queue_initial_send


class Command(BaseCommand):
    help = "Queue outreach emails for active campaigns."

    def handle(self, *args, **options):
        if not getattr(settings, "MARKETING_OUTREACH_ENABLED", False):
            self.stdout.write("MARKETING_OUTREACH_ENABLED is off. Skipping.")
            return

        queued = 0
        for campaign in OutreachCampaign.objects.filter(status="active", channel="email"):
            if not campaign.contact_list:
                continue

            memberships = ContactListMembership.objects.filter(contact_list=campaign.contact_list).select_related("contact")
            for m in memberships:
                contact = m.contact
                if not can_send_to_contact(contact):
                    continue

                obj, created = queue_initial_send(campaign, contact)
                if created:
                    queued += 1

                # Follow-up rules: 3 and 7 days after initial send, if no reply
                initial = OutreachSendLog.objects.filter(
                    campaign=campaign,
                    contact=contact,
                    send_type="initial",
                    status="sent",
                ).first()
                if initial and initial.sent_at:
                    if not OutreachSendLog.objects.filter(campaign=campaign, contact=contact, status="replied").exists():
                        if initial.sent_at.date() <= (timezone.localdate() - timedelta(days=3)):
                            OutreachSendLog.objects.get_or_create(
                                campaign=campaign,
                                contact=contact,
                                send_type="followup1",
                                defaults={"queued_at": timezone.now(), "status": "queued"},
                            )
                        if initial.sent_at.date() <= (timezone.localdate() - timedelta(days=7)):
                            OutreachSendLog.objects.get_or_create(
                                campaign=campaign,
                                contact=contact,
                                send_type="followup2",
                                defaults={"queued_at": timezone.now(), "status": "queued"},
                            )

        self.stdout.write(self.style.SUCCESS(f"Queued {queued} emails."))
