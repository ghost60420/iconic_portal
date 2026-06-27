from django.db.models import Q

from crm.permissions import get_access, operations_group_names


ROLE_CEO = "CEO"
ROLE_SALES = "Sales"
ROLE_PRODUCTION = "Production"
ROLE_ACCOUNTS = "Accounts"
ROLE_MERCHANDISING = "Merchandising"
OPERATIONS_ROLES = (
    ROLE_CEO,
    ROLE_SALES,
    ROLE_PRODUCTION,
    ROLE_ACCOUNTS,
    ROLE_MERCHANDISING,
)

PERMISSION_DESCRIPTIONS = {
    "view_lead": "Allows viewing permitted lead records.",
    "add_lead": "Allows creating new leads.",
    "change_lead": "Allows updating permitted lead records.",
    "view_opportunity": "Allows viewing permitted sales opportunities.",
    "add_opportunity": "Allows creating sales opportunities.",
    "change_opportunity": "Allows updating permitted sales opportunities.",
    "view_customer": "Allows viewing customer and brand records.",
    "add_customer": "Allows creating customer records.",
    "change_customer": "Allows updating customer records.",
    "view_costingheader": "Allows viewing costing and quotation records.",
    "add_costingheader": "Allows creating costing and quotation records.",
    "change_costingheader": "Allows updating costing and quotation records within the user's workflow role.",
    "view_quickcosting": "Allows viewing Quick Costing records without changing calculation rules.",
    "add_quickcosting": "Allows creating Quick Costing records.",
    "change_quickcosting": "Allows updating Quick Costing records within existing business rules.",
    "view_productionorder": "Allows viewing approved Production Orders.",
    "change_productionorder": "Allows updating permitted production status and notes; approved prices remain locked.",
    "view_productionstage": "Allows viewing production stages.",
    "change_productionstage": "Allows updating production stage progress.",
    "view_shipment": "Allows viewing shipment records.",
    "add_shipment": "Allows creating shipment records.",
    "change_shipment": "Allows updating shipment status and details.",
    "view_invoice": "Allows viewing invoice records.",
    "add_invoice": "Allows creating invoices through the existing invoice workflow.",
    "change_invoice": "Allows updating invoices through the existing invoice workflow.",
    "view_invoicepayment": "Allows viewing recorded payments.",
    "add_invoicepayment": "Allows recording invoice payments.",
    "change_invoicepayment": "Allows correcting permitted payment records.",
    "view_accountingentry": "Allows viewing finance entries.",
    "add_accountingentry": "Allows creating finance entries through existing finance rules.",
    "change_accountingentry": "Allows updating finance entries through existing finance rules.",
    "view_crmauditlog": "Allows viewing the CRM Audit Log when the user is also a CEO or administrator.",
}

ROLE_CAPABILITIES = {
    ROLE_CEO: (
        ("Approve Quotations", "Allows approving or rejecting quotations after Sales submission."),
        ("Management Visibility", "Allows viewing all operational, sales, production, finance, and audit information."),
    ),
    ROLE_SALES: (
        ("Own Sales Pipeline", "Allows working with assigned leads, opportunities, and quotations."),
        ("Submit Quotations", "Allows submitting quotations for CEO approval without self-approval rights."),
    ),
    ROLE_PRODUCTION: (
        ("Production Tracking", "Allows updating production progress and notes without changing approved prices."),
    ),
    ROLE_ACCOUNTS: (
        ("Invoice and Payment Access", "Allows invoice, payment, AR, AP, and permitted finance work."),
    ),
    ROLE_MERCHANDISING: (
        ("Costing and Sample Visibility", "Allows viewing costing and updating merchandising or production progress."),
    ),
}

ROLE_MODULES = {
    ROLE_CEO: {"customers", "leads", "opportunities", "quotations", "production", "inventory", "invoices", "finance", "audit"},
    ROLE_SALES: {"customers", "leads", "opportunities", "quotations"},
    ROLE_PRODUCTION: {"production", "inventory"},
    ROLE_ACCOUNTS: {"customers", "invoices", "finance"},
    ROLE_MERCHANDISING: {"customers", "quotations", "production", "inventory"},
}

FALLBACK_FLAGS = {
    "customers": ("can_customers",),
    "leads": ("can_leads",),
    "opportunities": ("can_opportunities",),
    "quotations": ("can_costing",),
    "production": ("can_production",),
    "inventory": ("can_inventory",),
    "invoices": ("can_accounting_ca", "can_accounting_bd"),
    "finance": ("can_accounting_ca", "can_accounting_bd"),
    "audit": ("can_view_ceo_tools",),
}


def operations_role_names(user):
    if not user or not getattr(user, "is_authenticated", False):
        return set()
    if getattr(user, "is_superuser", False):
        return set(OPERATIONS_ROLES)
    canonical = {
        "ceo": ROLE_CEO,
        "sales": ROLE_SALES,
        "production": ROLE_PRODUCTION,
        "accounts": ROLE_ACCOUNTS,
        "merchandising": ROLE_MERCHANDISING,
    }
    return {canonical[name] for name in operations_group_names(user) if name in canonical}


def has_operations_role(user, *roles):
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    return bool(operations_role_names(user).intersection(roles))


def can_access_operations_module(user, module):
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True

    roles = operations_role_names(user)
    if roles:
        return any(module in ROLE_MODULES.get(role, set()) for role in roles)

    try:
        access = get_access(user)
    except Exception:
        return False
    return any(bool(getattr(access, flag, False)) for flag in FALLBACK_FLAGS.get(module, ()))


def scope_sales_leads(queryset, user):
    if has_operations_role(user, ROLE_SALES) and not has_operations_role(user, ROLE_CEO):
        names = {user.get_username(), user.get_full_name().strip()}
        names.discard("")
        owner_filter = Q(assigned_to=user)
        for name in names:
            owner_filter |= Q(owner__iexact=name)
        return queryset.filter(owner_filter)
    return queryset


def scope_sales_opportunities(queryset, user):
    if has_operations_role(user, ROLE_SALES) and not has_operations_role(user, ROLE_CEO):
        names = {user.get_username(), user.get_full_name().strip()}
        names.discard("")
        owner_filter = Q(lead__assigned_to=user)
        for name in names:
            owner_filter |= Q(lead__owner__iexact=name)
        return queryset.filter(owner_filter)
    return queryset
