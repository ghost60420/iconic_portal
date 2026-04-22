from dataclasses import dataclass

from django.utils import timezone

from crm.models import Lead

from leadbrain.models import LeadBrainCompany
from leadbrain.services.matching import find_matching_lead


def _text(value):
    if value is None:
        return ""
    return str(value).strip()


def _market_from_country(country):
    value = _text(country).lower()
    if value in {"canada", "ca"}:
        return "CA"
    if value in {"usa", "us", "united states", "united states of america"}:
        return "USA"
    if value in {"bangladesh", "bd"}:
        return "BD"
    return "OTHER"


def _lead_notes(company: LeadBrainCompany) -> str:
    sections = []
    if company.notes:
        sections.append(company.notes.strip())

    fit_lines = []
    if company.fit_label:
        fit_lines.append(f"Lead Brain fit: {company.get_fit_label_display()} ({company.fit_score})")
    elif company.fit_score:
        fit_lines.append(f"Lead Brain fit score: {company.fit_score}")
    if company.suggested_action:
        fit_lines.append(f"Lead Brain suggested action: {company.suggested_action}")
    if company.ai_summary:
        fit_lines.append(f"Lead Brain summary: {company.ai_summary}")
    if company.fit_reason:
        fit_lines.append(f"Lead Brain reason: {company.fit_reason}")
    if fit_lines:
        sections.append("\n".join(fit_lines))

    source_lines = [
        f"Lead Brain company #{company.pk}",
        f"Lead Brain upload #{company.upload_id}",
    ]
    if company.linkedin_url:
        source_lines.append(f"LinkedIn: {company.linkedin_url}")
    sections.append("\n".join(source_lines))

    return "\n\n".join(section for section in sections if section).strip()


@dataclass
class LeadMoveResult:
    created: bool
    lead: Lead | None = None
    duplicate_reason: str = ""
    message: str = ""


def _duplicate_for_company(company: LeadBrainCompany):
    return find_matching_lead(
        website=company.website,
        email=company.email,
    )


def create_lead_from_company(company: LeadBrainCompany) -> LeadMoveResult:
    if company.moved_to_leads and company.moved_to_lead_id:
        return LeadMoveResult(
            created=False,
            lead=company.moved_to_lead,
            message=f"{company.company_name or 'This company'} was already moved to Leads.",
        )

    duplicate_lead, duplicate_reason = _duplicate_for_company(company)
    if duplicate_lead:
        return LeadMoveResult(
            created=False,
            lead=duplicate_lead,
            duplicate_reason=duplicate_reason,
            message=(
                f"A Lead already exists for {company.company_name or 'this company'} "
                f"by {duplicate_reason} (Lead #{duplicate_lead.pk})."
            ),
        )

    lead = Lead.objects.create(
        account_brand=_text(company.company_name)[:200],
        contact_name=_text(company.best_contact_name)[:200],
        email=_text(company.email),
        phone=_text(company.phone)[:50],
        country=_text(company.country)[:100],
        city=_text(company.city)[:100],
        website=_text(company.website)[:255],
        company_website=_text(company.website)[:255],
        linkedin_url=_text(company.linkedin_url)[:255],
        market=_market_from_country(company.country),
        source="Other",
        source_channel="Lead Brain Lite",
        lead_type="outbound",
        outbound_method="Lead Brain Lite",
        outbound_status="Not Contacted",
        lead_status="New",
        qualification_status=(
            "Outreach Ready"
            if company.fit_label == LeadBrainCompany.FIT_GOOD
            else "Needs Review"
        ),
        qualification_reason=_text(company.fit_reason),
        notes=_lead_notes(company),
        brand_fit_score=company.fit_score or 0,
        fit_score_locked=True,
        ideal_customer_profile_match=(company.fit_score or 0) >= 70,
        recommended_next_action=_text(company.suggested_action)[:200],
    )

    company.moved_to_leads = True
    company.moved_to_lead = lead
    company.moved_to_lead_code = lead.lead_id
    company.moved_to_leads_at = timezone.now()
    company.reviewed = True
    company.save(
        update_fields=[
            "moved_to_leads",
            "moved_to_lead",
            "moved_to_lead_code",
            "moved_to_leads_at",
            "reviewed",
            "updated_at",
        ]
    )

    return LeadMoveResult(
        created=True,
        lead=lead,
        message=f"{company.company_name or 'This company'} was moved to Leads as Lead #{lead.pk}.",
    )
