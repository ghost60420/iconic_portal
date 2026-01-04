import json
from decimal import Decimal
from typing import Optional
from django.http import HttpResponse, HttpResponseForbidden
from django.contrib import messages
from django.shortcuts import redirect
from django.utils import timezone
from django.core.serializers.json import DjangoJSONEncoder
from django.forms.models import model_to_dict

from .models import AccountingMonthClose, AccountingEntryAudit


AUDIT_FIELDS = [
    "date",
    "side",
    "direction",
    "status",
    "main_type",
    "sub_type",
    "currency",
    "amount_original",
    "rate_to_cad",
    "rate_to_bdt",
    "amount_cad",
    "amount_bdt",
    "description",
    "internal_note",
    "transfer_ref",
    "opportunity_id",
    "production_order_id",
    "shipment_id",
    "customer_id",
    "linked_entry_id",
]


def get_client_ip(request) -> str:
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "") or ""


def json_safe(payload):
    if payload is None:
        return None
    return json.loads(json.dumps(payload, cls=DjangoJSONEncoder))


def entry_snapshot(entry) -> dict:
    if not entry:
        return {}
    data = model_to_dict(entry, fields=AUDIT_FIELDS)

    for k, v in list(data.items()):
        if isinstance(v, Decimal):
            data[k] = str(v)

    return data


def log_entry_audit(request, entry, action: str, before_data=None, after_data=None, note: str = ""):
    AccountingEntryAudit.objects.create(
        entry=entry,
        action=(action or "")[:20],
        changed_at=timezone.now(),
        changed_by=getattr(request, "user", None),
        before_data=json_safe(before_data),
        after_data=json_safe(after_data),
        note=(note or "")[:255],
        ip_address=get_client_ip(request)[:64],
    )


def is_month_closed(year: int, month: int, side: str = "ALL") -> bool:
    if not year or not month:
        return False

    s = (side or "ALL").upper()
    if s not in ["CA", "BD", "ALL"]:
        s = "ALL"

    if AccountingMonthClose.objects.filter(year=year, month=month, side="ALL", is_closed=True).exists():
        return True

    if s in ["CA", "BD"]:
        return AccountingMonthClose.objects.filter(year=year, month=month, side=s, is_closed=True).exists()

    return False


def require_open_month_or_admin(request, entry_date, side: str) -> Optional[HttpResponse]:
    if request.user.is_superuser:
        return None

    y = entry_date.year
    m = entry_date.month

    if is_month_closed(y, m, side) or is_month_closed(y, m, "ALL"):
        messages.error(request, "This month is closed. Only admin can change it.")
        return HttpResponseForbidden("Month closed")

    return None


def block_if_month_closed_redirect(request, entry_date, side: str):
    locked = require_open_month_or_admin(request, entry_date, side)
    if locked:
        return redirect(request.META.get("HTTP_REFERER") or "accounting_entry_list")
    return None