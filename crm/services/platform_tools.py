from dataclasses import dataclass
from time import perf_counter

from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db import transaction
from django.db.models import F
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from crm.models import (
    CRMAuditLog,
    CostingHeader,
    Customer,
    FavoriteRecord,
    Invoice,
    Lead,
    Opportunity,
    ProductionOrder,
    RecentSearch,
    RecentlyViewedRecord,
    SavedFilter,
    UserDashboardPreference,
)
from crm.services.operations_permissions import (
    ROLE_ADMIN,
    ROLE_CEO,
    ROLE_DIRECTOR,
    can_access_operations_module,
    has_operations_role,
    scope_sales_leads,
    scope_sales_opportunities,
)


@dataclass(frozen=True)
class RecordConfig:
    model: type
    module: str
    record_type: str
    detail_route: str
    label_fields: tuple[str, ...]


RECORD_CONFIGS = {
    "customer": RecordConfig(Customer, "customers", "Customer", "customer_detail", ("customer_code", "account_brand", "contact_name")),
    "lead": RecordConfig(Lead, "leads", "Lead", "lead_detail", ("lead_id", "account_brand", "contact_name")),
    "opportunity": RecordConfig(Opportunity, "opportunities", "Opportunity", "opportunity_detail", ("opportunity_id",)),
    "quotation": RecordConfig(CostingHeader, "quotations", "Quotation", "cost_sheet_detail", ("quotation_number", "style_name", "brand")),
    "production": RecordConfig(ProductionOrder, "production", "Production Order", "production_detail", ("order_code", "title")),
    "invoice": RecordConfig(Invoice, "invoices", "Invoice", "invoice_view", ("invoice_number",)),
}

ROUTE_RECORD_TYPES = {
    "customer_detail": "customer",
    "lead_detail": "lead",
    "opportunity_detail": "opportunity",
    "cost_sheet_detail": "quotation",
    "cost_sheet_client_quotation": "quotation",
    "production_detail": "production",
    "invoice_view": "invoice",
}

DASHBOARD_WIDGETS = (
    ("kpis", "Key metrics"),
    ("workflow", "Workflow snapshot"),
    ("notifications", "Notification center"),
    ("operations", "Operations control"),
    ("favorites", "Favorites"),
    ("recent", "Recently viewed"),
    ("saved_filters", "Saved filters"),
)


def _base_queryset(config, user):
    queryset = config.model.objects.all()
    if config.model is Lead:
        return scope_sales_leads(queryset, user)
    if config.model is Opportunity:
        return scope_sales_opportunities(queryset, user)
    if config.model is CostingHeader and has_operations_role(user, "Sales") and not has_operations_role(
        user, ROLE_CEO, ROLE_DIRECTOR, ROLE_ADMIN
    ):
        return queryset.filter(quoted_by=user)
    return queryset


def permitted_record(user, record_type, object_id):
    config = RECORD_CONFIGS.get(record_type)
    if not config or not can_access_operations_module(user, config.module):
        return None
    return _base_queryset(config, user).filter(pk=object_id).first()


def record_label(record, config):
    parts = [str(getattr(record, field, "") or "").strip() for field in config.label_fields]
    return next((part for part in parts if part), f"{config.record_type} #{record.pk}")[:220]


def record_descriptor(user, record_type, object_id):
    config = RECORD_CONFIGS.get(record_type)
    record = permitted_record(user, record_type, object_id)
    if not config or not record:
        return None
    try:
        target_url = reverse(config.detail_route, args=[record.pk])
    except NoReverseMatch:
        return None
    return {
        "record": record,
        "config": config,
        "record_type": config.record_type,
        "record_label": record_label(record, config),
        "target_url": target_url,
    }


def descriptor_from_request(request):
    match = getattr(request, "resolver_match", None)
    record_type = ROUTE_RECORD_TYPES.get(getattr(match, "url_name", ""))
    object_id = (getattr(match, "kwargs", {}) or {}).get("pk")
    if not record_type or not object_id:
        return None
    return record_descriptor(request.user, record_type, object_id)


def toggle_favorite(user, record_type, object_id):
    descriptor = record_descriptor(user, record_type, object_id)
    if not descriptor:
        return None
    content_type = ContentType.objects.get_for_model(descriptor["record"])
    lookup = {"user": user, "content_type": content_type, "object_id": object_id}
    existing = FavoriteRecord.objects.filter(**lookup).first()
    if existing:
        existing.delete()
        return False
    FavoriteRecord.objects.create(
        **lookup,
        record_type=descriptor["record_type"],
        record_label=descriptor["record_label"],
        target_url=descriptor["target_url"],
    )
    return True


def track_recent_record(user, descriptor):
    if not descriptor:
        return
    content_type = ContentType.objects.get_for_model(descriptor["record"])
    RecentlyViewedRecord.objects.update_or_create(
        user=user,
        content_type=content_type,
        object_id=descriptor["record"].pk,
        defaults={
            "record_type": descriptor["record_type"],
            "record_label": descriptor["record_label"],
            "target_url": descriptor["target_url"],
        },
    )
    stale_ids = list(
        RecentlyViewedRecord.objects.filter(user=user).order_by("-viewed_at", "-id").values_list("id", flat=True)[20:]
    )
    if stale_ids:
        RecentlyViewedRecord.objects.filter(pk__in=stale_ids).delete()


def remember_search(user, query):
    query = " ".join((query or "").split())[:160]
    if len(query) < 2:
        return
    normalized = query.casefold()
    with transaction.atomic():
        row, created = RecentSearch.objects.get_or_create(
            user=user,
            normalized_query=normalized,
            defaults={"query": query},
        )
        if not created:
            RecentSearch.objects.filter(pk=row.pk).update(
                query=query,
                search_count=F("search_count") + 1,
                searched_at=timezone.now(),
            )
    stale_ids = list(RecentSearch.objects.filter(user=user).values_list("id", flat=True)[10:])
    if stale_ids:
        RecentSearch.objects.filter(pk__in=stale_ids).delete()


def dashboard_personalization(user):
    is_fixed = user.is_superuser or has_operations_role(user, ROLE_CEO)
    preference = None if is_fixed else UserDashboardPreference.objects.filter(user=user).first()
    return {
        "dashboard_widgets": DASHBOARD_WIDGETS,
        "dashboard_layout_fixed": is_fixed,
        "dashboard_hidden_widgets": preference.hidden_widgets if preference else [],
        "dashboard_widget_order": preference.widget_order if preference else [],
        "dashboard_favorites": visible_personal_records(user, FavoriteRecord.objects.filter(user=user), limit=10),
        "dashboard_recent_records": visible_personal_records(user, RecentlyViewedRecord.objects.filter(user=user), limit=10),
        "dashboard_saved_filters": SavedFilter.objects.filter(user=user)[:10],
    }


def visible_personal_records(user, queryset, *, limit=10):
    key_by_label = {config.record_type: key for key, config in RECORD_CONFIGS.items()}
    candidates = list(queryset[: max(limit * 2, 20)])
    ids_by_key = {}
    for item in candidates:
        key = key_by_label.get(item.record_type)
        if key:
            ids_by_key.setdefault(key, []).append(item.object_id)
    permitted = set()
    for key, object_ids in ids_by_key.items():
        config = RECORD_CONFIGS[key]
        if not can_access_operations_module(user, config.module):
            continue
        permitted.update(
            (key, object_id)
            for object_id in _base_queryset(config, user).filter(pk__in=object_ids).values_list("pk", flat=True)
        )
    return [item for item in candidates if (key_by_label.get(item.record_type), item.object_id) in permitted][:limit]


def record_timeline(user, module, record_id, *, limit=50):
    module_name = "invoices" if module == "invoice" else module
    return CRMAuditLog.objects.filter(module=module_name, record_id=str(record_id)).select_related(
        "actor", "actor__employee_profile"
    )[:limit]


def can_manage_archives(user):
    return bool(user and user.is_authenticated and (user.is_superuser or has_operations_role(user, ROLE_CEO, ROLE_DIRECTOR, ROLE_ADMIN)))


def set_record_archived(user, record_type, object_id, archived):
    if not can_manage_archives(user):
        return None
    descriptor = record_descriptor(user, record_type, object_id)
    if not descriptor or not hasattr(descriptor["record"], "is_archived"):
        return None
    record = descriptor["record"]
    record.is_archived = bool(archived)
    record.archived_at = timezone.now() if archived else None
    record.archived_by = user if archived else None
    record.save(update_fields=("is_archived", "archived_at", "archived_by"))
    return descriptor


def save_request_performance(path, elapsed_ms, query_count):
    key = "crm:request-performance"
    samples = cache.get(key, [])
    samples.append({"path": path[:180], "elapsed_ms": round(elapsed_ms, 2), "query_count": query_count, "at": timezone.now().isoformat()})
    cache.set(key, samples[-100:], 86400)


def request_performance_summary():
    samples = cache.get("crm:request-performance", [])
    if not samples:
        return {"sample_count": 0, "average_response_ms": None, "average_query_count": None, "slowest": []}
    elapsed = [sample["elapsed_ms"] for sample in samples]
    queries = [sample["query_count"] for sample in samples if sample["query_count"] is not None]
    return {
        "sample_count": len(samples),
        "average_response_ms": round(sum(elapsed) / len(elapsed), 2),
        "average_query_count": round(sum(queries) / len(queries), 2) if queries else None,
        "slowest": sorted(samples, key=lambda sample: sample["elapsed_ms"], reverse=True)[:5],
    }
