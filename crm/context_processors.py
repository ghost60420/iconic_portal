from django.db.utils import OperationalError, ProgrammingError
from django.db.models import Count, Q, Window
from django.core.cache import cache
from django.contrib.contenttypes.models import ContentType

from crm.services.operations_notifications import (
    notification_priority_order,
    prepare_notification_display,
    visible_notifications,
)
from crm.models import FavoriteRecord
from crm.services.platform_tools import RECORD_CONFIGS, can_manage_archives, descriptor_from_request


def operations_header(request):
    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return {"crm_header_notifications": [], "crm_header_unread_count": 0}
    route_name = getattr(getattr(request, "resolver_match", None), "url_name", "")
    cache_key = f"crm-header-unread:{user.pk}"
    cached = cache.get(cache_key)
    if isinstance(cached, dict):
        return cached
    if route_name == "quick_costing_detail":
        return {
            "crm_header_notifications": [],
            "crm_header_unread_count": 0,
        }
    try:
        queryset = (
            visible_notifications(user)
            .select_related("assigned_user")
            .annotate(
                header_unread_count=Window(
                    expression=Count("id", filter=Q(is_read=False)),
                ),
                priority_rank=notification_priority_order(),
            )
            .order_by("priority_rank", "-created_at", "-id")
        )
        items = list(queryset[:5])
        unread_count = int(getattr(items[0], "header_unread_count", 0) or 0) if items else 0
        payload = {
            "crm_header_notifications": [
                {
                    "title": item.title,
                    "message": item.message,
                    "record_label": item.record_label,
                    "icon_symbol": prepare_notification_display(item).icon_symbol,
                    "age_label": item.age_label,
                    "open_url": item.open_url,
                }
                for item in items
            ],
            "crm_header_unread_count": unread_count,
        }
        cache.set(cache_key, payload, 60)
        return payload
    except (OperationalError, ProgrammingError):
        return {"crm_header_notifications": [], "crm_header_unread_count": 0}


def platform_record_tools(request):
    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return {}
    route_name = getattr(getattr(request, "resolver_match", None), "url_name", "")
    filter_modules = {
        "leads_list": "leads",
        "opportunities_list": "opportunities",
        "cost_sheet_list": "quotations",
        "production_list": "production",
        "invoice_list": "invoices",
        "customers_list": "customers",
    }
    payload = {"crm_filter_module": filter_modules.get(route_name, "")}
    try:
        descriptor = descriptor_from_request(request)
        if not descriptor:
            return payload
        content_type = ContentType.objects.get_for_model(descriptor["record"])
        payload["crm_current_record"] = {
                "type_key": next(key for key, config in RECORD_CONFIGS.items() if config.model is descriptor["record"].__class__),
                "object_id": descriptor["record"].pk,
                "label": descriptor["record_label"],
                "is_favorite": FavoriteRecord.objects.filter(
                    user=user,
                    content_type=content_type,
                    object_id=descriptor["record"].pk,
                ).exists(),
                "is_archived": bool(getattr(descriptor["record"], "is_archived", False)),
                "can_archive": can_manage_archives(user) and hasattr(descriptor["record"], "is_archived"),
            }
        return payload
    except Exception:
        return payload
