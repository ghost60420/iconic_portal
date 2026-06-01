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


def _first_images_by_key(queryset, key_name):
    images = {}
    for image in queryset.order_by(key_name, "slot", "uploaded_at", "id"):
        key = getattr(image, key_name)
        if key and key not in images:
            images[key] = image
    return images


def attach_primary_reference_images_to_leads(leads):
    lead_list = list(leads)
    lead_ids = [lead.id for lead in lead_list if getattr(lead, "id", None)]
    images_by_lead = _first_images_by_key(
        ProductReferenceImage.objects.filter(lead_id__in=lead_ids),
        "lead_id",
    )
    for lead in lead_list:
        lead.primary_reference_image = images_by_lead.get(lead.id)
    return lead_list


def attach_primary_reference_images_to_opportunities(opportunities):
    opportunity_list = list(opportunities)
    opportunity_ids = [opportunity.id for opportunity in opportunity_list if getattr(opportunity, "id", None)]
    lead_ids = [
        opportunity.lead_id
        for opportunity in opportunity_list
        if getattr(opportunity, "lead_id", None)
    ]
    images_by_opportunity = _first_images_by_key(
        ProductReferenceImage.objects.filter(opportunity_id__in=opportunity_ids),
        "opportunity_id",
    )
    images_by_lead = _first_images_by_key(
        ProductReferenceImage.objects.filter(lead_id__in=lead_ids),
        "lead_id",
    )
    for opportunity in opportunity_list:
        opportunity.primary_reference_image = (
            images_by_opportunity.get(opportunity.id)
            or images_by_lead.get(getattr(opportunity, "lead_id", None))
        )
    return opportunity_list


def attach_primary_reference_images_to_production_orders(orders):
    order_list = list(orders)
    order_ids = [order.id for order in order_list if getattr(order, "id", None)]
    opportunity_ids = [
        order.opportunity_id
        for order in order_list
        if getattr(order, "opportunity_id", None)
    ]
    lead_ids = [
        order.lead_id
        for order in order_list
        if getattr(order, "lead_id", None)
    ]
    images_by_order = _first_images_by_key(
        ProductReferenceImage.objects.filter(production_order_id__in=order_ids),
        "production_order_id",
    )
    images_by_opportunity = _first_images_by_key(
        ProductReferenceImage.objects.filter(opportunity_id__in=opportunity_ids),
        "opportunity_id",
    )
    images_by_lead = _first_images_by_key(
        ProductReferenceImage.objects.filter(lead_id__in=lead_ids),
        "lead_id",
    )
    for order in order_list:
        order.primary_reference_image = (
            images_by_order.get(order.id)
            or images_by_opportunity.get(getattr(order, "opportunity_id", None))
            or images_by_lead.get(getattr(order, "lead_id", None))
        )
    return order_list


def _lead_quantity_text(lead):
    if not lead:
        return ""
    if getattr(lead, "order_quantity", ""):
        return lead.order_quantity
    min_qty = getattr(lead, "target_order_volume_min", None)
    max_qty = getattr(lead, "target_order_volume_max", None)
    if min_qty and max_qty:
        return f"{min_qty} - {max_qty} pcs"
    if min_qty:
        return f"{min_qty}+ pcs"
    if max_qty:
        return f"Up to {max_qty} pcs"
    return ""


def _reference_image_file(reference_image):
    return reference_image.image if reference_image and reference_image.image else None


def product_snapshot_for_lead(lead, reference_image=None):
    image_file = _reference_image_file(reference_image)
    product_type = getattr(lead, "primary_product_type", "") or ""
    category = getattr(lead, "product_category", "") or ""
    product = getattr(lead, "product_interest", "") or ""
    return {
        "image_file": image_file,
        "image_alt": getattr(reference_image, "caption", "") or product or "Product reference image",
        "title": product or category or product_type or "Product not set",
        "primary_type": product_type or "Type not set",
        "category": category or "Category not set",
        "quantity": _lead_quantity_text(lead) or "Quantity not set",
        "caption": getattr(reference_image, "caption", "") or "",
    }


def product_snapshot_for_opportunity(opportunity, reference_image=None):
    lead = getattr(opportunity, "lead", None)
    image_file = _reference_image_file(reference_image)
    product_type = (
        getattr(opportunity, "product_type", "")
        or getattr(lead, "primary_product_type", "")
        or ""
    )
    category = getattr(opportunity, "product_category", "") or getattr(lead, "product_category", "") or ""
    product = getattr(lead, "product_interest", "") or category or product_type
    quantity = getattr(opportunity, "moq_units", None)
    return {
        "image_file": image_file,
        "image_alt": getattr(reference_image, "caption", "") or product or "Product reference image",
        "title": product or "Product not set",
        "primary_type": product_type or "Type not set",
        "category": category or "Category not set",
        "quantity": f"{quantity} units" if quantity else _lead_quantity_text(lead) or "Quantity not set",
        "caption": getattr(reference_image, "caption", "") or "",
    }


def product_snapshot_for_production(order, reference_image=None):
    lead = getattr(order, "lead", None)
    opportunity = getattr(order, "opportunity", None)
    product = getattr(order, "product", None)
    image_file = _reference_image_file(reference_image)
    if not image_file and product and getattr(product, "image", None):
        image_file = product.image
    if not image_file and getattr(order, "style_image", None):
        image_file = order.style_image

    product_type = (
        getattr(opportunity, "product_type", "")
        or getattr(lead, "primary_product_type", "")
        or ""
    )
    category = getattr(opportunity, "product_category", "") or getattr(lead, "product_category", "") or ""
    title = (
        getattr(product, "name", "")
        or getattr(order, "style_name", "")
        or getattr(lead, "product_interest", "")
        or category
        or getattr(order, "title", "")
    )
    return {
        "image_file": image_file,
        "image_alt": getattr(reference_image, "caption", "") or title or "Product reference image",
        "title": title or "Product not set",
        "primary_type": product_type or "Type not set",
        "category": category or getattr(order, "color_info", "") or "Category not set",
        "quantity": f"{getattr(order, 'qty_total', 0) or 0} units",
        "caption": getattr(reference_image, "caption", "") or "",
    }
