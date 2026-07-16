from django.db.models import DateField
from django.db.models.functions import Coalesce, TruncDate

from .operations_permissions import ROLE_ADMIN, ROLE_CEO, has_operations_role


INVOICE_REPORTING_DATE_ALIAS = "_invoice_reporting_date"
OPPORTUNITY_REPORTING_DATE_ALIAS = "_opportunity_reporting_date"


def can_edit_historical_dates(user):
    return bool(
        user
        and getattr(user, "is_authenticated", False)
        and (getattr(user, "is_superuser", False) or has_operations_role(user, ROLE_CEO, ROLE_ADMIN))
    )


def invoice_reporting_date_expr():
    return Coalesce(
        "invoice_date",
        TruncDate("created_at"),
        output_field=DateField(),
    )


def opportunity_reporting_date_expr():
    return Coalesce(
        "opportunity_date",
        "created_date",
        output_field=DateField(),
    )


def with_invoice_reporting_date(queryset, alias=INVOICE_REPORTING_DATE_ALIAS):
    return queryset.annotate(**{alias: invoice_reporting_date_expr()})


def with_opportunity_reporting_date(queryset, alias=OPPORTUNITY_REPORTING_DATE_ALIAS):
    return queryset.annotate(**{alias: opportunity_reporting_date_expr()})


def apply_invoice_reporting_date_filter(queryset, date_from=None, date_to=None, alias=INVOICE_REPORTING_DATE_ALIAS):
    queryset = with_invoice_reporting_date(queryset, alias=alias)
    if date_from:
        queryset = queryset.filter(**{f"{alias}__gte": date_from})
    if date_to:
        queryset = queryset.filter(**{f"{alias}__lte": date_to})
    return queryset


def apply_opportunity_reporting_date_filter(
    queryset,
    date_from=None,
    date_to=None,
    alias=OPPORTUNITY_REPORTING_DATE_ALIAS,
):
    queryset = with_opportunity_reporting_date(queryset, alias=alias)
    if date_from:
        queryset = queryset.filter(**{f"{alias}__gte": date_from})
    if date_to:
        queryset = queryset.filter(**{f"{alias}__lte": date_to})
    return queryset


def invoice_reporting_date(invoice):
    if not invoice:
        return None
    return (
        getattr(invoice, "invoice_date", None)
        or (invoice.created_at.date() if getattr(invoice, "created_at", None) else None)
        or getattr(invoice, "issue_date", None)
    )


def opportunity_reporting_date(opportunity):
    if not opportunity:
        return None
    return getattr(opportunity, "opportunity_date", None) or getattr(opportunity, "created_date", None)
