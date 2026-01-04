import re
from django.core.management.base import BaseCommand
from django.utils import timezone

from crm.models import Lead
from crm.models_email import EmailMessage


def extract_form_entry_number(subject: str) -> str:
    # Example: "New Form Entry #952 for Contact Form"
    m = re.search(r"#\s*(\d+)", subject or "")
    return m.group(1) if m else ""


def extract_field(body_text: str, label: str) -> str:
    """
    Works with lines like:
    1. Name
    Callie Derouard
    2. Email Address
    babeandbloomco@gmail.com
    """
    if not body_text:
        return ""

    lines = [l.strip() for l in body_text.splitlines() if l.strip()]
    label_low = label.lower()

    for i, line in enumerate(lines):
        if line.lower() == label_low:
            # next line is value
            if i + 1 < len(lines):
                return lines[i + 1].strip()
    return ""


class Command(BaseCommand):
    help = "Convert synced form entry emails into Lead records."

    def add_arguments(self, parser):
        parser.add_argument("--only", type=str, default="")  # example: 952

    def handle(self, *args, **opts):
        only = (opts["only"] or "").strip()

        qs = EmailMessage.objects.filter(is_form_entry=True).order_by("id")
        if only:
            qs = qs.filter(subject__icontains=f"#{only}")

        created = 0
        skipped = 0

        for em in qs:
            entry_no = extract_form_entry_number(em.subject)
            if not entry_no:
                skipped += 1
                continue

            # Do not create duplicates
            if Lead.objects.filter(lead_id=str(entry_no)).exists():
                skipped += 1
                continue

            name = extract_field(em.body_text, "Name")
            email_addr = extract_field(em.body_text, "Email Address")
            phone = extract_field(em.body_text, "Phone")
            company = extract_field(em.body_text, "Company Name")
            notes = extract_field(em.body_text, "Additional Notes")

            lead = Lead.objects.create(
                lead_id=str(entry_no),
                account_brand=company or "",
                contact_name=name or "",
                email=email_addr or em.from_email or "",
                phone=phone or "",
                notes=(notes or em.body_text or "")[:5000],
                source="Website Form",
                created_date=timezone.now().date(),
            )

            created += 1
            self.stdout.write(f"Created Lead {lead.lead_id} ({lead.contact_name})")

        self.stdout.write(self.style.SUCCESS(f"Done. Created: {created} | Skipped: {skipped}"))