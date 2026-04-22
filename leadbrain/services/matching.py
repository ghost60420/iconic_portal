from django.db.models import Q

from crm.models import Lead


def _text(value):
    if value is None:
        return ""
    return str(value).strip()


def _norm_text(value):
    return _text(value).lower()


def _website_key(value):
    website = _norm_text(value)
    if not website:
        return ""
    for prefix in ("https://", "http://"):
        if website.startswith(prefix):
            website = website[len(prefix) :]
            break
    if website.startswith("www."):
        website = website[4:]
    return website.rstrip("/")


def find_matching_lead(*, website="", email="", exclude_lead_id=None):
    queryset = Lead.objects.all()
    if exclude_lead_id:
        queryset = queryset.exclude(pk=exclude_lead_id)

    email_key = _norm_text(email)
    if email_key:
        record = queryset.filter(email__iexact=email_key).order_by("id").first()
        if record:
            return record, "email exact match"

    website_key = _website_key(website)
    if website_key:
        record = queryset.filter(
            Q(website__icontains=website_key) | Q(company_website__icontains=website_key)
        ).order_by("id").first()
        if record:
            return record, "website exact match"

    return None, ""
