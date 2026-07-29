from decimal import Decimal


ROLE_TEMPLATE_DEFINITIONS = (
    (
        "ceo",
        "CEO",
        (
            ("Revenue and profit performance", 25),
            ("Sales pipeline growth", 15),
            ("Team leadership", 15),
            ("Client trust and retention", 15),
            ("CRM and financial control", 15),
            ("Strategy and improvement", 15),
        ),
    ),
    (
        "director",
        "Director",
        (
            ("Sample quality approval", 25),
            ("Management oversight", 20),
            ("Canada and Bangladesh reporting", 15),
            ("Team accountability", 15),
            ("Client and product quality", 15),
            ("Process improvement", 10),
        ),
    ),
    (
        "north-america-sales-representative",
        "North America Sales Representative",
        (
            ("New lead contact", 15),
            ("Follow up activity", 15),
            ("Qualified opportunities", 15),
            ("Meetings and quotations", 15),
            ("Sampling conversion", 15),
            ("Production sales", 15),
            ("CRM record quality", 10),
        ),
    ),
    (
        "project-manager",
        "Project Manager",
        (
            ("Project setup", 15),
            ("Client updates", 20),
            ("Approval management", 15),
            ("Timeline control", 20),
            ("Documentation", 15),
            ("Client satisfaction", 15),
        ),
    ),
    (
        "factory-manager",
        "Factory Manager",
        (
            ("Material and trim readiness", 20),
            ("Factory floor control", 20),
            ("Supplier management", 15),
            ("Bangladesh local sales support", 15),
            ("Booking and shipment memo", 15),
            ("Cost and waste control", 15),
        ),
    ),
    (
        "merchandiser",
        "Merchandiser",
        (
            ("Order details and documentation", 20),
            ("Material sourcing follow up", 20),
            ("Client and factory coordination", 20),
            ("Timeline monitoring", 15),
            ("Cost tracking", 15),
            ("Shipment preparation", 10),
        ),
    ),
    (
        "production-manager",
        "Production Manager",
        (
            ("Production planning", 20),
            ("On time production", 20),
            ("Quality results", 20),
            ("Team productivity", 15),
            ("Delay reporting and recovery", 15),
            ("Final handover", 10),
        ),
    ),
    (
        "pattern-master",
        "Pattern Master",
        (
            ("Pattern accuracy", 30),
            ("Measurement accuracy", 20),
            ("Grading quality", 15),
            ("Correction rate", 15),
            ("Development speed", 10),
            ("Pattern documentation", 10),
        ),
    ),
    (
        "sample-development",
        "Sample Development",
        (
            ("Sample completion time", 20),
            ("Sample quality", 25),
            ("Sample Information Card", 15),
            ("Research and development", 15),
            ("Sample library", 15),
            ("Correction rate", 10),
        ),
    ),
    (
        "quality-control-inspector",
        "Quality Control Inspector",
        (
            ("Incoming material inspection", 15),
            ("In process inspection", 20),
            ("Final inspection", 25),
            ("Defect rate", 15),
            ("QC documentation", 15),
            ("Issue escalation", 10),
        ),
    ),
    (
        "accounts",
        "Accounts",
        (
            ("Invoice accuracy", 20),
            ("Invoice timing", 15),
            ("Payment follow up", 20),
            ("Financial records", 20),
            ("Monthly reporting", 15),
            ("Data protection", 10),
        ),
    ),
    (
        "marketing",
        "Marketing",
        (
            ("Content production", 20),
            ("Content schedule", 15),
            ("Lead generation", 20),
            ("Brand quality", 15),
            ("Marketing reporting", 15),
            ("Sales support", 15),
        ),
    ),
    (
        "administration",
        "Administration",
        (
            ("Record accuracy", 25),
            ("Task completion", 20),
            ("Document control", 20),
            ("Communication support", 15),
            ("Reporting", 10),
            ("Data protection", 10),
        ),
    ),
    (
        "production-support",
        "Production Support",
        (
            ("Daily production follow up", 25),
            ("Delay reporting", 20),
            ("Photo and status collection", 15),
            ("CRM updates", 15),
            ("Team coordination", 15),
            ("Issue escalation", 10),
        ),
    ),
    (
        "bangladesh-local-sales",
        "Bangladesh Local Sales",
        (
            ("New client development", 20),
            ("CMT quotation activity", 15),
            ("Confirmed orders", 20),
            ("Sewing revenue", 20),
            ("Payment follow up", 15),
            ("CRM accuracy", 10),
        ),
    ),
)

DEFAULT_RED_MIN = Decimal("0.00")
DEFAULT_RED_MAX = Decimal("69.99")
DEFAULT_YELLOW_MIN = Decimal("70.00")
DEFAULT_YELLOW_MAX = Decimal("84.99")
DEFAULT_GREEN_MIN = Decimal("85.00")
DEFAULT_GREEN_MAX = Decimal("100.00")

DEFAULT_NOTIFICATION_EVENTS = (
    "review_open",
    "review_due_soon",
    "review_due_today",
    "review_overdue",
    "approval_pending",
    "rejected_correction",
    "locked_confirmation",
    "improvement_action",
    "critical_red",
    "yellow_attention",
    "green_recognition",
    "bonus_ready",
    "bonus_pending",
    "bonus_blocked",
    "manager_queue",
    "executive_intelligence",
    "data_quality",
)

DEFAULT_NOTIFICATION_ESCALATIONS = (
    ("review_open", 0, 0, "information", ("employee", "manager")),
    ("review_due_soon", 0, 7, "normal", ("employee",)),
    ("review_due_soon", 1, 3, "normal", ("employee",)),
    ("review_due_soon", 2, 1, "high", ("employee",)),
    ("review_due_today", 0, 0, "high", ("employee", "manager")),
    ("review_overdue", 0, -1, "high", ("employee", "manager")),
    ("review_overdue", 1, -7, "critical", ("manager", "director")),
    ("approval_pending", 0, 0, "high", ("director",)),
    ("rejected_correction", 0, 0, "high", ("manager",)),
    ("locked_confirmation", 0, 0, "information", ("employee", "manager")),
    ("improvement_action", 0, -1, "normal", ("employee", "manager")),
    ("critical_red", 0, 0, "critical", ("manager", "director", "executive")),
    ("critical_red", 1, -7, "critical", ("manager", "director", "executive")),
    ("yellow_attention", 0, 0, "normal", ("manager",)),
    ("green_recognition", 0, 0, "information", ("employee", "manager")),
    ("bonus_ready", 0, 0, "information", ("manager", "executive")),
    ("bonus_pending", 0, 0, "high", ("manager", "executive")),
    ("bonus_blocked", 0, 0, "critical", ("manager", "executive")),
    ("manager_queue", 0, 0, "high", ("manager", "director", "executive")),
    ("executive_intelligence", 0, 0, "high", ("executive",)),
    ("data_quality", 0, 0, "normal", ("hr", "executive")),
)
