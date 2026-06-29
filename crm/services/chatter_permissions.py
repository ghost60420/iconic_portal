from django.db.models import Q

from crm.models import EmployeeProfile, Lead, LeadComment, Opportunity, ProductionOrder
from crm.services.operations_permissions import (
    can_access_operations_module,
    scope_sales_leads,
    scope_sales_opportunities,
)


def visible_chatter_leads(user):
    return scope_sales_leads(Lead.objects.all(), user)


def visible_chatter_opportunities(user):
    return scope_sales_opportunities(Opportunity.objects.select_related("lead"), user)


def visible_chatter_production(user):
    return ProductionOrder.objects.select_related("lead", "opportunity", "opportunity__lead")


def visible_chatter_comments(user):
    if not user or not getattr(user, "is_authenticated", False):
        return LeadComment.objects.none()
    queryset = LeadComment.objects.select_related(
        "lead",
        "opportunity",
        "opportunity__lead",
        "production",
        "production__lead",
        "production__opportunity",
        "production__opportunity__lead",
        "author_user",
        "author_user__employee_profile",
    )
    visibility = Q(lead__isnull=True, opportunity__isnull=True, production__isnull=True)
    if can_access_operations_module(user, "leads"):
        visibility |= Q(
            lead__in=visible_chatter_leads(user),
            opportunity__isnull=True,
            production__isnull=True,
        )
    if can_access_operations_module(user, "opportunities"):
        visibility |= Q(
            opportunity__in=visible_chatter_opportunities(user),
            production__isnull=True,
        )
    if can_access_operations_module(user, "production"):
        visibility |= Q(production__in=visible_chatter_production(user))
    return queryset.filter(visibility).distinct()


def resolve_chatter_target(user, link_type, link_id):
    link_type = (link_type or "").strip().lower()
    link_id = (link_id or "").strip()
    if not link_type and not link_id:
        return {"lead": None, "opportunity": None, "production": None}
    if link_type not in {"lead", "opportunity", "production"} or not link_id.isdigit():
        return None
    pk = int(link_id)
    if link_type == "lead":
        if not can_access_operations_module(user, "leads"):
            return None
        lead = visible_chatter_leads(user).filter(pk=pk).first()
        return {"lead": lead, "opportunity": None, "production": None} if lead else None
    if link_type == "opportunity":
        if not can_access_operations_module(user, "opportunities"):
            return None
        opportunity = visible_chatter_opportunities(user).filter(pk=pk).first()
        if not opportunity:
            return None
        return {"lead": opportunity.lead, "opportunity": opportunity, "production": None}
    if not can_access_operations_module(user, "production"):
        return None
    production = visible_chatter_production(user).filter(pk=pk).first()
    if not production:
        return None
    lead = production.lead or (production.opportunity.lead if production.opportunity_id else None)
    return {"lead": lead, "opportunity": production.opportunity, "production": production}


def can_access_chatter_record(user, module, record):
    if not user or not getattr(user, "is_authenticated", False) or not record:
        return False
    if not can_access_operations_module(user, module):
        return False
    if module == "production":
        return True
    if module == "leads":
        return visible_chatter_leads(user).filter(pk=record.pk).exists()
    if module == "opportunities":
        return visible_chatter_opportunities(user).filter(pk=record.pk).exists()
    return False


def can_receive_chatter_context(user, module, record):
    if not user or not user.is_active:
        return False
    profile = getattr(user, "employee_profile", None)
    if not profile or profile.status not in EmployeeProfile.MENTIONABLE_STATUSES:
        return False
    return module == "general" or can_access_chatter_record(user, module, record)
