from django.db.models import Q

from crm.models_access import UserAccess
from crm.services.employee_identity import employee_lead_ownership_q


ROLE_CEO = "CEO"
ROLE_DIRECTOR = "Director"
ROLE_MANAGER = "Manager"
ROLE_SALES = "Sales"
ROLE_PRODUCTION = "Production"
ROLE_ACCOUNTS = "Accounts"
ROLE_MERCHANDISING = "Merchandising"
ROLE_MERCHANDISER = "Merchandiser"
ROLE_SUPERVISOR = "Supervisor"
ROLE_FINANCE = "Finance"
ROLE_QC = "QC"
ROLE_WAREHOUSE = "Warehouse"
ROLE_HR = "HR"
ROLE_ADMIN = "Admin"
ROLE_READ_ONLY = "Read Only"
ROLE_SALES_MANAGER = "Sales Manager"
OPERATIONS_ROLES = (
    ROLE_CEO,
    ROLE_DIRECTOR,
    ROLE_MANAGER,
    ROLE_SALES,
    ROLE_PRODUCTION,
    ROLE_ACCOUNTS,
    ROLE_MERCHANDISING,
    ROLE_MERCHANDISER,
    ROLE_SUPERVISOR,
    ROLE_FINANCE,
    ROLE_QC,
    ROLE_WAREHOUSE,
    ROLE_HR,
    ROLE_ADMIN,
    ROLE_READ_ONLY,
    ROLE_SALES_MANAGER,
)

ROLE_FLAG_MATRIX = {
    ROLE_CEO: "*",
    ROLE_DIRECTOR: {
        "can_leads", "can_opportunities", "can_customers", "can_inventory", "can_production",
        "can_shipping", "can_ai", "can_calendar", "can_marketing", "can_whatsapp", "can_costing",
        "can_view_internal_costing", "can_accounting_bd", "can_accounting_ca", "can_library",
    },
    ROLE_MANAGER: {
        "can_leads", "can_opportunities", "can_customers", "can_costing", "can_view_internal_costing",
        "can_production", "can_shipping", "can_inventory", "can_calendar",
    },
    ROLE_SALES_MANAGER: {
        "can_leads", "can_opportunities", "can_customers", "can_costing", "can_view_internal_costing", "can_calendar",
    },
    ROLE_SALES: {
        "can_leads", "can_opportunities", "can_customers", "can_costing", "can_view_internal_costing", "can_calendar",
    },
    ROLE_MERCHANDISING: {
        "can_customers", "can_costing", "can_view_internal_costing", "can_production", "can_inventory", "can_library", "can_calendar",
    },
    ROLE_MERCHANDISER: {
        "can_customers", "can_costing", "can_view_internal_costing", "can_production", "can_inventory", "can_library", "can_calendar",
    },
    ROLE_PRODUCTION: {"can_production", "can_shipping", "can_inventory", "can_calendar"},
    ROLE_ACCOUNTS: {"can_customers", "can_accounting_bd", "can_accounting_ca"},
    ROLE_FINANCE: {"can_customers", "can_accounting_bd", "can_accounting_ca"},
    ROLE_QC: {"can_production", "can_calendar"},
    ROLE_WAREHOUSE: {"can_production", "can_shipping", "can_inventory"},
    ROLE_SUPERVISOR: set(),
    ROLE_HR: set(),
    ROLE_ADMIN: {"can_leads", "can_opportunities", "can_production"},
    ROLE_READ_ONLY: set(),
}

DEPARTMENT_FLAG_MATRIX = {
    "management": {flag for flags in ROLE_FLAG_MATRIX.values() if flags != "*" for flag in flags},
    "sales": ROLE_FLAG_MATRIX[ROLE_SALES],
    "merchandising": ROLE_FLAG_MATRIX[ROLE_MERCHANDISING],
    "production": ROLE_FLAG_MATRIX[ROLE_PRODUCTION],
    "quality": ROLE_FLAG_MATRIX[ROLE_QC],
    "quality_control": ROLE_FLAG_MATRIX[ROLE_QC],
    "accounts": ROLE_FLAG_MATRIX[ROLE_ACCOUNTS],
    "warehouse": ROLE_FLAG_MATRIX[ROLE_WAREHOUSE],
    "shipping": ROLE_FLAG_MATRIX[ROLE_WAREHOUSE],
}

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
    ROLE_DIRECTOR: (("Company Visibility", "Allows all operational access except CEO-only settings and approvals."),),
    ROLE_MANAGER: (
        ("Team Visibility", "Allows viewing team sales and operational dashboards without CEO approval rights."),
        ("Work Assignment", "Allows reviewing performance and assigning permitted operational work."),
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
    ROLE_MERCHANDISER: (("Merchandising Work", "Allows permitted merchandising, costing, and production visibility."),),
    ROLE_SUPERVISOR: (("Department Supervision", "Allows permitted work within the employee's assigned department."),),
    ROLE_FINANCE: (("Finance Reporting", "Allows viewing executive finance reports and currency exposure."),),
    ROLE_QC: (("Quality Control", "Allows viewing production and updating quality-control progress and notes."),),
    ROLE_WAREHOUSE: (("Warehouse and Shipping", "Allows inventory, ready-to-ship, and shipment work."),),
    ROLE_HR: (("Employee Records", "Allows permitted employee profile administration."),),
    ROLE_ADMIN: (("User Administration", "Allows employee, role, and system setup without granting CEO approval rights."),),
    ROLE_READ_ONLY: (("Read Only", "Grants configured view access and does not remove permissions granted by another role."),),
    ROLE_SALES_MANAGER: (("Sales Team", "Allows team sales pipeline, lead, opportunity, and quotation access."),),
}

ROLE_MODULES = {
    ROLE_CEO: {"customers", "leads", "opportunities", "quotations", "production", "inventory", "invoices", "finance", "audit"},
    ROLE_DIRECTOR: {"customers", "leads", "opportunities", "quotations", "production", "inventory", "invoices", "finance", "audit"},
    ROLE_MANAGER: set(),
    ROLE_SALES: {"customers", "leads", "opportunities", "quotations"},
    ROLE_PRODUCTION: {"production", "inventory"},
    ROLE_ACCOUNTS: {"customers", "invoices", "finance"},
    ROLE_MERCHANDISING: {"customers", "quotations", "production", "inventory"},
    ROLE_MERCHANDISER: {"customers", "quotations", "production", "inventory"},
    ROLE_SUPERVISOR: set(),
    ROLE_FINANCE: {"customers", "invoices", "finance"},
    ROLE_QC: {"production"},
    ROLE_WAREHOUSE: {"production", "inventory"},
    ROLE_HR: set(),
    ROLE_ADMIN: {"leads", "opportunities", "production"},
    ROLE_READ_ONLY: set(),
    ROLE_SALES_MANAGER: {"customers", "leads", "opportunities", "quotations"},
}

DEPARTMENT_MODULES = {
    "management": {"customers", "leads", "opportunities", "quotations", "production", "inventory", "invoices", "finance"},
    "sales": {"customers", "leads", "opportunities", "quotations"},
    "production": {"production", "inventory"},
    "merchandising": {"customers", "quotations", "production", "inventory"},
    "accounts": {"customers", "invoices", "finance"},
    "administration": set(),
    "quality_control": {"production"},
    "logistics": {"production", "inventory"},
    "it": set(),
    "marketing": {"customers", "leads", "opportunities"},
    "customer_service": {"customers", "leads"},
}


def employee_department(user):
    try:
        profile = user.employee_profile
        return profile.department_ref.code if profile.department_ref_id else (profile.department or "")
    except Exception:
        return ""


def get_access(user):
    access, _created = UserAccess.objects.get_or_create(user=user)
    return access


def operations_group_names(user):
    if not user or not getattr(user, "is_authenticated", False):
        return set()
    cached = getattr(user, "_operations_group_names", None)
    if cached is not None:
        return cached
    names = set(user.groups.filter(name__in=OPERATIONS_ROLES).values_list("name", flat=True))
    normalized = {name.casefold() for name in names}
    user._operations_group_names = normalized
    return normalized

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
        "director": ROLE_DIRECTOR,
        "manager": ROLE_MANAGER,
        "sales": ROLE_SALES,
        "production": ROLE_PRODUCTION,
        "accounts": ROLE_ACCOUNTS,
        "merchandising": ROLE_MERCHANDISING,
        "merchandiser": ROLE_MERCHANDISER,
        "supervisor": ROLE_SUPERVISOR,
        "finance": ROLE_FINANCE,
        "qc": ROLE_QC,
        "warehouse": ROLE_WAREHOUSE,
        "hr": ROLE_HR,
        "admin": ROLE_ADMIN,
        "read only": ROLE_READ_ONLY,
        "sales manager": ROLE_SALES_MANAGER,
    }
    return {canonical[name] for name in operations_group_names(user) if name in canonical}


def has_operations_role(user, *roles):
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    return bool(operations_role_names(user).intersection(roles))


def can_archive_invoices(user):
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser or has_operations_role(user, ROLE_CEO, ROLE_ADMIN):
        return True
    try:
        profile = user.employee_profile
    except Exception:
        return False
    position_code = profile.position_ref.code if profile.position_ref_id else profile.position
    return position_code == "accounts_manager"


def role_flag_decision(user, flag_name):
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    roles = operations_role_names(user)
    if not roles:
        return None
    if ROLE_CEO in roles:
        return True
    if any(flag_name in ROLE_FLAG_MATRIX.get(role, set()) for role in roles):
        return True
    if roles.intersection({ROLE_MANAGER, ROLE_SUPERVISOR}):
        department = employee_department(user)
        if department:
            return flag_name in DEPARTMENT_FLAG_MATRIX.get(department, set())
        return flag_name in ROLE_FLAG_MATRIX[ROLE_MANAGER]
    return False


def can_access_operations_module(user, module):
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True

    roles = operations_role_names(user)
    if roles:
        if any(module in ROLE_MODULES.get(role, set()) for role in roles):
            return True
        if roles.intersection({ROLE_MANAGER, ROLE_SUPERVISOR}):
            department = employee_department(user)
            if department:
                return module in DEPARTMENT_MODULES.get(department, set())
            # Preserve existing managers until a department is assigned.
            return module in {"customers", "leads", "opportunities", "quotations", "production", "inventory", "invoices"}
        return False

    try:
        access = get_access(user)
    except Exception:
        return False
    return any(bool(getattr(access, flag, False)) for flag in FALLBACK_FLAGS.get(module, ()))


LEAD_CLOSED_STATUSES = {"Converted", "Lost", "Unqualified"}
LEAD_CLOSED_OUTBOUND_STATUSES = {"Converted to Opportunity", "Archived", "Bad Fit"}


def _lead_field(prefix, field_name):
    return f"{prefix}{field_name}"


def active_sales_lead_q(prefix=""):
    return (
        Q(**{_lead_field(prefix, "is_archived"): False})
        & ~Q(**{f"{_lead_field(prefix, 'lead_status')}__in": LEAD_CLOSED_STATUSES})
        & ~Q(**{f"{_lead_field(prefix, 'outbound_status')}__in": LEAD_CLOSED_OUTBOUND_STATUSES})
    )


def available_sales_lead_q(prefix=""):
    return active_sales_lead_q(prefix) & Q(**{f"{_lead_field(prefix, 'assigned_to')}__isnull": True})


def is_available_sales_lead(lead):
    return bool(
        lead
        and not getattr(lead, "is_archived", False)
        and getattr(lead, "assigned_to_id", None) is None
        and getattr(lead, "lead_status", "") not in LEAD_CLOSED_STATUSES
        and getattr(lead, "outbound_status", "") not in LEAD_CLOSED_OUTBOUND_STATUSES
    )


def can_manage_all_sales_records(user):
    return has_operations_role(
        user,
        ROLE_CEO,
        ROLE_DIRECTOR,
        ROLE_ADMIN,
        ROLE_MANAGER,
        ROLE_SALES_MANAGER,
    )


def _has_legacy_lead_access(user):
    return not operations_role_names(user) and can_access_operations_module(user, "leads")


def can_claim_sales_lead(user):
    return (
        can_manage_all_sales_records(user)
        or has_operations_role(user, ROLE_SALES)
        or _has_legacy_lead_access(user)
    )


def can_release_sales_lead(user, lead):
    return can_manage_all_sales_records(user) or bool(
        user
        and getattr(user, "is_authenticated", False)
        and getattr(lead, "assigned_to_id", None) == user.pk
    )


def scope_sales_leads(queryset, user):
    if has_operations_role(user, ROLE_CEO, ROLE_DIRECTOR, ROLE_ADMIN):
        return queryset
    if has_operations_role(user, ROLE_MANAGER, ROLE_SUPERVISOR):
        department = employee_department(user)
        if department in {"sales", "marketing", "customer_service"}:
            return queryset.filter(assigned_to__employee_profile__department=department)
    if has_operations_role(user, ROLE_SALES):
        return queryset.filter(employee_lead_ownership_q(user))
    return queryset


def scope_sales_lead_queue(queryset, user):
    if can_manage_all_sales_records(user):
        return queryset
    if has_operations_role(user, ROLE_SALES) or _has_legacy_lead_access(user):
        return queryset.filter(Q(assigned_to=user) | available_sales_lead_q()).distinct()
    return queryset.none()


def scope_owned_sales_leads(queryset, user):
    if can_manage_all_sales_records(user):
        return queryset
    if has_operations_role(user, ROLE_SALES) or _has_legacy_lead_access(user):
        return queryset.filter(assigned_to=user)
    return queryset.none()


def scope_sales_opportunities(queryset, user):
    if can_manage_all_sales_records(user):
        return queryset
    if has_operations_role(user, ROLE_SALES):
        return queryset.filter(employee_lead_ownership_q(user, prefix="lead__"))
    return queryset


def scope_production_orders(queryset, user):
    if has_operations_role(user, ROLE_CEO, ROLE_DIRECTOR, ROLE_ADMIN):
        return queryset
    if has_operations_role(user, ROLE_ACCOUNTS, ROLE_FINANCE):
        return queryset
    if has_operations_role(user, ROLE_PRODUCTION):
        return queryset.filter(assigned_production_manager=user)
    if has_operations_role(user, ROLE_SALES):
        return queryset.filter(
            Q(lead__assigned_to=user)
            | employee_lead_ownership_q(user, prefix="opportunity__lead__")
        ).distinct()
    if has_operations_role(user, ROLE_MANAGER, ROLE_SUPERVISOR) and employee_department(user) == "production":
        return queryset.filter(assigned_production_manager=user)
    return queryset


def can_view_local_sewing_financials(user):
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    roles = operations_role_names(user)
    if roles:
        return bool(roles.intersection({ROLE_CEO, ROLE_ADMIN, ROLE_ACCOUNTS, ROLE_FINANCE}))
    try:
        access = get_access(user)
    except Exception:
        return False
    return bool(
        access.can_view_internal_costing
        and (access.can_accounting_ca or access.can_accounting_bd)
    )
