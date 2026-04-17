from urllib.parse import quote


def _text(value):
    if value is None:
        return ""
    return str(value).strip()


def _truncate(value, limit):
    text = _text(value)
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)].rstrip() + "..."


def _clean_missing_info(items):
    out = []
    for item in items or []:
        text = _text(item)
        if not text or text == "No major missing info detected.":
            continue
        if text in {"Email", "Email or phone", "Contact name", "Next follow-up date"}:
            continue
        out.append(text)
    return out


def _request_line(items):
    labels = {
        "Phone": "best phone number",
        "Website": "website or brand page",
        "Product interest": "product interest or style reference",
        "Order quantity": "target order quantity",
    }
    prompts = []
    for item in _clean_missing_info(items):
        prompts.append(labels.get(item, item.lower()))
        if len(prompts) >= 2:
            break

    if not prompts:
        return ""
    if len(prompts) == 1:
        return f"Could you share your {prompts[0]}?"
    return f"Could you share your {prompts[0]} and {prompts[1]}?"


def _subject(lead):
    brand = _text(getattr(lead, "account_brand", ""))
    product = _text(getattr(lead, "product_interest", "")) or _text(getattr(lead, "product_category", ""))

    if product and brand:
        return _truncate(f"{product} follow up for {brand}", 78)
    if product:
        return _truncate(f"{product} follow up from Iconic Apparel House", 78)
    if brand:
        return _truncate(f"Follow up for {brand}", 78)
    return "Quick follow up from Iconic Apparel House"


def _body(lead, brain):
    contact = _text(getattr(lead, "contact_name", "")) or "there"
    brand = _text(getattr(lead, "account_brand", ""))
    product = _text(getattr(lead, "product_interest", "")) or _text(getattr(lead, "product_category", ""))
    suggested_next_step = _truncate(brain.get("suggested_next_step", ""), 180)
    request_line = _request_line(brain.get("missing_info", []))

    intro_target = product or brand or "your request"
    intro_line = f"I wanted to follow up on {intro_target}."
    if product and brand:
        intro_line = f"I wanted to follow up on {product} for {brand}."

    body_lines = [
        f"Hello {contact},",
        "",
        intro_line,
    ]

    if request_line:
        body_lines.extend(["", request_line])
    elif suggested_next_step:
        body_lines.extend(["", _truncate(suggested_next_step, 180)])

    body_lines.extend(
        [
            "",
            "Thank you,",
            "Iconic Apparel House",
        ]
    )
    return "\n".join(body_lines)


def build_iconic_ai_brain_email_draft(*, lead, brain):
    """
    Build a short, plain-text draft for the native mailto compose flow.

    This helper is intentionally read-only. It does not send mail, log drafts,
    create records, or call external services.
    """
    subject = _subject(lead)
    body = _body(lead, brain or {})
    email = _text(getattr(lead, "email", ""))
    subject_q = quote(subject, safe="")
    body_q = quote(body, safe="")
    mailto_url = f"mailto:{email}?subject={subject_q}&body={body_q}"
    return {
        "subject": subject,
        "body": body,
        "mailto_url": mailto_url,
    }
