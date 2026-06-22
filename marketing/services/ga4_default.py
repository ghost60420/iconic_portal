from marketing.models import SeoProperty


def ga4_property_queryset():
    return SeoProperty.objects.exclude(ga4_property_id="").order_by("name", "ga4_property_id")


def get_default_ga4_property():
    active = list(ga4_property_queryset().filter(is_active=True))
    if len(active) == 1:
        return active[0]
    return None


def ga4_reporting_queryset():
    default_property = get_default_ga4_property()
    if default_property:
        return ga4_property_queryset().filter(pk=default_property.pk)
    return ga4_property_queryset().filter(is_active=True)


def set_default_ga4_property(property_id: str) -> SeoProperty:
    prop = ga4_property_queryset().get(ga4_property_id=(property_id or "").strip())
    SeoProperty.objects.exclude(ga4_property_id="").exclude(pk=prop.pk).update(is_active=False)
    if not prop.is_active:
        prop.is_active = True
        prop.save(update_fields=["is_active", "updated_at"])
    return prop
