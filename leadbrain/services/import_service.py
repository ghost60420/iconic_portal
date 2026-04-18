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


def _existing_identity_keys(rows) -> set[str]:
    websites = {_key_text(row.get("website", "")) for row in rows if _key_text(row.get("website", ""))}
    emails = {_key_text(row.get("email", "")) for row in rows if _key_text(row.get("email", ""))}
    company_names = {_key_text(row.get("company_name", "")) for row in rows if _key_text(row.get("company_name", ""))}

    if not (websites or emails or company_names):
        return set()

    queryset = LeadBrainCompany.objects.annotate(
        company_name_key=Lower("company_name"),
        website_key=Lower("website"),
        email_key=Lower("email"),
    ).filter(
        Q(website_key__in=websites) | Q(email_key__in=emails) | Q(company_name_key__in=company_names)
    )

    keys = set()
    for record in queryset.values("company_name_key", "website_key", "email_key"):
        keys.update(
            _row_identity(
                _key_text(record.get("company_name_key")),
                _key_text(record.get("website_key")),
                _key_text(record.get("email_key")),
            )
        )
    return keys


def prepare_import_rows(rows):
    existing_keys = _existing_identity_keys(rows)
    seen_keys = set()
    imported_rows = []
    skipped_duplicate_rows = 0
    invalid_rows = 0

    for row in rows:
        company_name = _key_text(row.get("company_name", ""))
        website = _key_text(row.get("website", ""))
        email = _key_text(row.get("email", ""))
        phone = _key_text(row.get("phone", ""))
        identity_keys = _row_identity(company_name, website, email)

        if not (identity_keys or phone):
            invalid_rows += 1
            continue

        if identity_keys and ((identity_keys & existing_keys) or (identity_keys & seen_keys)):
            skipped_duplicate_rows += 1
            continue

        seen_keys.update(identity_keys)
        imported_rows.append(row)

    return {
        "rows": imported_rows,
        "imported_rows": len(imported_rows),
        "skipped_duplicate_rows": skipped_duplicate_rows,
        "invalid_rows": invalid_rows,
    }
