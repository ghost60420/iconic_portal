from django.utils import timezone

from crm.models import Lead
from whatsapp.utils.phones import normalize_phone


def link_existing_lead(phone: str):
    phone_norm = normalize_phone(phone)
    if not phone_norm:
        return None
    return Lead.objects.filter(phone__icontains=phone_norm).order_by("-id").first()


def create_lead_from_thread(thread, name: str = ""):
    phone_norm = normalize_phone(thread.contact_phone or "")
    lead = Lead.objects.create(
        account_brand=name or thread.contact_name or "WhatsApp Lead",
        contact_name=name or thread.contact_name or "",
        phone=phone_norm,
        lead_status="New",
        source="WhatsApp",
        created_date=timezone.localdate(),
    )
    thread.linked_lead = lead
    thread.save(update_fields=["linked_lead"])
    return lead
