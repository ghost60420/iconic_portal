from decimal import Decimal

from django.db.models import Q
from django.urls import reverse

from crm.models import CostingHeader, Customer, EmployeeProfile, Invoice, Lead, Opportunity, ProductionOrder
from crm.services.costing_currency import format_finance_money
from crm.services.employee_identity import employee_profile_ids_matching
from crm.services.operations_permissions import (
    ROLE_CEO,
    ROLE_ADMIN,
    ROLE_DIRECTOR,
    ROLE_HR,
    can_access_operations_module,
    has_operations_role,
    scope_sales_leads,
    scope_sales_opportunities,
)


def _safe_decimal(value):
    try:
        return Decimal(str(value or "0"))
    except Exception:
        return Decimal("0")


def search_operations_records(user, query, *, limit=10, include_opportunities=True):
    query = (query or "").strip()
    if len(query) < 2:
        return []
    groups = []

    if can_access_operations_module(user, "customers"):
        rows = Customer.objects.filter(is_archived=False).filter(
            Q(customer_code__icontains=query)
            | Q(account_brand__icontains=query)
            | Q(contact_name__icontains=query)
            | Q(email__icontains=query)
            | Q(phone__icontains=query)
        ).order_by("account_brand", "id")[:limit]
        groups.append(("Customers", [
            {
                "type": "Customer",
                "number": row.customer_code,
                "name": row.account_brand or row.contact_name,
                "status": "Active" if row.is_active else "Inactive",
                "date": row.updated_at,
                "amount": "",
                "url": reverse("customer_detail", args=[row.pk]),
            }
            for row in rows
        ]))

    if can_access_operations_module(user, "leads"):
        rows = scope_sales_leads(
            Lead.objects.filter(is_archived=False).filter(
                Q(lead_id__icontains=query)
                | Q(account_brand__icontains=query)
                | Q(contact_name__icontains=query)
                | Q(email__icontains=query)
                | Q(phone__icontains=query)
            ),
            user,
        ).order_by("-created_date", "-id")[:limit]
        groups.append(("Leads", [
            {
                "type": "Lead",
                "number": row.lead_id,
                "name": row.account_brand or row.contact_name,
                "status": row.lead_status,
                "date": row.created_date,
                "amount": "",
                "url": reverse("lead_detail", args=[row.pk]),
            }
            for row in rows
        ]))

    if include_opportunities and can_access_operations_module(user, "opportunities"):
        rows = scope_sales_opportunities(
            Opportunity.objects.select_related("lead", "customer").filter(is_archived=False).filter(
                Q(opportunity_id__icontains=query)
                | Q(lead__lead_id__icontains=query)
                | Q(lead__account_brand__icontains=query)
                | Q(lead__email__icontains=query)
                | Q(lead__phone__icontains=query)
                | Q(customer__account_brand__icontains=query)
            ),
            user,
        ).order_by("-updated_at", "-id")[:limit]
        groups.append(("Opportunities", [
            {
                "type": "Opportunity",
                "number": row.opportunity_id,
                "name": (row.customer.account_brand if row.customer else "") or row.lead.account_brand,
                "status": row.stage,
                "date": row.updated_at,
                "amount": format_finance_money(row.order_value, row.order_currency) if row.order_value else "",
                "url": reverse("opportunity_detail", args=[row.pk]),
            }
            for row in rows
        ]))

    if can_access_operations_module(user, "quotations"):
        rows = CostingHeader.objects.select_related("customer", "opportunity", "opportunity__lead").filter(is_archived=False).filter(
            Q(quotation_number__icontains=query)
            | Q(style_name__icontains=query)
            | Q(brand__icontains=query)
            | Q(customer__account_brand__icontains=query)
            | Q(opportunity__opportunity_id__icontains=query)
            | Q(opportunity__lead__lead_id__icontains=query)
        )
        if has_operations_role(user, "Sales") and not has_operations_role(user, ROLE_CEO):
            rows = rows.filter(quoted_by=user)
        rows = rows.exclude(quotation_number="").order_by("-updated_at", "-id")[:limit]
        groups.append(("Quotations", [
            {
                "type": "Quotation",
                "number": row.quotation_number,
                "name": (row.customer.account_brand if row.customer else "") or row.brand or row.style_name,
                "status": row.get_quotation_status_display(),
                "date": row.updated_at,
                "amount": (
                    format_finance_money(
                        _safe_decimal(row.manual_fob_per_piece) * Decimal(row.order_quantity or 0),
                        row.currency,
                    )
                    if row.manual_fob_per_piece and row.order_quantity
                    else ""
                ),
                "url": reverse("cost_sheet_client_quotation", args=[row.pk]),
            }
            for row in rows
        ]))

    if can_access_operations_module(user, "production"):
        rows = ProductionOrder.objects.select_related("customer").filter(is_archived=False).filter(
            Q(order_code__icontains=query)
            | Q(title__icontains=query)
            | Q(client_name_snapshot__icontains=query)
            | Q(brand_name_snapshot__icontains=query)
            | Q(product_name_snapshot__icontains=query)
            | Q(customer__account_brand__icontains=query)
        ).order_by("-updated_at", "-id")[:limit]
        groups.append(("Production", [
            {
                "type": "Production Order",
                "number": row.order_code,
                "name": row.client_name_snapshot or (row.customer.account_brand if row.customer else "") or row.title,
                "status": row.get_operational_status_display(),
                "date": row.updated_at,
                "amount": format_finance_money(row.approved_total_value, row.approved_currency) if row.approved_total_value else "",
                "url": reverse("production_detail", args=[row.pk]),
            }
            for row in rows
        ]))

    if can_access_operations_module(user, "invoices"):
        rows = Invoice.objects.select_related("customer").filter(is_archived=False).filter(
            Q(invoice_number__icontains=query)
            | Q(customer__account_brand__icontains=query)
            | Q(customer__contact_name__icontains=query)
            | Q(customer__email__icontains=query)
            | Q(customer__phone__icontains=query)
        ).order_by("-issue_date", "-id")[:limit]
        groups.append(("Invoices", [
            {
                "type": "Invoice",
                "number": row.invoice_number,
                "name": (row.customer.account_brand if row.customer else "") or "Invoice customer",
                "status": row.get_status_display(),
                "date": row.issue_date,
                "amount": format_finance_money(row.total_amount, row.currency),
                "url": reverse("invoice_view", args=[row.pk]),
            }
            for row in rows
        ]))

    if has_operations_role(user, ROLE_CEO, ROLE_DIRECTOR, ROLE_ADMIN, ROLE_HR) or user.is_superuser:
        alias_profile_ids = employee_profile_ids_matching(query)
        employee_filter = (
            Q(employee_id__icontains=query)
            | Q(display_name__icontains=query)
            | Q(user__first_name__icontains=query)
            | Q(user__last_name__icontains=query)
            | Q(user__email__icontains=query)
        )
        if alias_profile_ids:
            employee_filter |= Q(pk__in=alias_profile_ids)
        rows = EmployeeProfile.objects.select_related("user", "position_ref", "department_ref").filter(
            employee_filter
        ).order_by("display_name", "user__username")[:limit]
        groups.append(("Employees", [
            {
                "type": "Employee",
                "number": row.employee_id,
                "name": row.public_name,
                "status": f"{row.position_name} · {row.department_name}",
                "date": row.updated_at,
                "amount": "",
                "url": reverse("employee_edit", args=[row.user_id]),
            }
            for row in rows
        ]))

    return [(label, rows) for label, rows in groups if rows]
