from django.utils import timezone
from crm.ai.openai_client import ask_openai

def score_lead(*, request, lead):
    prompt = f"""
You are a CRM lead analyzer.

Lead:
Brand: {lead.account_brand}
Name: {lead.contact_name}
Email: {lead.email}
Phone: {lead.phone}
Product interest: {lead.product_interest}
Order quantity: {lead.order_quantity}
Notes: {lead.notes}

Return exactly this format:
SCORE: (0-100)
LABEL: good or maybe or bad
SUMMARY: 2 lines only
""".strip()

    text = ask_openai(
        request=request,
        user=request.user if request else None,
        prompt_text=prompt,
        meta={"feature": "lead_score", "lead_id": lead.id},
    )

    score = 0
    label = "maybe"
    summary = ""

    for line in text.splitlines():
        line2 = line.strip()
        if line2.upper().startswith("SCORE:"):
            try:
                score = int("".join([c for c in line2.split(":", 1)[1] if c.isdigit()]) or "0")
            except Exception:
                score = 0
        elif line2.upper().startswith("LABEL:"):
            label = line2.split(":", 1)[1].strip().lower() or "maybe"
        elif line2.upper().startswith("SUMMARY:"):
            summary = line2.split(":", 1)[1].strip()

    lead.ai_score = max(0, min(score, 100))
    lead.ai_label = label[:20]
    lead.ai_summary = summary[:500]
    lead.ai_last_run_at = timezone.now()
    lead.save(update_fields=["ai_score", "ai_label", "ai_summary", "ai_last_run_at"])
    return lead