from django.core.management.base import BaseCommand
from django.utils import timezone

from django.conf import settings

from crm.models import LeadResearchJob, LeadContactPoint, LeadAIInsight
from crm.services.lead_enrichment import analyze_website, recommend_channel, qualification_status
from crm.ai.suggestions import lead_outbound_insights


def _add_contact_point(lead, contact_type, value, label="", source_url="", confidence=70):
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
    help = "Process queued lead research jobs."

    def add_arguments(self, parser):
        parser.add_argument("--job", type=int, default=None)
        parser.add_argument("--limit", type=int, default=5)

    def handle(self, *args, **options):
        job_id = options.get("job")
        limit = options.get("limit") or 5

        qs = LeadResearchJob.objects.filter(status="queued").order_by("created_at")
        if job_id:
            qs = qs.filter(pk=job_id)

        for job in qs[:limit]:
            lead = job.lead
            job.status = "processing"
            job.started_at = timezone.now()
            job.save(update_fields=["status", "started_at"])

            try:
                signals = analyze_website(job.website or lead.website or lead.company_website)
                job.data = signals

                if signals.get("company_name") and not lead.account_brand:
                    lead.account_brand = signals.get("company_name")[:200]
                if signals.get("domain") and not lead.website:
                    lead.website = "https://" + signals.get("domain")

                for email in signals.get("emails", []):
                    _add_contact_point(lead, "email", email, "Website", signals.get("contact_page") or job.website)
                for phone in signals.get("phones", []):
                    _add_contact_point(lead, "phone", phone, "Website", signals.get("contact_page") or job.website)

                socials = signals.get("socials", {})
                if socials.get("instagram"):
                    _add_contact_point(lead, "instagram", socials.get("instagram"), "Website", socials.get("instagram"))
                if socials.get("linkedin"):
                    _add_contact_point(lead, "linkedin", socials.get("linkedin"), "Website", socials.get("linkedin"))

                contact_page = signals.get("contact_page")
                if contact_page:
                    _add_contact_point(lead, "contact_form", contact_page, "Contact page", contact_page)

                score, strengths = lead.compute_fit_score()
                lead.brand_fit_score = score
                lead.ideal_customer_profile_match = score >= 70
                lead.qualification_reason = ", ".join(strengths)
                lead.recommended_channel = recommend_channel(signals)
                lead.qualification_status = qualification_status(score, lead.contact_points.exists())
                lead.last_enriched_at = timezone.now()
                lead.save()

                summary_text = f"Website research completed. Found {len(signals.get('emails', []))} emails and {len(signals.get('phones', []))} phones."

                if (getattr(settings, "OPENAI_API_KEY", "") or "").strip():
                    try:
                        ai_text = lead_outbound_insights(request=None, lead=lead)
                        if ai_text:
                            summary_text = ai_text
                    except Exception:
                        pass

                LeadAIInsight.objects.create(
                    lead=lead,
                    summary_text=summary_text,
                    data=signals,
                )

                job.status = "done"
                job.finished_at = timezone.now()
                job.save(update_fields=["status", "data", "finished_at"])
            except Exception as exc:
                job.status = "failed"
                job.error_message = str(exc)[:4000]
                job.finished_at = timezone.now()
                job.save(update_fields=["status", "error_message", "finished_at"])
