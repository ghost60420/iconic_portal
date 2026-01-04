# crm/ai/suggestions.py

from crm.ai.openai_client import ask_openai


def _val(obj, *names):
    """
    Return the first non empty attribute value from obj.
    """
    for n in names:
        v = getattr(obj, n, "") if obj is not None else ""
        if v is None:
            v = ""
        if str(v).strip() != "":
            return v
    return ""


def lead_suggestion(*, request, lead):
    user = getattr(request, "user", None)

    prompt = f"""
You are Iconic CRM helper.
Be short and clear.

Lead info:
Name: {_val(lead, "contact_name", "name")}
Brand: {_val(lead, "account_brand", "brand", "company_name")}
Email: {_val(lead, "email")}
Phone: {_val(lead, "phone", "phone_number")}
Notes: {_val(lead, "notes", "note", "additional_notes")}

Give:
1) Best next action
2) A short follow up message (2 to 4 lines)
3) Missing info or risks
""".strip()

    return ask_openai(
        request=request,
        user=user,
        prompt_text=prompt,
        meta={"feature": "lead_suggestion", "lead_id": getattr(lead, "id", None)},
    )


def opportunity_suggestion(*, request, opp):
    user = getattr(request, "user", None)

    prompt = f"""
You are Iconic CRM helper.
Use short bullet points.

Opportunity info:
Code: {_val(opp, "opportunity_id", "code", "ref", "id")}
Stage: {_val(opp, "stage", "status")}
Product: {_val(opp, "product_type", "product_category", "product_name")}
Quantity: {_val(opp, "quantity", "qty")}
Budget: {_val(opp, "order_value", "budget", "target_price")}

Give:
1) Next best step
2) What to ask the client next
3) Red flags
""".strip()

    return ask_openai(
        request=request,
        user=user,
        prompt_text=prompt,
        meta={"feature": "opportunity_suggestion", "opp_id": getattr(opp, "id", None)},
    )


def production_suggestion(*, request, po):
    user = getattr(request, "user", None)

    prompt = f"""
You are Iconic production helper.
Be short and practical.

Production order:
Code: {_val(po, "order_code", "po_number", "code", "id")}
Status: {_val(po, "status")}
Current stage: {_val(po, "current_stage", "stage")}
Deadline: {_val(po, "delivery_date", "ship_date", "due_date")}

Give:
1) Most likely risk
2) What to check now
3) Short client update message (2 to 4 lines)
""".strip()

    return ask_openai(
        request=request,
        user=user,
        prompt_text=prompt,
        meta={"feature": "production_suggestion", "po_id": getattr(po, "id", None)},
    )