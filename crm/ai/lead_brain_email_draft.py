import re
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
    return _text(getattr(lead, "account_brand", "")) or _text(getattr(lead, "company_name", ""))


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


def _missing_info_labels():
    return {
        "Phone": "your best phone number",
        "Website": "your website or brand page",
        "Product interest": "the product or style you need",
        "Order quantity": "your target order quantity",
        "Budget": "your budget range",
        "Timeline": "your target timeline",
    }


def _request_items(items, limit=2):
    labels = _missing_info_labels()
    prompts = []
    for item in _clean_missing_info(items):
        prompts.append(labels.get(item, item.lower()))
        if len(prompts) >= limit:
            break
    return prompts


def _request_line_from_items(items):
    if not items:
        return ""
    if len(items) == 1:
        return f"Could you share {items[0]}?"
    return f"Could you share {items[0]} and {items[1]}?"


def _request_line(brain):
    prompts = _request_items(brain.get("missing_info", []))
    if prompts:
        return _request_line_from_items(prompts)

    suggested = _text(brain.get("suggested_next_step", "")).lower()
    if any(token in suggested for token in ("tech pack", "reference", "photo", "image", "sample")):
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


def _numbers_in_text(value):
    raw = _text(value).replace(",", "")
    return [int(token) for token in re.findall(r"\d+", raw)]


def _quantity_signal(lead):
    candidates = []
    for field_name in ("order_quantity", "target_order_volume_min", "target_order_volume_max"):
        candidates.extend(_numbers_in_text(getattr(lead, field_name, "")))
    if not candidates:
        return 0
    return max(candidates)


def _fit_score(lead):
    try:
        return int(_text(getattr(lead, "brand_fit_score", "")) or 0)
    except Exception:
        return 0


def _serious_lead_score(lead):
    score = 0
    if _quantity_signal(lead) >= 500:
        score += 2
    if _product_name(lead):
        score += 1
    if _website_name(lead):
        score += 1
    if _fit_score(lead) >= 70:
        score += 1
    if _text(getattr(lead, "qualification_status", "")) in {"Strong Fit", "Outreach Ready", "Qualified"}:
        score += 1
    if _text(getattr(lead, "last_reply_date", "")):
        score += 1
    return score


def _is_serious_lead(lead):
    return _serious_lead_score(lead) >= 3


def _tone_text(lead, canada_text, us_text, neutral_text):
    market = _market_bucket(lead)
    if market == "canada":
        return canada_text
    if market == "us":
        return us_text
    return neutral_text


def _contact_name(lead):
    return _text(getattr(lead, "contact_name", "")) or "there"


def _inquiry_phrase(lead):
    brand = _brand_name(lead)
    product = _product_name(lead)
    website = _website_name(lead)

    if product and brand:
        return f"your {product} inquiry for {brand}"
    if product:
        return f"your {product} inquiry"
    if brand:
        return f"{brand}"
    if website:
        return f"your inquiry for {website}"
    return "your inquiry"


def _line_hello(lead):
    return f"Hello {_contact_name(lead)},"


def _general_cta(lead):
    return _tone_text(
        lead,
        "If you send that over, I can guide you on the next steps.",
        "Once I have that, I can give you a clearer idea of pricing and next steps.",
        "If easier, just reply with the details here and I will take it from there.",
    )


def _pricing_cta(lead):
    return _tone_text(
        lead,
        "Once I have that, I can guide you on pricing and the next steps.",
        "Once I have that, I can give you a clearer idea of pricing and next steps.",
        "Once I have that, I can give you a clearer idea of pricing and next steps.",
    )


def _sampling_cta(lead):
    return _tone_text(
        lead,
        "If you send that over, I can guide the next step for sampling.",
        "Once I have that, I can outline the best next step for sampling.",
        "If you send that over, I can guide the next step for sampling.",
    )


def _call_cta(lead):
    return _tone_text(
        lead,
        "If a short call is easier, send over a time that works for you.",
        "If a short call works better, send over a time that suits you.",
        "If a short call helps, send over a time that works for you.",
    )


def _subject_for_follow_up(lead, number):
    brand = _brand_name(lead)
    product = _product_name(lead)

    if number == 1:
        if product:
            return _truncate(f"Next steps for {product}", 70)
        if brand:
            return _truncate(f"Quick follow up for {brand}", 70)
        return "Follow up on your inquiry"
    if number == 2:
        if brand:
            return _truncate(f"Quick follow up for {brand}", 70)
        return "Quick follow up"
    if number == 3:
        return "Checking back on your inquiry"
    if number == 4:
        return "Following up on your project"
    return "Closing the loop for now"


def _variant(subject, *lines):
    body = "\n".join([_text(line) for line in lines if _text(line)])
    return {
        "subject": _truncate(subject, 70),
        "body": body,
    }


def _follow_up_variants(lead, brain):
    inquiry = _inquiry_phrase(lead)
    request_line = _request_line(brain)

    return {
        "follow_up_1": _variant(
            _subject_for_follow_up(lead, 1),
            _line_hello(lead),
            "Hope you're doing well.",
            f"I wanted to follow up on {inquiry}.",
            request_line,
            _general_cta(lead),
            "Thank you,",
            "Iconic Apparel House",
        ),
        "follow_up_2": _variant(
            _subject_for_follow_up(lead, 2),
            _line_hello(lead),
            "Hope you're doing well.",
            f"Just checking back on {inquiry}.",
            request_line,
            _pricing_cta(lead),
            "Thank you,",
            "Iconic Apparel House",
        ),
        "follow_up_3": _variant(
            _subject_for_follow_up(lead, 3),
            _line_hello(lead),
            "Hope you're doing well.",
            f"Reaching back out on {inquiry} in case now is a better time.",
            "If easier, just reply here with the key detail you need help with.",
            _tone_text(
                lead,
                "I can guide you from there.",
                "I can move the next step forward from there.",
                "I can take it from there.",
            ),
            "Thank you,",
            "Iconic Apparel House",
        ),
        "follow_up_4": _variant(
            _subject_for_follow_up(lead, 4),
            _line_hello(lead),
            "Hope you're doing well.",
            f"No rush on {inquiry}.",
            "If the timing is not right, just let me know and I can reconnect later.",
            _tone_text(
                lead,
                "Happy to circle back when the timing is better.",
                "Happy to reconnect when the timing makes more sense.",
                "Happy to reconnect when it is a better time.",
            ),
            "Thank you,",
            "Iconic Apparel House",
        ),
        "follow_up_5": _variant(
            _subject_for_follow_up(lead, 5),
            _line_hello(lead),
            "Hope you're doing well.",
            f"I will close the loop on {inquiry} for now.",
            "If this becomes a priority later, just reply here and I can pick it back up.",
            _tone_text(
                lead,
                "Happy to help whenever the timing works.",
                "Happy to help when you are ready to move forward.",
                "Happy to help when you are ready.",
            ),
            "Thank you,",
            "Iconic Apparel House",
        ),
    }


def _pricing_request(brain):
    prompts = _request_items(brain.get("missing_info", []))
    pricing_prompts = [item for item in prompts if item in {"your target order quantity", "the product or style you need"}]
    if pricing_prompts:
        return _request_line_from_items(pricing_prompts[:2])

    suggested = _text(brain.get("suggested_next_step", "")).lower()
    if "timeline" in suggested or "delivery" in suggested:
        return "Could you share your target quantity and timeline?"
    return "Could you share your target order quantity and product details?"


def _sample_request(brain):
    suggested = _text(brain.get("suggested_next_step", "")).lower()
    if any(token in suggested for token in ("tech pack", "reference", "photo", "image", "sample")):
        return "Could you share any tech pack or reference images?"
    return "Could you share a style reference or spec for the sample?"


def _high_ticket_variants(lead, brain):
    inquiry = _inquiry_phrase(lead)
    return {
        "serious_lead_next_step": _variant(
            "Planning next steps",
            _line_hello(lead),
            "Hope you're doing well.",
            f"{_brand_name(lead) or 'This project'} looks like a strong fit for planning next steps.",
            _pricing_request(brain),
            _tone_text(
                lead,
                "Once I have that, I can guide you on the best next step for production.",
                "Once I have that, I can map out the next step for pricing and production.",
                "Once I have that, I can map out the best next step for pricing and production.",
            ),
            "Thank you,",
            "Iconic Apparel House",
        ),
        "pricing_clarity": _variant(
            "Pricing and next steps",
            _line_hello(lead),
            "Hope you're doing well.",
            f"I can give you a clearer price direction for {inquiry}.",
            _pricing_request(brain),
            _pricing_cta(lead),
            "Thank you,",
            "Iconic Apparel House",
        ),
        "sampling_next_step": _variant(
            "Sample planning",
            _line_hello(lead),
            "Hope you're doing well.",
            f"A sample can be the fastest way to move {inquiry} forward.",
            _sample_request(brain),
            _sampling_cta(lead),
            "Thank you,",
            "Iconic Apparel House",
        ),
        "warm_lead_call_suggestion": _variant(
            "Quick call on next steps",
            _line_hello(lead),
            "Hope you're doing well.",
            f"A short call may be the easiest way to align on {inquiry}.",
            _call_cta(lead),
            _tone_text(
                lead,
                "I can guide the next steps from there.",
                "I can move the next step forward from there.",
                "I can guide the next steps from there.",
            ),
            "Thank you,",
            "Iconic Apparel House",
        ),
    }


def _objection_variants(lead, brain):
    pricing_request = _pricing_request(brain)
    return {
        "objection_not_ready_yet": _variant(
            "No problem for now",
            _line_hello(lead),
            "Hope you're doing well.",
            "No problem at all if the timing is not right yet.",
            "If you have a rough timeline, just reply with that and I can follow up closer to it.",
            _tone_text(
                lead,
                "Happy to reconnect when the timing works better.",
                "Happy to reconnect when the timing makes more sense.",
                "Happy to reconnect when the timing is better.",
            ),
            "Thank you,",
            "Iconic Apparel House",
        ),
        "objection_just_looking_for_price": _variant(
            "Pricing and next steps",
            _line_hello(lead),
            "Hope you're doing well.",
            "I can give you a useful price direction.",
            pricing_request,
            _pricing_cta(lead),
            "Thank you,",
            "Iconic Apparel House",
        ),
        "objection_moq_concern": _variant(
            "Quantity planning",
            _line_hello(lead),
            "Hope you're doing well.",
            "We can usually guide the right starting quantity based on the item and finish.",
            "Could you share the quantity range you are considering?",
            _tone_text(
                lead,
                "If you send that over, I can guide the best next step.",
                "Once I have that, I can point you to the most practical next step.",
                "If you send that over, I can guide the best next step.",
            ),
            "Thank you,",
            "Iconic Apparel House",
        ),
        "objection_timeline_concern": _variant(
            "Timeline and next steps",
            _line_hello(lead),
            "Hope you're doing well.",
            "We can usually tell you quickly what is realistic for timing.",
            "Could you share your target delivery date?",
            _tone_text(
                lead,
                "If you send that over, I can guide the next step.",
                "Once I have that, I can tell you the clearest next step.",
                "If you send that over, I can guide the next step.",
            ),
            "Thank you,",
            "Iconic Apparel House",
        ),
        "objection_wants_to_think_about_it": _variant(
            "Take your time",
            _line_hello(lead),
            "Hope you're doing well.",
            "Of course, take your time on this.",
            "If helpful, just reply with the one question still on your mind.",
            _tone_text(
                lead,
                "I am happy to answer it clearly and keep this simple.",
                "I can answer it directly and keep the next step simple.",
                "I can answer it directly and keep this simple.",
            ),
            "Thank you,",
            "Iconic Apparel House",
        ),
        "objection_not_ready_for_call": _variant(
            "No call needed",
            _line_hello(lead),
            "Hope you're doing well.",
            "No problem, we can keep this over email.",
            "If easier, just send the key details here and I can take it from there.",
            _tone_text(
                lead,
                "Happy to keep it simple and move at your pace.",
                "Happy to keep it efficient and move it forward by email.",
                "Happy to keep it simple by email.",
            ),
            "Thank you,",
            "Iconic Apparel House",
        ),
        "objection_comparing_suppliers": _variant(
            "Happy to help compare",
            _line_hello(lead),
            "Hope you're doing well.",
            "That makes sense if you are comparing suppliers.",
            "If you share the main point you are comparing, I can give you a direct answer on fit and next steps.",
            _tone_text(
                lead,
                "Happy to make that easier for you.",
                "I can keep that clear and practical for you.",
                "Happy to make that clearer for you.",
            ),
            "Thank you,",
            "Iconic Apparel House",
        ),
        "objection_budget_concern": _variant(
            "Budget and next steps",
            _line_hello(lead),
            "Hope you're doing well.",
            "Understood if budget is a concern right now.",
            "If you share the range you want to stay within, I can suggest the most practical next step.",
            _tone_text(
                lead,
                "I can guide you toward the most realistic option from there.",
                "I can point you to the clearest next step from there.",
                "I can suggest the clearest next step from there.",
            ),
            "Thank you,",
            "Iconic Apparel House",
        ),
    }


def _all_variants(lead, brain):
    variants = {}
    variants.update(_follow_up_variants(lead, brain))
    variants.update(_high_ticket_variants(lead, brain))
    variants.update(_objection_variants(lead, brain))
    return variants


def _recommended_mode(lead, brain, variants):
    if _is_serious_lead(lead):
        suggested = _text(brain.get("suggested_next_step", "")).lower()
        missing = set(_clean_missing_info(brain.get("missing_info", [])))
        if any(token in suggested for token in ("sample", "tech pack", "reference")):
            return "sampling_next_step"
        if missing.intersection({"Order quantity", "Product interest"}) or any(
            token in suggested for token in ("price", "pricing", "quote", "quantity", "budget")
        ):
            return "pricing_clarity"
        return "serious_lead_next_step"
    return "follow_up_1"


def build_iconic_ai_brain_email_draft(*, lead, brain):
    """
    Build a short, plain-text draft for the native mailto compose flow.

    This helper is intentionally read-only. It does not send mail, log drafts,
    create records, or call external services.
    """
    brain = brain or {}
    reply_variants = _all_variants(lead, brain)
    recommended_mode = _recommended_mode(lead, brain, reply_variants)
    current = reply_variants[recommended_mode]
    subject = current["subject"]
    body = current["body"]
    email = _text(getattr(lead, "email", ""))
    subject_q = quote(subject, safe="").replace("+", "%20")
    body_q = quote(body, safe="").replace("+", "%20")
    mailto_url = f"mailto:{email}?subject={subject_q}&body={body_q}"
    return {
        "subject": subject,
        "body": body,
        "mailto_url": mailto_url,
        "recommended_mode": recommended_mode,
        "reply_variants": reply_variants,
    }
