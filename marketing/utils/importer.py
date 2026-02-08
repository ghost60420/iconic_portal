import csv
import io

from marketing.models import Contact, ContactListMembership


def _norm(v: str) -> str:
    return (v or "").strip()


def _bool(val: str) -> bool:
    val = (val or "").strip().lower()
    return val in {"1", "true", "yes", "y", "on"}


def import_contacts_from_csv(file_obj, contact_list=None):
    data = file_obj.read()
    if isinstance(data, bytes):
        text = data.decode("utf-8", errors="ignore")
    else:
        text = str(data)

    reader = csv.DictReader(io.StringIO(text))

    created = 0
    updated = 0
    skipped = 0
    errors = []

    for row in reader:
        email = _norm(row.get("email", "")).lower()
        if not email:
            skipped += 1
            continue

        contact, was_created = Contact.objects.get_or_create(email=email)

        # Only fill missing data
        updates = {}
        for field in [
            "first_name",
            "last_name",
            "company",
            "phone",
            "website",
            "city",
            "state",
            "country",
            "industry",
            "job_title",
        ]:
            if not getattr(contact, field):
                val = _norm(row.get(field, ""))
                if val:
                    updates[field] = val

        consent = _norm(row.get("consent_status", ""))
        if consent and contact.consent_status != "opted_out":
            updates["consent_status"] = consent

        source = _norm(row.get("source", ""))
        if source and not contact.source:
            updates["source"] = source

        tags_raw = _norm(row.get("tags", ""))
        if tags_raw and not contact.tags:
            updates["tags"] = [t.strip() for t in tags_raw.split(",") if t.strip()]

        dnc_val = _bool(row.get("do_not_contact", ""))
        if dnc_val:
            updates["do_not_contact"] = True

        if updates:
            for k, v in updates.items():
                setattr(contact, k, v)
            contact.save()
            if not was_created:
                updated += 1

        if was_created:
            created += 1

        if contact_list:
            ContactListMembership.objects.get_or_create(contact_list=contact_list, contact=contact)

    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
    }
