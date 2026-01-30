import re

def _pick(text: str, patterns):
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE | re.MULTILINE)
        if m:
            return (m.group(1) or "").strip()
    return ""

def parse_lead_from_email(subject: str, body: str, from_email: str) -> dict:
    body = (body or "").strip()
    subject = (subject or "").strip()
    from_email = (from_email or "").strip()

    data = {
        "account_brand": "",
        "contact_name": "",
        "email": from_email,
        "phone": "",
        "contact2_name": "",
        "email2": "",
        "phone2": "",
        "company_website": "",
        "country": "",
        "city": "",
        "product_interest": "",
        "order_quantity": "",
        "budget": "",
        "preferred_contact_time": "",
        "notes": "",
    }

    text = body if body else ""

    data["contact_name"] = _pick(text, [
        r"^\s*name\s*:\s*(.+)$",
        r"^\s*contact name\s*:\s*(.+)$",
        r"^\s*full name\s*:\s*(.+)$",
    ])

    data["account_brand"] = _pick(text, [
        r"^\s*brand\s*:\s*(.+)$",
        r"^\s*company\s*:\s*(.+)$",
        r"^\s*account\s*:\s*(.+)$",
    ])

    data["phone"] = _pick(text, [
        r"^\s*phone\s*:\s*(.+)$",
        r"^\s*mobile\s*:\s*(.+)$",
        r"^\s*whatsapp\s*:\s*(.+)$",
    ])

    data["company_website"] = _pick(text, [
        r"^\s*website\s*:\s*(.+)$",
        r"^\s*site\s*:\s*(.+)$",
    ])

    data["country"] = _pick(text, [
        r"^\s*country\s*:\s*(.+)$",
    ])

    data["city"] = _pick(text, [
        r"^\s*city\s*:\s*(.+)$",
    ])

    data["product_interest"] = _pick(text, [
        r"^\s*products?\s*looking\s*for\s*:\s*(.+)$",
        r"^\s*product\s*interest\s*:\s*(.+)$",
        r"^\s*items\s*:\s*(.+)$",
    ])

    data["order_quantity"] = _pick(text, [
        r"^\s*order\s*quantity\s*:\s*(.+)$",
        r"^\s*quantity\s*:\s*(.+)$",
        r"^\s*qty\s*:\s*(.+)$",
    ])

    data["budget"] = _pick(text, [
        r"^\s*budget\s*:\s*(.+)$",
    ])

    data["preferred_contact_time"] = _pick(text, [
        r"^\s*preferred\s*contact\s*time\s*:\s*(.+)$",
        r"^\s*preferred\s*time\s*:\s*(.+)$",
    ])

    data["contact2_name"] = _pick(text, [
        r"^\s*second\s*contact\s*name\s*:\s*(.+)$",
        r"^\s*contact\s*2\s*name\s*:\s*(.+)$",
    ])

    data["email2"] = _pick(text, [
        r"^\s*second\s*email\s*:\s*(.+)$",
        r"^\s*email\s*2\s*:\s*(.+)$",
    ])

    data["phone2"] = _pick(text, [
        r"^\s*second\s*phone\s*:\s*(.+)$",
        r"^\s*phone\s*2\s*:\s*(.+)$",
    ])

    notes_parts = []
    if subject:
        notes_parts.append(f"Subject: {subject}")
    if from_email:
        notes_parts.append(f"From: {from_email}")
    if text:
        notes_parts.append("")
        notes_parts.append(text[:4000])

    data["notes"] = "\n".join(notes_parts).strip()

    return data