# crm/admin.py

from django.contrib import admin
from django.contrib.admin.sites import NotRegistered
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied

from crm.utils.activity_log import log_activity  # keep if you use it elsewhere

from .models import (
    ExchangeRate,
    Lead,
    Opportunity,
    Customer,
    BDStaff,
    BDStaffMonth,
    Product,
    Fabric,
    Accessory,
    Trim,
    ThreadOption,
    MoneyTransfer,
    AccountingMonthClose,
    AccountingEntry,
    AccountingMonthlyTarget,
    BDMonthlyTarget,
)

# Email config model is in another file in your project
from crm.models_email_config import EmailInboxConfig
from django.core.exceptions import PermissionDenied

def is_bd_user(user):
    return user.is_authenticated and user.groups.filter(name="BD_TEAM").exists()

# -------------------------
# Role helpers
# -------------------------

def is_bd_user(user):
    return user.is_authenticated and user.groups.filter(name="BD_TEAM").exists()


def is_ca_user(user):
    return user.is_authenticated and user.groups.filter(name="CA_TEAM").exists()


def block_bd(user):
    if is_bd_user(user):
        raise PermissionDenied("BD Team cannot access this page.")


# -------------------------
# Optional AI Health models
# -------------------------

AIHealthRun = None
AIHealthRunCheck = None
AIHealthIssue = None

try:
    from .models import AIHealthRun  # type: ignore
except Exception:
    pass

try:
    from .models import AIHealthRunCheck  # type: ignore
except Exception:
    pass

try:
    from .models import AIHealthIssue  # type: ignore
except Exception:
    pass


# -------------------------
# Helpers for Group presets
# -------------------------

def _perms_for_models(model_list, codenames=None):
    cts_map = ContentType.objects.get_for_models(*model_list)
    cts = list(cts_map.values())
    qs = Permission.objects.filter(content_type__in=cts)
    if codenames:
        qs = qs.filter(codename__in=codenames)
    return qs


def _crm_all_permissions():
    crm_models = [
        ExchangeRate,
        Lead,
        Opportunity,
        Customer,
        BDStaff,
        BDStaffMonth,
        Product,
        Fabric,
        Accessory,
        Trim,
        ThreadOption,
        MoneyTransfer,
        AccountingMonthClose,
        AccountingEntry,
        AccountingMonthlyTarget,
        BDMonthlyTarget,
    ]
    return _perms_for_models(crm_models)


def _bd_preset_permissions():
    # BD team can NOT access CA Accounting pages:
    # - All Entry (AccountingEntry admin)
    # - Money Transfer (MoneyTransfer admin)
    # So we do NOT give those permissions here.
    codes = [
        "add_bdstaff", "change_bdstaff", "view_bdstaff",
        "add_bdstaffmonth", "change_bdstaffmonth", "view_bdstaffmonth",
        "add_bdmonthlytarget", "change_bdmonthlytarget", "view_bdmonthlytarget",
        # Allow other CRM items as needed, add more codes later if you want
        "view_lead", "change_lead", "add_lead",
        "view_opportunity", "change_opportunity", "add_opportunity",
        "view_customer", "change_customer", "add_customer",
    ]
    return Permission.objects.filter(codename__in=codes)


def _ca_preset_permissions():
    return _crm_all_permissions()


@admin.action(description="Apply BD preset permissions")
def apply_bd_preset(modeladmin, request, queryset):
    perms = _bd_preset_permissions()
    for g in queryset:
        g.permissions.set(perms)
    modeladmin.message_user(request, "BD preset applied.")


@admin.action(description="Apply CA preset permissions")
def apply_ca_preset(modeladmin, request, queryset):
    perms = _ca_preset_permissions()
    for g in queryset:
        g.permissions.set(perms)
    modeladmin.message_user(request, "CA preset applied.")


@admin.action(description="Clear all permissions")
def clear_all_permissions(modeladmin, request, queryset):
    for g in queryset:
        g.permissions.clear()
    modeladmin.message_user(request, "All permissions cleared.")


# Replace default Group admin so we can add actions
try:
    admin.site.unregister(Group)
except admin.sites.NotRegistered:
    pass


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    search_fields = ("name",)
    filter_horizontal = ("permissions",)
    actions = [apply_bd_preset, apply_ca_preset, clear_all_permissions]


# -------------------------
# Model admins
# -------------------------

@admin.register(ExchangeRate)
class ExchangeRateAdmin(admin.ModelAdmin):
    list_display = ("cad_to_bdt", "updated_at")


@admin.register(BDStaff)
class BDStaffAdmin(admin.ModelAdmin):
    list_display = ("name", "role", "base_salary_bdt", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "role")


@admin.register(BDStaffMonth)
class BDStaffMonthAdmin(admin.ModelAdmin):
    list_display = (
        "staff",
        "year",
        "month",
        "base_salary_bdt",
        "overtime_hours",
        "overtime_total_bdt",
        "final_pay_bdt",
        "is_paid",
    )
    list_filter = ("year", "month", "is_paid")
    search_fields = ("staff__name",)


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = (
        "lead_id",
        "account_brand",
        "contact_name",
        "owner",
        "source",
        "lead_type",
        "lead_status",
        "priority",
        "market",
        "created_date",
    )
    list_filter = ("source", "lead_type", "lead_status", "priority", "market", "owner")
    search_fields = ("lead_id", "account_brand", "contact_name", "email", "phone")


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = (
        "customer_code",
        "account_brand",
        "contact_name",
        "email",
        "phone",
        "market",
        "is_active",
        "created_date",
    )
    list_filter = ("market", "is_active")
    search_fields = ("customer_code", "account_brand", "contact_name", "email", "phone")


@admin.register(Opportunity)
class OpportunityAdmin(admin.ModelAdmin):
    list_display = (
        "opportunity_id",
        "lead",
        "product_type",
        "product_category",
        "order_value",
        "stage",
        "is_open",
        "created_date",
    )
    list_filter = ("stage", "is_open")
    search_fields = ("opportunity_id", "lead__lead_id", "lead__account_brand")


# -------------------------
# BLOCKED for BD team (Admin)
# - All Entry
# - Add Canada Entry
# -------------------------
@admin.register(AccountingEntry)
class AccountingEntryAdmin(admin.ModelAdmin):
    list_display = (
        "date",
        "side",
        "main_type",
        "direction",
        "amount_original",
        "currency",
        "amount_cad",
        "amount_bdt",
        "customer",
        "opportunity",
        "production_order",
    )
    list_filter = ("side", "main_type", "direction", "currency")
    search_fields = (
        "description",
        "sub_type",
        "customer__name",
        "opportunity__opportunity_id",
        "production_order__order_code",
    )
    date_hierarchy = "date"

    def has_module_permission(self, request):
        if is_bd_user(request.user):
            return False
        return super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        if is_bd_user(request.user):
            return False
        return super().has_view_permission(request, obj=obj)

    def has_add_permission(self, request):
        if is_bd_user(request.user):
            return False
        return super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        if is_bd_user(request.user):
            return False
        return super().has_change_permission(request, obj=obj)

    def has_delete_permission(self, request, obj=None):
        if is_bd_user(request.user):
            return False
        return super().has_delete_permission(request, obj=obj)


# -------------------------
# BLOCKED for BD team (Admin)
# - Money Transfer
# -------------------------

@admin.register(MoneyTransfer)
class MoneyTransferAdmin(admin.ModelAdmin):
    list_display = ("created_at", "sent_method", "amount_cad", "amount_bdt", "receiver_name")
    list_filter = ("sent_method", "created_at")
    search_fields = ("receiver_name", "note")

    def has_module_permission(self, request):
        if is_bd_user(request.user):
            return False
        return super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        block_bd(request.user)
        return super().has_view_permission(request, obj=obj)

    def has_add_permission(self, request):
        block_bd(request.user)
        return super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        block_bd(request.user)
        return super().has_change_permission(request, obj=obj)

    def has_delete_permission(self, request, obj=None):
        block_bd(request.user)
        return super().has_delete_permission(request, obj=obj)


@admin.register(AccountingMonthClose)
class AccountingMonthCloseAdmin(admin.ModelAdmin):
    list_display = ("year", "month", "side", "is_closed", "closed_at", "closed_by")
    list_filter = ("side", "is_closed", "year", "month")
    search_fields = ("note",)
    actions = ["mark_closed", "mark_open"]

    def mark_closed(self, request, queryset):
        queryset.update(is_closed=True)
        for obj in queryset:
            if not obj.closed_by:
                obj.closed_by = request.user
                obj.save(update_fields=["closed_by"])

    def mark_open(self, request, queryset):
        queryset.update(is_closed=False)
        for obj in queryset:
            obj.closed_by = request.user
            obj.save(update_fields=["closed_by"])


@admin.register(AccountingMonthlyTarget)
class AccountingMonthlyTargetAdmin(admin.ModelAdmin):
    list_display = ("side", "year", "month", "target_bdt", "updated_at", "updated_by")
    list_filter = ("side", "year", "month")
    search_fields = ("side",)


@admin.register(BDMonthlyTarget)
class BDMonthlyTargetAdmin(admin.ModelAdmin):
    list_display = ("year", "month", "target_bdt", "updated_at", "updated_by")
    list_filter = ("year", "month")
    search_fields = ("year", "month")


# -------------------------
# AI Health admin (register once, safe)
# -------------------------

if AIHealthIssue:
    try:
        admin.site.unregister(AIHealthIssue)
    except admin.sites.NotRegistered:
        pass

    @admin.register(AIHealthIssue)
    class AIHealthIssueAdmin(admin.ModelAdmin):
        list_display = ("id", "created_at", "title", "severity", "source", "is_resolved", "created_by")
        list_filter = ("severity", "source", "is_resolved", "created_at")
        search_fields = ("title", "details")


if AIHealthRun:
    try:
        admin.site.unregister(AIHealthRun)
    except admin.sites.NotRegistered:
        pass

    @admin.register(AIHealthRun)
    class AIHealthRunAdmin(admin.ModelAdmin):
        list_display = ("id", "created_at", "score", "ok_count", "warn_count", "bad_count", "created_by")
        list_filter = ("created_at",)
        search_fields = ("id",)


if AIHealthRunCheck:
    try:
        admin.site.unregister(AIHealthRunCheck)
    except NotRegistered:
        pass

    @admin.register(AIHealthRunCheck)
    class AIHealthRunCheckAdmin(admin.ModelAdmin):
        list_display = (
            "id",
            "run",
            "name",
            "status",
            "detail",
            "created_at",
        )
        list_select_related = ("run",)
        search_fields = ("name", "status", "detail")
        list_filter = ("status",)


# -------------------------
# Email inbox config
# -------------------------

@admin.register(EmailInboxConfig)
class EmailInboxConfigAdmin(admin.ModelAdmin):
    list_display = ("label", "username", "imap_host", "imap_port", "use_ssl", "is_enabled", "updated_at")
    list_filter = ("label", "use_ssl", "is_enabled")
    search_fields = ("username", "imap_host")