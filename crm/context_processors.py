from django.db.utils import OperationalError, ProgrammingError
from django.db.models import Count, Q, Window
from django.core.cache import cache

from crm.services.operations_notifications import visible_notifications


def operations_header(request):
    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return {"crm_header_notifications": [], "crm_header_unread_count": 0}
    route_name = getattr(getattr(request, "resolver_match", None), "url_name", "")
    cache_key = f"crm-header-unread:{user.pk}"
    if route_name == "quick_costing_detail":
        return {
            "crm_header_notifications": [],
            "crm_header_unread_count": int(cache.get(cache_key, 0) or 0),
        }
    try:
        queryset = (
            visible_notifications(user)
            .select_related("assigned_user")
            .annotate(
                header_unread_count=Window(
                    expression=Count("id", filter=Q(is_read=False)),
                )
            )
            .order_by("is_read", "-created_at", "-id")
        )
        items = list(queryset[:5])
        unread_count = int(getattr(items[0], "header_unread_count", 0) or 0) if items else 0
        cache.set(cache_key, unread_count, 60)
        return {
            "crm_header_notifications": items,
            "crm_header_unread_count": unread_count,
        }
    except (OperationalError, ProgrammingError):
        return {"crm_header_notifications": [], "crm_header_unread_count": 0}
