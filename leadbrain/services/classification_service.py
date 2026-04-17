def _text(value):
    if value is None:
        return ""
    return str(value).strip()


def _row_data(company):
    if isinstance(company, dict):
        return company
    return {
        "company_name": _text(getattr(company, "company_name", "")),
        "website": _text(getattr(company, "website", "")),
        "email": _text(getattr(company, "email", "")),
        "phone": _text(getattr(company, "phone", "")),
        "country": _text(getattr(company, "country", "")),
        "city": _text(getattr(company, "city", "")),
        "product_interest": _text(getattr(company, "product_interest", "")),
        "company_name_alt": _text(getattr(company, "company_name", "")),
        "raw_row_json": getattr(company, "raw_row_json", {}) or {},
    }


def _business_type_from_research(research_data):
    return _text(research_data.get("business_type_detected", ""))


def score_company(research_data, row_data):
    real_score = 0
    website_status = _text(research_data.get("website_status", ""))
    if website_status == "live":
        real_score += 22
    elif website_status == "redirect":
        real_score += 18

    if _text(research_data.get("official_website_found", "")):
        real_score += 5
    if _text(research_data.get("search_summary", "")):
        real_score += 3
    if _text(research_data.get("linkedin_url_found", "")):
        real_score += 3
    real_score = min(real_score, 30)

    apparel_signals = research_data.get("apparel_signals", []) or []
    apparel_score = min(30, len(apparel_signals) * 8)
    if _business_type_from_research(research_data) in {
        "Apparel Brand",
        "Manufacturer / Private Label",
        "Uniform Supplier",
        "Merch Brand",
    }:
        apparel_score = min(30, apparel_score + 6)

    fit_score = 0
    combined_text = " ".join(
        [
            _text(research_data.get("business_description", "")),
            _text(research_data.get("search_summary", "")),
            _business_type_from_research(research_data),
        ]
    ).lower()
    fit_terms = ["private label", "manufacturer", "custom", "merch", "uniform", "brand", "sample", "production"]
    fit_score += sum(4 for term in fit_terms if term in combined_text)
    fit_score = min(fit_score, 20)

    contact_score = 0
    if _text(row_data.get("email", "")) or _text(research_data.get("public_email_found", "")):
        contact_score += 10
    if _text(row_data.get("phone", "")) or _text(research_data.get("public_phone_found", "")):
        contact_score += 5
    if _text(research_data.get("linkedin_url_found", "")):
        contact_score += 3
    if _text(research_data.get("possible_contact_title", "")) or _text(research_data.get("possible_contact_name", "")):
        contact_score += 2
    contact_score = min(contact_score, 20)

    return max(0, min(100, real_score + apparel_score + fit_score + contact_score))


def map_fit_label(score):
    if score >= 75:
        return "good_fit"
    if score >= 50:
        return "possible_fit"
    return "weak_fit"


def _suggested_action(fit_label, row_data, research_data):
    has_email = bool(_text(row_data.get("email", "")) or _text(research_data.get("public_email_found", "")))
    has_phone = bool(_text(row_data.get("phone", "")) or _text(research_data.get("public_phone_found", "")))
    has_linkedin = bool(_text(research_data.get("linkedin_url_found", "")))

    if fit_label == "good_fit":
        if has_email:
            return "Good for Custom Pitch"
        if has_phone:
            return "Call First"
        if has_linkedin:
            return "Find Buyer Contact"
        return "Review Manually"

    if fit_label == "possible_fit":
        if has_email:
            return "Email First"
        if has_phone:
            return "Call First"
        if has_linkedin:
            return "Find Buyer Contact"
        return "Review Manually"

    if has_email or has_phone or has_linkedin:
        return "Review Manually"
    return "Skip"


def _best_contact_title(research_data):
    title = _text(research_data.get("possible_contact_title", ""))
    if title:
        return title

    business_type = _business_type_from_research(research_data)
    if business_type in {"Apparel Brand", "Merch Brand"}:
        return "Buyer"
    if business_type == "Manufacturer / Private Label":
        return "Sourcing Manager"
    return ""


def classify_company(company, research_data):
    row_data = _row_data(company)
    score = score_company(research_data, row_data)
    fit_label = map_fit_label(score)
    business_type = _business_type_from_research(research_data)

    if not business_type:
        if research_data.get("apparel_signals"):
            business_type = "Apparel Related Business"
        elif _text(research_data.get("official_website_found", "")):
            business_type = "General Business"

    has_real_signals = bool(
        _text(research_data.get("official_website_found", ""))
        or _text(research_data.get("linkedin_url_found", ""))
        or _text(research_data.get("search_summary", ""))
    )
    apparel_signal_text = ", ".join(research_data.get("apparel_signals", [])[:3]) or "limited apparel signals"

    if fit_label == "good_fit":
        fit_reason = "Public data suggests a real active business with strong apparel or custom manufacturing relevance."
        ai_summary = f"Looks like a strong outreach target with {apparel_signal_text} and usable public business signals."
    elif fit_label == "possible_fit":
        fit_reason = "The business appears real and shows some matching signals, but more manual review is needed."
        ai_summary = f"Could be worth outreach. Public research shows {apparel_signal_text}, but the fit is not fully confirmed yet."
    else:
        fit_reason = "Public signals are weak, unclear, or not strongly related to apparel manufacturing needs."
        ai_summary = "This record needs manual review or can be skipped if stronger targets are available."

    if not has_real_signals:
        fit_reason = "Public business signals are limited, so the company could not be confidently verified."

    return {
        "business_type": business_type,
        "fit_label": fit_label,
        "fit_score": score,
        "fit_reason": fit_reason,
        "ai_summary": ai_summary,
        "suggested_action": _suggested_action(fit_label, row_data, research_data),
        "best_contact_title": _best_contact_title(research_data),
    }

