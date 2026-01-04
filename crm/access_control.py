from dataclasses import dataclass

# Simple module keys you can use everywhere
MODULE_LEADS = "leads"
MODULE_OPPORTUNITIES = "opportunities"
MODULE_PRODUCTION = "production"
MODULE_SHIPPING = "shipping"
MODULE_INVENTORY = "inventory"

MODULE_ACCOUNTING_BD = "accounting_bd"
MODULE_ACCOUNTING_CA = "accounting_ca"
MODULE_ADMIN_TOOLS = "admin_tools"


@dataclass(frozen=True)
class Module:
    key: str
    label: str
    description: str


MODULES = [
    Module(MODULE_LEADS, "Leads", "View and manage leads."),
    Module(MODULE_OPPORTUNITIES, "Opportunities", "View and manage opportunities."),
    Module(MODULE_PRODUCTION, "Production", "Production orders and updates."),
    Module(MODULE_SHIPPING, "Shipping", "Shipments and tracking."),
    Module(MODULE_INVENTORY, "Inventory", "Stock and items."),
    Module(MODULE_ACCOUNTING_BD, "Bangladesh accounting", "BD dashboard and BD grid only."),
    Module(MODULE_ACCOUNTING_CA, "Canada money transfer", "CA money transfer only."),
    Module(MODULE_ADMIN_TOOLS, "Admin tools", "Settings and management pages."),
]

# Role presets (one click)
ROLE_PRESETS = {
    "BD_TEAM": {
        MODULE_LEADS,
        MODULE_OPPORTUNITIES,
        MODULE_ACCOUNTING_BD,
    },
    "SALES_TEAM": {
        MODULE_LEADS,
        MODULE_OPPORTUNITIES,
    },
    "PRODUCTION_TEAM": {
        MODULE_PRODUCTION,
    },
    "CANADA_TEAM": {
        MODULE_LEADS,
        MODULE_OPPORTUNITIES,
        MODULE_PRODUCTION,
        MODULE_SHIPPING,
        MODULE_INVENTORY,
        MODULE_ACCOUNTING_BD,
        MODULE_ACCOUNTING_CA,
        MODULE_ADMIN_TOOLS,
    },
}

def module_set_from_keys(keys):
    keys = set(keys or [])
    allowed = {m.key for m in MODULES}
    return {k for k in keys if k in allowed}