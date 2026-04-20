from django.db.models import Q
from django.db.models.functions import Lower

from leadbrain.models import LeadBrainCompany


def _key_text(value) -> str:
    return str(value or "").strip().lower()


def _row_identity(company_name: str, website: str, email: str) -> set[str]:
    keys = set()
    if website:
        keys.add(f"website:{website}")
    if email:
        keys.add(f"email:{email}")
    if company_name and website:
        keys.add(f"name_website:{company_name}|{website}")
    if company_name and email:
        keys.add(f"name_email:{company_name}|{email}")
    return keys


def _existing_identity_maps(rows):
    websites = {_key_text(row.get("website", "")) for row in rows if _key_text(row.get("website", ""))}
    emails = {_key_text(row.get("email", "")) for row in rows if _key_text(row.get("email", ""))}

    if not (websites or emails):
        return {
            "website": {},
            "email": {},
            "name_website": {},
            "name_email": {},
        }

    queryset = LeadBrainCompany.objects.annotate(
        company_name_key=Lower("company_name"),
        website_key=Lower("website"),
        email_key=Lower("email"),
    ).filter(
        Q(website_key__in=websites) | Q(email_key__in=emails)
    )

    maps = {
        "website": {},
        "email": {},
        "name_website": {},
        "name_email": {},
    }
    for record in queryset.values("company_name_key", "website_key", "email_key"):
        company_name = _key_text(record.get("company_name_key"))
        website = _key_text(record.get("website_key"))
        email = _key_text(record.get("email_key"))

        if website and website not in maps["website"]:
            maps["website"][website] = record
        if email and email not in maps["email"]:
            maps["email"][email] = record
        if company_name and website and (company_name, website) not in maps["name_website"]:
            maps["name_website"][(company_name, website)] = record
        if company_name and email and (company_name, email) not in maps["name_email"]:
            maps["name_email"][(company_name, email)] = record
    return maps


def _invalid_row_reason(row) -> str:
    company_name = _key_text(row.get("company_name", ""))
    website = _key_text(row.get("website", ""))
    if not company_name and not website:
        row_number = row.get("row_number", "?")
        return f"Row {row_number}: missing both company name and website."
    return ""


def _match_duplicate_reason(row, *, existing_maps, seen_maps):
    company_name = _key_text(row.get("company_name", ""))
    website = _key_text(row.get("website", ""))
    email = _key_text(row.get("email", ""))

    if website and website in existing_maps["website"]:
        return {
            "rule": "website exact match",
            "source": "existing",
            "match": existing_maps["website"][website],
        }
    if email and email in existing_maps["email"]:
        return {
            "rule": "email exact match",
            "source": "existing",
            "match": existing_maps["email"][email],
        }
    if company_name and website and (company_name, website) in existing_maps["name_website"]:
        return {
            "rule": "company name plus website",
            "source": "existing",
            "match": existing_maps["name_website"][(company_name, website)],
        }
    if company_name and email and (company_name, email) in existing_maps["name_email"]:
        return {
            "rule": "company name plus email",
            "source": "existing",
            "match": existing_maps["name_email"][(company_name, email)],
        }
    if website and website in seen_maps["website"]:
        return {
            "rule": "website exact match",
            "source": "same file",
            "match": seen_maps["website"][website],
        }
    if email and email in seen_maps["email"]:
        return {
            "rule": "email exact match",
            "source": "same file",
            "match": seen_maps["email"][email],
        }
    if company_name and website and (company_name, website) in seen_maps["name_website"]:
        return {
            "rule": "company name plus website",
            "source": "same file",
            "match": seen_maps["name_website"][(company_name, website)],
        }
    if company_name and email and (company_name, email) in seen_maps["name_email"]:
        return {
            "rule": "company name plus email",
            "source": "same file",
            "match": seen_maps["name_email"][(company_name, email)],
        }
    return None


def _remember_row_identity(row, seen_maps):
    company_name = _key_text(row.get("company_name", ""))
    website = _key_text(row.get("website", ""))
    email = _key_text(row.get("email", ""))
    if website:
        seen_maps["website"][website] = row
    if email:
        seen_maps["email"][email] = row
    if company_name and website:
        seen_maps["name_website"][(company_name, website)] = row
    if company_name and email:
        seen_maps["name_email"][(company_name, email)] = row


def _format_duplicate_example(row, duplicate_match):
    row_number = row.get("row_number", "?")
    company_name = row.get("company_name", "") or "Unnamed company"
    rule = duplicate_match["rule"]
    source = duplicate_match["source"]
    match = duplicate_match.get("match") or {}

    if source == "existing":
        matched_company = match.get("company_name_key") or company_name
        matched_website = match.get("website_key") or ""
        return (
            f"Row {row_number}: {company_name} matched existing Lead Brain data by {rule}"
            + (f" ({matched_website})." if matched_website else ".")
        )
    matched_row = match.get("row_number", "?")
    return f"Row {row_number}: {company_name} duplicated row {matched_row} in the same file by {rule}."


def prepare_import_rows(rows):
    existing_maps = _existing_identity_maps(rows)
    seen_maps = {
        "website": {},
        "email": {},
        "name_website": {},
        "name_email": {},
    }
    imported_rows = []
    skipped_duplicate_rows = 0
    invalid_rows = 0
    invalid_reasons = []
    duplicate_examples = []

    for row in rows:
        invalid_reason = _invalid_row_reason(row)
        if invalid_reason:
            invalid_rows += 1
            if len(invalid_reasons) < 5:
                invalid_reasons.append(invalid_reason)
            continue

        duplicate_match = _match_duplicate_reason(row, existing_maps=existing_maps, seen_maps=seen_maps)
        if duplicate_match:
            skipped_duplicate_rows += 1
            if len(duplicate_examples) < 5:
                duplicate_examples.append(_format_duplicate_example(row, duplicate_match))
            continue

        _remember_row_identity(row, seen_maps)
        imported_rows.append(row)

    return {
        "rows": imported_rows,
        "imported_rows": len(imported_rows),
        "skipped_duplicate_rows": skipped_duplicate_rows,
        "invalid_rows": invalid_rows,
        "invalid_reasons": invalid_reasons,
        "duplicate_examples": duplicate_examples,
    }
