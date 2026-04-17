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


def _brand_name(lead):
    return (
        _text(getattr(lead, "account_brand", ""))
        or _text(getattr(lead, "company_name", ""))
    )


def _product_name(lead):
    return _text(getattr(lead, "product_interest", "")) or _text(getattr(lead, "product_category", ""))


def _website_name(lead):
    return _text(getattr(lead, "website", "")) or _text(getattr(lead, "company_website", ""))


def _market_bucket(lead):
    values = []
    market_display = getattr(lead, "get_market_display", None)
    if callable(market_display):
        try:
            values.append(market_display())
        except Exception:
            pass

    for field_name in ("market", "country", "shipping_country"):
        values.append(getattr(lead, field_name, ""))

    for value in values:
        normalized = _text(value).lower().replace(".", "").replace("-", " ")
        normalized = " ".join(normalized.split())
        if normalized in {"ca", "canada", "canadian"}:
            return "canada"
        if normalized in {"us", "usa", "united states", "united states of america", "american"}:
            return "us"
    return "neutral"


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


def _request_items(items):
    labels = {
        "Phone": "your best phone number",
        "Website": "your website or brand page",
        "Product interest": "the product or style you need",
        "Order quantity": "your target order quantity",
    }
    prompts = []
    for item in _clean_missing_info(items):
        prompts.append(labels.get(item, item.lower()))
        if len(prompts) >= 2:
            break
    return prompts


def _request_line(brain):
    prompts = _request_items(brain.get("missing_info", []))
    if prompts:
        if len(prompts) == 1:
            return f"Could you share {prompts[0]}?"
        return f"Could you share {prompts[0]} and {prompts[1]}?"

    suggested = _text(brain.get("suggested_next_step", "")).lower()
    if any(token in suggested for token in ("tech pack", "reference", "photo", "image")):
        return "Could you share any tech pack or reference images?"
    if any(token in suggested for token in ("quantity", "volume", "moq")):
        return "Could you share your target order quantity?"
    if any(token in suggested for token in ("product", "style", "category")):
        return "Could you share the product or style you have in mind?"
    if any(token in suggested for token in ("timeline", "date", "delivery")):
        return "Could you share your target timeline?"
    if any(token in suggested for token in ("website", "brand page")):
        return "Could you share your website or brand page?"
    return "Could you share a bit more detail on what you need?"


def _subject(lead):
    brand = _brand_name(lead)
    product = _product_name(lead)

    if product:
        return _truncate(f"Next steps for {product}", 70)
    if brand:
        return _truncate(f"Quick follow up for {brand}", 70)
    return "Follow up on your inquiry"


def _intro_line(lead):
    brand = _brand_name(lead)
    product = _product_name(lead)
    website = _website_name(lead)

    if product and brand:
        return f"I wanted to follow up on your {product} inquiry for {brand}."
    if product:
        return f"I wanted to follow up on your {product} inquiry."
    if brand:
        return f"I wanted to follow up regarding {brand}."
    if website:
        return f"I wanted to follow up regarding your inquiry for {website}."
    return "I wanted to follow up on your inquiry."


def _cta_line(lead):
    market = _market_bucket(lead)
    if market == "canada":
        return "If you send that over, I can guide you on the next steps."
    if market == "us":
        return "Once I have that, I can give you a clearer idea of pricing and next steps."
    return "If easier, just reply with the details here and I will take it from there."


def _body(lead, brain):
    contact = _text(getattr(lead, "contact_name", "")) or "there"
    ask_line = _request_line(brain or {})
    cta_line = _cta_line(lead)

    body_lines = [
        f"Hello {contact},",
        "Hope you're doing well.",
        _intro_line(lead),
        ask_line,
        cta_line,
        "Thank you,",
        "Iconic Apparel House",
    ]
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
    subject_q = quote(subject, safe="").replace("+", "%20")
    body_q = quote(body, safe="").replace("+", "%20")
    mailto_url = f"mailto:{email}?subject={subject_q}&body={body_q}"
    return {
        "subject": subject,
        "body": body,
        "mailto_url": mailto_url,
    }
