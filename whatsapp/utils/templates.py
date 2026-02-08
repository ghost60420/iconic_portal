from crm.models import Lead


def render_template(text: str, lead: Lead | None = None, extra: dict | None = None) -> str:
    out = text or ""
    if not out:
        return ""

    values = {}
    if lead:
        values.update(
            {
                "first_name": lead.contact_name.split(" ")[0] if (lead.contact_name or "") else "",
                "company": lead.account_brand or "",
                "lead_id": lead.lead_id or "",
                "product": lead.product_interest or "",
            }
        )
    if extra:
        values.update(extra)

    for k, v in values.items():
        out = out.replace("{" + k + "}", str(v))
    return out
