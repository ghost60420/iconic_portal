from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction

from crm.models import ExpenseCategory, FinancialAccount


class ChartOfAccountsError(ValueError):
    pass


@dataclass(frozen=True)
class AccountDefinition:
    code: str
    name: str
    account_type: str
    normal_balance: str
    system_key: str
    parent_key: str = ""
    subtype: str = ""
    control: bool = False
    manual: bool = True
    sensitive: bool = False


D = FinancialAccount.NORMAL_DEBIT
C = FinancialAccount.NORMAL_CREDIT
A = FinancialAccount.TYPE_ASSET
L = FinancialAccount.TYPE_LIABILITY
E = FinancialAccount.TYPE_EQUITY
R = FinancialAccount.TYPE_REVENUE
G = FinancialAccount.TYPE_COGS
O = FinancialAccount.TYPE_OPERATING_EXPENSE
OI = FinancialAccount.TYPE_OTHER_INCOME
OE = FinancialAccount.TYPE_OTHER_EXPENSE


ACCOUNT_DEFINITIONS = (
    AccountDefinition("1000", "Assets", A, D, "ASSETS", manual=False),
    AccountDefinition("1010", "Cash in Hand", A, D, "CASH_IN_HAND", "ASSETS", "CASH", control=True),
    AccountDefinition("1020", "Canadian Bank Accounts", A, D, "CANADIAN_BANK", "ASSETS", "BANK", control=True, sensitive=True),
    AccountDefinition("1030", "Bangladesh Bank Accounts", A, D, "BANGLADESH_BANK", "ASSETS", "BANK", control=True, sensitive=True),
    AccountDefinition("1040", "PayPal and Payment Accounts", A, D, "PAYMENT_ACCOUNTS", "ASSETS", "BANK", control=True, sensitive=True),
    AccountDefinition("1100", "Accounts Receivable", A, D, "ACCOUNTS_RECEIVABLE", "ASSETS", "RECEIVABLE", control=True, manual=False),
    AccountDefinition("1150", "Supplier Advances", A, D, "SUPPLIER_ADVANCES", "ASSETS", "SUPPLIER_CREDIT", control=True, manual=False),
    AccountDefinition("1200", "Inventory", A, D, "INVENTORY", "ASSETS", "INVENTORY", control=True),
    AccountDefinition("1300", "Prepaid Expenses", A, D, "PREPAID_EXPENSES", "ASSETS"),
    AccountDefinition("1350", "Foreign Exchange Clearing", A, D, "FX_CLEARING", "ASSETS", "CLEARING", control=True, manual=False),
    AccountDefinition("1400", "Equipment", A, D, "EQUIPMENT", "ASSETS", "FIXED_ASSET"),
    AccountDefinition("1410", "Machinery", A, D, "MACHINERY", "ASSETS", "FIXED_ASSET"),
    AccountDefinition("1420", "Vehicles", A, D, "VEHICLES", "ASSETS", "FIXED_ASSET"),
    AccountDefinition("1900", "Other Assets", A, D, "OTHER_ASSETS", "ASSETS"),
    AccountDefinition("2000", "Liabilities", L, C, "LIABILITIES", manual=False),
    AccountDefinition("2010", "Accounts Payable", L, C, "ACCOUNTS_PAYABLE", "LIABILITIES", "PAYABLE", control=True, manual=False),
    AccountDefinition("2020", "Customer Deposits", L, C, "CUSTOMER_DEPOSITS", "LIABILITIES", "CUSTOMER_CREDIT", control=True, manual=False),
    AccountDefinition("2030", "Taxes Payable", L, C, "TAXES_PAYABLE", "LIABILITIES", "TAX", control=True),
    AccountDefinition("2040", "Payroll Payable", L, C, "PAYROLL_PAYABLE", "LIABILITIES", "PAYROLL", control=True, sensitive=True),
    AccountDefinition("2050", "Loans Payable", L, C, "LOANS_PAYABLE", "LIABILITIES", "LOAN", control=True, sensitive=True),
    AccountDefinition("2060", "Accrued Expenses", L, C, "ACCRUED_EXPENSES", "LIABILITIES"),
    AccountDefinition("2900", "Other Liabilities", L, C, "OTHER_LIABILITIES", "LIABILITIES"),
    AccountDefinition("3000", "Equity", E, C, "EQUITY", manual=False, sensitive=True),
    AccountDefinition("3010", "Owner Investment", E, C, "OWNER_INVESTMENT", "EQUITY", sensitive=True),
    AccountDefinition("3020", "Owner Withdrawals", E, D, "OWNER_WITHDRAWALS", "EQUITY", sensitive=True),
    AccountDefinition("3030", "Retained Earnings", E, C, "RETAINED_EARNINGS", "EQUITY", manual=False, sensitive=True),
    AccountDefinition("3040", "Current Year Earnings", E, C, "CURRENT_YEAR_EARNINGS", "EQUITY", manual=False, sensitive=True),
    AccountDefinition("4000", "Revenue", R, C, "REVENUE", manual=False),
    AccountDefinition("4010", "Product Sales", R, C, "PRODUCT_SALES", "REVENUE", "OPERATING"),
    AccountDefinition("4020", "Sample Revenue", R, C, "SAMPLE_REVENUE", "REVENUE", "OPERATING"),
    AccountDefinition("4030", "Full Package Revenue", R, C, "FULL_PACKAGE_REVENUE", "REVENUE", "OPERATING"),
    AccountDefinition("4040", "FOB Revenue", R, C, "FOB_REVENUE", "REVENUE", "OPERATING"),
    AccountDefinition("4050", "Door to Door Revenue", R, C, "DOOR_TO_DOOR_REVENUE", "REVENUE", "OPERATING"),
    AccountDefinition("4060", "CMT or Sewing Revenue", R, C, "CMT_REVENUE", "REVENUE", "OPERATING"),
    AccountDefinition("4070", "Rework Revenue", R, C, "REWORK_REVENUE", "REVENUE", "OPERATING"),
    AccountDefinition("4080", "Shipping Revenue", R, C, "SHIPPING_REVENUE", "REVENUE", "OPERATING"),
    AccountDefinition("4090", "Other Revenue", R, C, "OTHER_REVENUE", "REVENUE", "OPERATING"),
    AccountDefinition("4900", "Other Income", OI, C, "OTHER_INCOME", subtype="NON_OPERATING"),
    AccountDefinition("4910", "Foreign Exchange Gain", OI, C, "FX_GAIN", subtype="FOREIGN_EXCHANGE"),
    AccountDefinition("5000", "Cost of Goods Sold", G, D, "COST_OF_GOODS_SOLD", manual=False),
    AccountDefinition("5010", "Fabric", G, D, "COGS_FABRIC", "COST_OF_GOODS_SOLD"),
    AccountDefinition("5020", "Trims and Accessories", G, D, "COGS_TRIMS", "COST_OF_GOODS_SOLD"),
    AccountDefinition("5030", "Cutting", G, D, "COGS_CUTTING", "COST_OF_GOODS_SOLD"),
    AccountDefinition("5040", "Sewing", G, D, "COGS_SEWING", "COST_OF_GOODS_SOLD"),
    AccountDefinition("5050", "Printing", G, D, "COGS_PRINTING", "COST_OF_GOODS_SOLD"),
    AccountDefinition("5060", "Embroidery", G, D, "COGS_EMBROIDERY", "COST_OF_GOODS_SOLD"),
    AccountDefinition("5070", "Washing", G, D, "COGS_WASHING", "COST_OF_GOODS_SOLD"),
    AccountDefinition("5080", "Packing", G, D, "COGS_PACKING", "COST_OF_GOODS_SOLD"),
    AccountDefinition("5090", "Production Labor", G, D, "COGS_PRODUCTION_LABOR", "COST_OF_GOODS_SOLD"),
    AccountDefinition("5100", "Production Shipping", G, D, "COGS_PRODUCTION_SHIPPING", "COST_OF_GOODS_SOLD"),
    AccountDefinition("5110", "Duty", G, D, "COGS_DUTY", "COST_OF_GOODS_SOLD"),
    AccountDefinition("5120", "Factory Overhead Allocation", G, D, "COGS_FACTORY_OVERHEAD", "COST_OF_GOODS_SOLD"),
    AccountDefinition("5190", "Other Direct Cost", G, D, "COGS_OTHER_DIRECT", "COST_OF_GOODS_SOLD"),
    AccountDefinition("6000", "Operating Expenses", O, D, "OPERATING_EXPENSES", manual=False),
    AccountDefinition("6010", "Office Rent", O, D, "OFFICE_RENT", "OPERATING_EXPENSES"),
    AccountDefinition("6020", "Factory Rent", O, D, "FACTORY_RENT", "OPERATING_EXPENSES"),
    AccountDefinition("6030", "Canada Salaries", O, D, "CANADA_SALARIES", "OPERATING_EXPENSES", sensitive=True),
    AccountDefinition("6040", "Bangladesh Salaries", O, D, "BANGLADESH_SALARIES", "OPERATING_EXPENSES", sensitive=True),
    AccountDefinition("6050", "Bonuses", O, D, "BONUSES", "OPERATING_EXPENSES", sensitive=True),
    AccountDefinition("6060", "Overtime", O, D, "OVERTIME", "OPERATING_EXPENSES", sensitive=True),
    AccountDefinition("6070", "Payroll Taxes", O, D, "PAYROLL_TAXES", "OPERATING_EXPENSES", sensitive=True),
    AccountDefinition("6080", "Electricity", O, D, "ELECTRICITY", "OPERATING_EXPENSES"),
    AccountDefinition("6090", "Hydro", O, D, "HYDRO", "OPERATING_EXPENSES"),
    AccountDefinition("6100", "Water", O, D, "WATER", "OPERATING_EXPENSES"),
    AccountDefinition("6110", "Gas", O, D, "GAS", "OPERATING_EXPENSES"),
    AccountDefinition("6120", "Internet", O, D, "INTERNET", "OPERATING_EXPENSES"),
    AccountDefinition("6130", "Telephone", O, D, "TELEPHONE", "OPERATING_EXPENSES"),
    AccountDefinition("6140", "Software", O, D, "SOFTWARE", "OPERATING_EXPENSES"),
    AccountDefinition("6150", "Marketing", O, D, "MARKETING", "OPERATING_EXPENSES"),
    AccountDefinition("6160", "Travel", O, D, "TRAVEL", "OPERATING_EXPENSES"),
    AccountDefinition("6170", "Fuel", O, D, "FUEL", "OPERATING_EXPENSES"),
    AccountDefinition("6180", "Vehicle Expenses", O, D, "VEHICLE_EXPENSES", "OPERATING_EXPENSES"),
    AccountDefinition("6190", "Machine Maintenance", O, D, "MACHINE_MAINTENANCE", "OPERATING_EXPENSES"),
    AccountDefinition("6200", "Building Maintenance", O, D, "BUILDING_MAINTENANCE", "OPERATING_EXPENSES"),
    AccountDefinition("6210", "Repairs", O, D, "REPAIRS", "OPERATING_EXPENSES"),
    AccountDefinition("6220", "Cleaning", O, D, "CLEANING", "OPERATING_EXPENSES"),
    AccountDefinition("6230", "Security", O, D, "SECURITY", "OPERATING_EXPENSES"),
    AccountDefinition("6240", "Insurance", O, D, "INSURANCE", "OPERATING_EXPENSES"),
    AccountDefinition("6250", "Legal Fees", O, D, "LEGAL_FEES", "OPERATING_EXPENSES"),
    AccountDefinition("6260", "Accounting Fees", O, D, "ACCOUNTING_FEES", "OPERATING_EXPENSES"),
    AccountDefinition("6270", "Bank Fees", O, D, "BANK_FEES", "OPERATING_EXPENSES"),
    AccountDefinition("6280", "Office Supplies", O, D, "OFFICE_SUPPLIES", "OPERATING_EXPENSES"),
    AccountDefinition("6290", "Administrative Expenses", O, D, "ADMINISTRATIVE_EXPENSES", "OPERATING_EXPENSES"),
    AccountDefinition("6300", "Taxes", O, D, "TAX_EXPENSE", "OPERATING_EXPENSES"),
    AccountDefinition("6310", "Shipping and Duty Expense", O, D, "SHIPPING_DUTY_EXPENSE", "OPERATING_EXPENSES"),
    AccountDefinition("6320", "Professional Fees", O, D, "PROFESSIONAL_FEES", "OPERATING_EXPENSES"),
    AccountDefinition("6990", "Miscellaneous Expense", O, D, "MISCELLANEOUS_EXPENSE", "OPERATING_EXPENSES"),
    AccountDefinition("7000", "Interest Expense", OE, D, "INTEREST_EXPENSE", subtype="FINANCING"),
    AccountDefinition("7010", "Other Expense", OE, D, "OTHER_EXPENSE", subtype="NON_OPERATING"),
    AccountDefinition("7020", "Foreign Exchange Loss", OE, D, "FX_LOSS", subtype="FOREIGN_EXCHANGE"),
)


EXPENSE_CATEGORY_DEFINITIONS = (
    ("OFFICE_RENT", "Office rent", "", "OFFICE_RENT"),
    ("FACTORY_RENT", "Factory rent", "", "FACTORY_RENT"),
    ("SALARY_CA", "Salary", "Canada", "CANADA_SALARIES"),
    ("SALARY_BD", "Salary", "Bangladesh", "BANGLADESH_SALARIES"),
    ("WAGES", "Wages", "", "COGS_PRODUCTION_LABOR"),
    ("BONUS", "Bonus", "", "BONUSES"),
    ("OVERTIME", "Overtime", "", "OVERTIME"),
    ("ELECTRICITY", "Electricity", "", "ELECTRICITY"),
    ("HYDRO", "Hydro", "", "HYDRO"),
    ("WATER", "Water", "", "WATER"),
    ("GAS", "Gas", "", "GAS"),
    ("INTERNET", "Internet", "", "INTERNET"),
    ("PHONE", "Phone", "", "TELEPHONE"),
    ("SOFTWARE", "Software", "", "SOFTWARE"),
    ("CLEANING", "Cleaning", "", "CLEANING"),
    ("SECURITY", "Security", "", "SECURITY"),
    ("MAINTENANCE", "Maintenance", "", "BUILDING_MAINTENANCE"),
    ("REPAIRS", "Repairs", "", "REPAIRS"),
    ("MACHINE_SERVICE", "Machine service", "", "MACHINE_MAINTENANCE"),
    ("VEHICLE", "Vehicle expense", "", "VEHICLE_EXPENSES"),
    ("FUEL", "Fuel", "", "FUEL"),
    ("TRAVEL", "Travel", "", "TRAVEL"),
    ("MARKETING", "Marketing", "", "MARKETING"),
    ("PROFESSIONAL_FEES", "Professional fees", "", "PROFESSIONAL_FEES"),
    ("BANK_FEES", "Bank fees", "", "BANK_FEES"),
    ("INSURANCE", "Insurance", "", "INSURANCE"),
    ("OFFICE_SUPPLIES", "Office supplies", "", "OFFICE_SUPPLIES"),
    ("TAXES", "Taxes", "", "TAX_EXPENSE"),
    ("MISCELLANEOUS", "Miscellaneous", "", "MISCELLANEOUS_EXPENSE"),
)


def account_by_key(system_key: str) -> FinancialAccount:
    key = (system_key or "").upper().strip()
    if not key:
        raise ChartOfAccountsError("An account system key is required.")
    try:
        return FinancialAccount.objects.get(system_key=key, is_active=True)
    except FinancialAccount.DoesNotExist as exc:
        raise ChartOfAccountsError(f"Financial account '{key}' is not configured.") from exc


@transaction.atomic
def bootstrap_chart_of_accounts(*, actor, dry_run=False) -> dict:
    if not actor or not getattr(actor, "is_authenticated", False):
        raise ChartOfAccountsError("An authenticated actor is required.")

    created = 0
    updated = 0
    accounts = {row.system_key: row for row in FinancialAccount.objects.all() if row.system_key}
    for definition in ACCOUNT_DEFINITIONS:
        parent = accounts.get(definition.parent_key) if definition.parent_key else None
        defaults = {
            "code": definition.code,
            "name": definition.name,
            "account_type": definition.account_type,
            "normal_balance": definition.normal_balance,
            "parent": parent,
            "subtype": definition.subtype,
            "is_control_account": definition.control,
            "allow_manual_posting": definition.manual,
            "is_sensitive": definition.sensitive,
            "is_active": True,
            "modified_by": actor,
        }
        existing = accounts.get(definition.system_key)
        if existing:
            structural = ("code", "account_type", "normal_balance", "parent_id")
            proposed = {**defaults, "parent_id": parent.pk if parent else None}
            if existing.journal_lines.exists() and any(getattr(existing, field) != proposed[field] for field in structural):
                raise ChartOfAccountsError(
                    f"Account {definition.system_key} has ledger activity and its accounting structure cannot be changed."
                )
            changed = False
            for field, value in defaults.items():
                if getattr(existing, field) != value:
                    setattr(existing, field, value)
                    changed = True
            if changed:
                existing.save()
                updated += 1
            accounts[definition.system_key] = existing
            continue

        account = FinancialAccount(
            system_key=definition.system_key,
            created_by=actor,
            **defaults,
        )
        account.full_clean()
        account.save()
        accounts[definition.system_key] = account
        created += 1

    category_created = 0
    for code, name, subcategory, account_key in EXPENSE_CATEGORY_DEFINITIONS:
        category, was_created = ExpenseCategory.objects.get_or_create(
            code=code,
            defaults={
                "name": name,
                "subcategory": subcategory,
                "default_account": accounts[account_key],
                "is_production_cost": account_key.startswith("COGS_"),
                "is_active": True,
                "created_by": actor,
                "modified_by": actor,
            },
        )
        if not was_created:
            category.name = name
            category.subcategory = subcategory
            category.default_account = accounts[account_key]
            category.is_production_cost = account_key.startswith("COGS_")
            category.is_active = True
            category.modified_by = actor
            category.save()
        category_created += int(was_created)
        try:
            category.full_clean()
        except ValidationError as exc:
            raise ChartOfAccountsError(str(exc)) from exc

    result = {
        "accounts_created": created,
        "accounts_updated": updated,
        "categories_created": category_created,
        "account_total": len(ACCOUNT_DEFINITIONS),
        "category_total": len(EXPENSE_CATEGORY_DEFINITIONS),
    }
    if dry_run:
        transaction.set_rollback(True)
    return result
