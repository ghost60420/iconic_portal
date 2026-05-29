from django.core.exceptions import ValidationError
from django.db.models import Q

from crm.models import ProductReferenceImage


MAX_REFERENCE_IMAGES = 3
REFERENCE_IMAGE_SLOTS = (1, 2, 3)


def _request_user(user):
    if getattr(user, "is_authenticated", False):
        return user
    return None


def reference_image_payload_from_cleaned_data(cleaned_data):
    return [
        {
            "slot": slot,
            "image": cleaned_data.get(f"reference_image_{slot}"),
            "caption": cleaned_data.get(f"reference_caption_{slot}", ""),
        }
        for slot in REFERENCE_IMAGE_SLOTS
    ]


def reference_image_payload_from_request(request):
    return [
        {
            "slot": slot,
            "image": request.FILES.get(f"reference_image_{slot}"),
            "caption": request.POST.get(f"reference_caption_{slot}", ""),
        }
        for slot in REFERENCE_IMAGE_SLOTS
    ]


def save_reference_images_for_lead(lead, payload, user=None):
    saved_images = []
    upload_count = 0
    acting_user = _request_user(user)

    for item in payload:
        slot = int(item.get("slot") or 0)
        if slot not in REFERENCE_IMAGE_SLOTS:
            raise ValidationError("Only three product reference images are allowed.")

        image = item.get("image")
        caption = (item.get("caption") or "").strip()
        existing = ProductReferenceImage.objects.filter(lead=lead, slot=slot).first()

        if image:
            upload_count += 1
            if existing and existing.image:
                existing.image.delete(save=False)
                existing.delete()

            reference_image = ProductReferenceImage.objects.create(
                lead=lead,
                slot=slot,
                image=image,
                caption=caption,
                uploaded_by=acting_user,
            )
            saved_images.append(reference_image)
        elif existing and caption != existing.caption:
            existing.caption = caption
            existing.save(update_fields=["caption"])
            saved_images.append(existing)

    if ProductReferenceImage.objects.filter(lead=lead).count() > MAX_REFERENCE_IMAGES:
        raise ValidationError("Only three product reference images are allowed.")

    return saved_images, upload_count


def link_reference_images_to_opportunity(lead, opportunity):
    if not lead or not opportunity:
        return 0
    return ProductReferenceImage.objects.filter(lead=lead).update(opportunity=opportunity)


def link_reference_images_to_production(opportunity=None, production_order=None):
    if not production_order:
        return 0

    filters = Q()
    if opportunity:
        filters |= Q(opportunity=opportunity)
        if getattr(opportunity, "lead_id", None):
            filters |= Q(lead_id=opportunity.lead_id)
    if getattr(production_order, "lead_id", None):
        filters |= Q(lead_id=production_order.lead_id)

    if not filters:
        return 0
    return ProductReferenceImage.objects.filter(filters).update(production_order=production_order)


def reference_images_for_lead(lead):
    if not lead:
        return ProductReferenceImage.objects.none()
    return (
        ProductReferenceImage.objects.filter(lead=lead)
        .select_related("uploaded_by", "lead", "opportunity", "production_order")
        .order_by("slot", "uploaded_at", "id")[:MAX_REFERENCE_IMAGES]
    )


def reference_images_for_opportunity(opportunity):
    if not opportunity:
        return ProductReferenceImage.objects.none()

    filters = Q(opportunity=opportunity)
    if getattr(opportunity, "lead_id", None):
        filters |= Q(lead_id=opportunity.lead_id)
    return (
        ProductReferenceImage.objects.filter(filters)
        .select_related("uploaded_by", "lead", "opportunity", "production_order")
        .distinct()
        .order_by("slot", "uploaded_at", "id")[:MAX_REFERENCE_IMAGES]
    )


def reference_images_for_production(production_order):
    if not production_order:
        return ProductReferenceImage.objects.none()

    filters = Q(production_order=production_order)
    if getattr(production_order, "opportunity_id", None):
        filters |= Q(opportunity_id=production_order.opportunity_id)
    if getattr(production_order, "lead_id", None):
        filters |= Q(lead_id=production_order.lead_id)
    return (
        ProductReferenceImage.objects.filter(filters)
        .select_related("uploaded_by", "lead", "opportunity", "production_order")
        .distinct()
        .order_by("slot", "uploaded_at", "id")[:MAX_REFERENCE_IMAGES]
    )
