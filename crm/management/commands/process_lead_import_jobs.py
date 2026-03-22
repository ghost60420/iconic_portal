import csv
import json
from io import BytesIO, TextIOWrapper

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from crm.models import Lead, LeadImportJob, LeadContactPoint
from crm.services.lead_enrichment import recommend_channel, qualification_status

try:
    import openpyxl
except Exception:
    openpyxl = None


def _norm_key(value):
    return "".join(ch for ch in str(value).strip().lower() if ch.isalnum())


HEADER_MAP = {
    "company": "account_brand",
    "brand": "account_brand",
    "companyname": "account_brand",
    "accountbrand": "account_brand",
    "contact": "contact_name",
    "contactname": "contact_name",
    "name": "contact_name",
    "email": "email",
    "phone": "phone",
    "mobile": "phone",
    "website": "website",
    "domain": "website",
    "instagram": "instagram_handle",
    "ig": "instagram_handle",
    "linkedin": "linkedin_url",
    "country": "country",
    "region": "region",
    "product": "product_interest",
    "productinterest": "product_interest",
    "producttype": "product_interest",
    "targetmin": "target_order_volume_min",
    "targetmax": "target_order_volume_max",
    "targetordervolumemin": "target_order_volume_min",
    "targetordervolumemax": "target_order_volume_max",
    "sourcechannel": "source_channel",
    "outboundmethod": "outbound_method",
    "assignedto": "assigned_to",
    "notes": "notes",
}


def _map_row(row):
    data = {}
    for key, value in row.items():
        norm = _norm_key(key)
        field = HEADER_MAP.get(norm)
        if not field:
            continue
        data[field] = (value or "").strip() if isinstance(value, str) else value
    return data


def _resolve_assigned(value):
    if not value:
        return None
    user_model = get_user_model()
    value = value.strip()
    if "@" in value:
        return user_model.objects.filter(email__iexact=value).first()
    return user_model.objects.filter(username__iexact=value).first()


def _find_duplicate_lead(data):
    email = (data.get("email") or "").strip().lower()
    phone = (data.get("phone") or "").strip()
    website = (data.get("website") or "").strip().lower()
    instagram = (data.get("instagram_handle") or "").strip().lower()
    linkedin = (data.get("linkedin_url") or "").strip().lower()

    q = Q()
    if email:
        q |= Q(email__iexact=email)
    if phone:
        q |= Q(phone__icontains=phone)
    if website:
        q |= Q(website__icontains=website) | Q(company_website__icontains=website)
    if instagram:
        q |= Q(instagram_handle__icontains=instagram)
    if linkedin:
        q |= Q(linkedin_url__icontains=linkedin)
    if not q:
        return None
    return Lead.objects.filter(q).first()


def _add_contact_point(lead, contact_type, value, label="", source_url="", confidence=60):
    if not value:
        return
    LeadContactPoint.objects.get_or_create(
        lead=lead,
        contact_type=contact_type,
        value=str(value).strip(),
        defaults={
            "label": label,
            "source_url": source_url,
            "confidence": confidence,
        },
    )


class Command(BaseCommand):
    help = "Process queued lead import jobs."

    def add_arguments(self, parser):
        parser.add_argument("--job", type=int, default=None)
        parser.add_argument("--limit", type=int, default=5)

    def handle(self, *args, **options):
        job_id = options.get("job")
        limit = options.get("limit") or 5

        qs = LeadImportJob.objects.filter(status="queued").order_by("created_at")
        if job_id:
            qs = qs.filter(pk=job_id)

        for job in qs[:limit]:
            self.stdout.write(self.style.NOTICE(f"Processing job {job.pk}"))
            job.status = "processing"
            job.started_at = timezone.now()
            job.save(update_fields=["status", "started_at"])

            try:
                created = updated = duplicates = strong = moderate = weak = bad = missing_contact = 0
                errors = []

                file_path = job.file.path
                if file_path.lower().endswith(".xlsx"):
                    if not openpyxl:
                        raise RuntimeError("openpyxl not available for Excel import.")
                    wb = openpyxl.load_workbook(file_path)
                    ws = wb.active
                    headers = []
                    rows = []
                    for i, row in enumerate(ws.iter_rows(values_only=True)):
                        if i == 0:
                            headers = [str(c or "").strip() for c in row]
                            continue
                        rows.append(dict(zip(headers, row)))
                else:
                    with open(file_path, "rb") as f:
                        wrapper = TextIOWrapper(f, encoding="utf-8-sig")
                        reader = csv.DictReader(wrapper)
                        rows = list(reader)

                job.total_rows = len(rows)
                job.save(update_fields=["total_rows"])

                for row in rows:
                    data = _map_row(row)
                    if not any(data.values()):
                        continue

                    existing = _find_duplicate_lead(data)
                    lead = existing or Lead()
                    is_new = existing is None
                    changed = False

                    if existing:
                        duplicates += 1
                    else:
                        created += 1

                    for field, value in data.items():
                        if field == "assigned_to":
                            assigned = _resolve_assigned(value)
                            if assigned and not lead.assigned_to:
                                lead.assigned_to = assigned
                                changed = True
                            continue
                        if value in (None, ""):
                            continue
                        current = getattr(lead, field, "")
                        if not current:
                            setattr(lead, field, value)
                            changed = True

                    if is_new:
                        lead.lead_type = "outbound"
                        if not lead.outbound_status:
                            lead.outbound_status = "Not Contacted"
                        lead.qualification_status = "Raw Imported"
                    else:
                        if not lead.lead_type:
                            lead.lead_type = "outbound"
                        if lead.lead_type == "outbound" and not lead.outbound_status:
                            lead.outbound_status = "Not Contacted"

                    lead.import_job = job

                    with transaction.atomic():
                        lead.save()

                    if data.get("email"):
                        _add_contact_point(lead, "email", data.get("email"), "Imported")
                    if data.get("phone"):
                        _add_contact_point(lead, "phone", data.get("phone"), "Imported")
                    if data.get("website"):
                        _add_contact_point(lead, "website", data.get("website"), "Imported")
                    if data.get("instagram_handle"):
                        _add_contact_point(lead, "instagram", data.get("instagram_handle"), "Imported")
                    if data.get("linkedin_url"):
                        _add_contact_point(lead, "linkedin", data.get("linkedin_url"), "Imported")

                    score, strengths = lead.compute_fit_score()
                    if lead.fit_score_locked:
                        score = lead.brand_fit_score
                    else:
                        lead.brand_fit_score = score
                        lead.ideal_customer_profile_match = score >= 70
                    lead.qualification_reason = ", ".join(strengths)

                    has_contact = lead.contact_points.exists()
                    lead.recommended_channel = recommend_channel({
                        "emails": [data.get("email")] if data.get("email") else [],
                        "phones": [data.get("phone")] if data.get("phone") else [],
                        "contact_page": "",
                        "socials": {
                            "instagram": data.get("instagram_handle") or "",
                            "linkedin": data.get("linkedin_url") or "",
                        },
                    })
                    if is_new:
                        lead.qualification_status = qualification_status(score, has_contact)
                    lead.last_enriched_at = timezone.now()
                    lead.save(update_fields=[
                        "brand_fit_score",
                        "ideal_customer_profile_match",
                        "qualification_reason",
                        "qualification_status",
                        "recommended_channel",
                        "last_enriched_at",
                    ])

                    if lead.qualification_status == "Outreach Ready":
                        strong += 1
                    elif lead.qualification_status == "Qualified":
                        moderate += 1
                    elif lead.qualification_status in ("Needs Review", "Contact Missing"):
                        weak += 1
                    elif lead.qualification_status == "Bad Fit":
                        bad += 1
                    if lead.qualification_status == "Contact Missing":
                        missing_contact += 1

                    if existing and changed:
                        updated += 1

                job.created_count = created
                job.updated_count = updated
                job.duplicate_count = duplicates
                job.strong_fit_count = strong
                job.moderate_fit_count = moderate
                job.weak_fit_count = weak
                job.bad_fit_count = bad
                job.missing_contact_count = missing_contact
                job.error_count = len(errors)
                job.error_log = "\n".join(errors)[:4000]
                job.status = "done"
                job.finished_at = timezone.now()
                job.save()

                self.stdout.write(self.style.SUCCESS(f"Job {job.pk} completed"))
            except Exception as exc:
                job.status = "failed"
                job.error_log = str(exc)[:4000]
                job.finished_at = timezone.now()
                job.save(update_fields=["status", "error_log", "finished_at"])
                self.stdout.write(self.style.ERROR(f"Job {job.pk} failed: {exc}"))
