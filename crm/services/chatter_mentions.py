import logging
import re

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db.models import Case, IntegerField, Q, Value, When
from django.urls import reverse

from crm.models import AutomationNotification
from crm.models import EmployeeProfile
from crm.services.chatter_permissions import can_receive_chatter_context
from crm.services.employee_profiles import employee_display_name
from crm.services.operations_permissions import (
    OPERATIONS_ROLES,
)


logger = logging.getLogger(__name__)
MENTION_RE = re.compile(r"@([A-Za-z0-9._-]+)")
MAX_MENTIONS = 20


def mention_handles(text):
    handles = []
    seen = set()
    for handle in MENTION_RE.findall(text or ""):
        key = handle.casefold()
        if key not in seen:
            seen.add(key)
            handles.append(handle)
        if len(handles) >= MAX_MENTIONS:
            break
    return handles


def _record_context(comment):
    if comment.production_id:
        record = comment.production
        return {
            "record": record,
            "module": "production",
            "rule_type": "production",
            "label": record.purchase_order_number or record.title or f"Production Order {record.pk}",
            "url": reverse("production_detail", args=[record.pk]),
        }
    if comment.opportunity_id:
        record = comment.opportunity
        return {
            "record": record,
            "module": "opportunities",
            "rule_type": "lifecycle",
            "label": record.opportunity_id or f"Opportunity {record.pk}",
            "url": reverse("opportunity_detail", args=[record.pk]),
        }
    if comment.lead_id:
        record = comment.lead
        return {
            "record": record,
            "module": "leads",
            "rule_type": "lifecycle",
            "label": record.lead_id or record.account_brand or f"Lead {record.pk}",
            "url": reverse("lead_detail", args=[record.pk]),
        }
    return {
        "record": comment,
        "module": "general",
        "rule_type": "general",
        "label": "Chatter",
        "url": reverse("chatter_feed"),
    }


def _prime_role_cache(user):
    user._operations_group_names = {
        group.name.casefold() for group in user.groups.all()
    }


def _resolve_recipients(handles, actor):
    if not handles:
        return []
    normalized = {handle.casefold() for handle in handles}
    role_map = {role.casefold().replace(" ", ""): role for role in OPERATIONS_ROLES}
    role_names = {role_map[key] for key in normalized if key in role_map}

    user_filter = Q()
    for handle in handles:
        user_filter |= (
            Q(employee_profile__display_name__iexact=handle)
            | Q(employee_profile__display_name__istartswith=f"{handle} ")
        )
    users = get_user_model().objects.filter(
        is_active=True,
        employee_profile__status__in=EmployeeProfile.MENTIONABLE_STATUSES,
    ).filter(
        user_filter | Q(groups__name__in=role_names)
    ).exclude(pk=getattr(actor, "pk", None)).select_related(
        "employee_profile"
    ).prefetch_related("groups").distinct()
    return list(users)


def notify_chatter_mentions(comment, actor):
    handles = mention_handles(comment.content)
    if not handles:
        return 0
    try:
        context = _record_context(comment)
        recipients = _resolve_recipients(handles, actor)
        allowed = []
        for user in recipients:
            _prime_role_cache(user)
            if can_receive_chatter_context(user, context["module"], context["record"]):
                allowed.append(user)
        if not allowed:
            return 0

        actor_name = employee_display_name(actor)
        preview = " ".join((comment.content or "").split())[:180]
        content_type = ContentType.objects.get_for_model(context["record"], for_concrete_model=False)
        rows = [
            AutomationNotification(
                source_key=f"chatter-mention:{comment.pk}:user:{user.pk}",
                rule_type=context["rule_type"],
                notification_type="mention",
                title=f"{actor_name} mentioned you on {context['label']}",
                message=preview,
                priority="normal",
                record_content_type=content_type,
                record_object_id=context["record"].pk,
                record_label=context["label"],
                target_url=context["url"],
                assigned_user=user,
            )
            for user in allowed
        ]
        AutomationNotification.objects.bulk_create(rows, ignore_conflicts=True)
        for user in allowed:
            cache.delete(f"crm-header-unread:{user.pk}")
        return len(rows)
    except Exception:
        logger.exception("Chatter mention notification failed for comment %s", comment.pk)
        return 0


def mention_suggestions(query):
    query = (query or "").strip().lstrip("@")[:40]
    if not query:
        return []
    users = list(
        get_user_model().objects.filter(
            is_active=True,
            employee_profile__status__in=EmployeeProfile.MENTIONABLE_STATUSES,
        ).filter(
            employee_profile__display_name__icontains=query,
        ).annotate(
            mention_rank=Case(
                When(employee_profile__display_name__iexact=query, then=Value(0)),
                When(employee_profile__display_name__istartswith=query, then=Value(1)),
                default=Value(2),
                output_field=IntegerField(),
            )
        ).select_related("employee_profile", "employee_profile__position_ref", "employee_profile__department_ref").order_by(
            "mention_rank", "employee_profile__display_name", "username"
        )[:10]
    )
    rows = []
    for user in users:
        profile = user.employee_profile
        display_name = employee_display_name(user)
        handle = display_name.split()[0] if display_name else user.get_username()
        rows.append(
            {
                "handle": handle,
                "display_name": display_name,
                "position": profile.position_name or "Team Member",
                "department": profile.department_name,
                "photo_url": profile.profile_photo.url if profile.profile_photo else "",
                "initials": profile.initials,
                "kind": "user",
            }
        )
    existing = {row["handle"].casefold() for row in rows}
    remaining = max(10 - len(rows), 0)
    for role in Group.objects.filter(name__in=OPERATIONS_ROLES, name__icontains=query).annotate(
        mention_rank=Case(
            When(name__iexact=query, then=Value(0)),
            When(name__istartswith=query, then=Value(1)),
            default=Value(2),
            output_field=IntegerField(),
        )
    ).order_by("mention_rank", "name")[:remaining]:
        if role.name.casefold() not in existing:
            rows.append(
                {
                    "handle": role.name.replace(" ", ""),
                    "display_name": role.name,
                    "position": "Permission Role",
                    "department": "",
                    "photo_url": "",
                    "initials": role.name[:1].upper(),
                    "kind": "role",
                }
            )
    return rows[:10]
