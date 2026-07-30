from django.conf import settings
from django.urls import reverse
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
from crm.services.operations_permissions import operations_role_names
from crm.services.platform_tools import RECORD_CONFIGS, can_manage_archives, descriptor_from_request


KPI_STAGING_UAT_ROUTES = {
    "employee_performance",
    "kpi_assignment_management",
    "kpi_dashboard",
    "kpi_dashboard_widget",
    "kpi_intelligence",
    "kpi_intelligence_report",
    "kpi_intelligence_widget",
    "kpi_policy_management",
    "kpi_review_detail",
    "kpi_review_list",
    "notification_list",
}
KPI_STAGING_UAT_ROLE_ORDER = (
    "CEO",
    "Director",
    "Manager",
    "HR",
    "Accounts",
    "Finance",
    "Admin",
    "Sales Manager",
    "Sales",
    "Production",
    "Merchandising",
    "Merchandiser",
    "QC",
    "Warehouse",
    "Supervisor",
    "Read Only",
)


def _kpi_staging_uat_role_label(user):
    if user.is_superuser:
        return "CEO / Super Admin"
    roles = operations_role_names(user)
    ordered = [role for role in KPI_STAGING_UAT_ROLE_ORDER if role in roles]
    return " / ".join(ordered) if ordered else "Employee"


def kpi_staging_uat(request):
    user = getattr(request, "user", None)
    route_name = getattr(getattr(request, "resolver_match", None), "url_name", "")
    if not (
        getattr(settings, "KPI_STAGING", False)
        and getattr(settings, "KPI_STAGING_UAT_MENU_ENABLED", False)
        and user
        and getattr(user, "is_authenticated", False)
        and route_name in KPI_STAGING_UAT_ROUTES
    ):
        return {}

    review_id = int(getattr(settings, "KPI_STAGING_UAT_REVIEW_ID", 0) or 0)
    return {
        "kpi_staging_uat": {
            "enabled": True,
            "environment": "PRIVATE KPI STAGING",
            "role_label": _kpi_staging_uat_role_label(user),
            "review_detail_url": (
                reverse("kpi_review_detail", args=[review_id])
                if review_id
                else ""
            ),
        }
    }


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
