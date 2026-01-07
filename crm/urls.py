from django.urls import path
from django.contrib.auth import views as auth_views
from django.urls import path
from . import views_invoice as inv
from . import views
from . import views_ai as ai
from .whatsapp_webhook import whatsapp_webhook
from crm import views_email
from crm import views_whatsapp as wa
from . import views_accounting as acc


urlpatterns = [
    # Home
    path("", views.leads_list, name="home"),

    # Main dashboard
    path("main-dashboard/", views.main_dashboard, name="main_dashboard"),

    # Auth (uncomment if you use them)
    # path("login/", auth_views.LoginView.as_view(template_name="crm/login.html"), name="login"),
    # path("logout/", auth_views.LogoutView.as_view(), name="logout"),

    # LEADS
    path("leads/", views.leads_list, name="leads_list"),
    path("leads/add/", views.add_lead, name="add_lead"),
    path("leads/<int:pk>/", views.lead_detail, name="lead_detail"),
    path("leads/<int:pk>/edit/", views.edit_lead, name="edit_lead"),
    path("leads/<int:pk>/convert/", views.convert_lead_to_opportunity, name="convert_lead_to_opportunity"),

    # Old AI (keep if still used)
    path("leads/ai/overview/", views.leads_ai_overview, name="leads_ai_overview"),
    path("leads/<int:pk>/ai/", views.lead_ai_detail, name="lead_ai_detail"),

    # New AI endpoints (views_ai.py)
    path("leads/<int:pk>/ai/suggest/", ai.ai_lead_suggest, name="ai_lead_suggest"),
    path("leads/<int:pk>/ai/thank-you/", ai.ai_lead_send_thankyou, name="ai_lead_send_thankyou"),
    path("leads/<int:pk>/ai/meeting-confirm/", ai.ai_lead_send_meeting_confirm, name="ai_lead_send_meeting_confirm"),

    # OPPORTUNITIES
    path("opportunities/", views.opportunities_list, name="opportunities_list"),
    path("opportunities/add/", views.add_opportunity, name="add_opportunity"),
    path("opportunities/<int:pk>/", views.opportunity_detail, name="opportunity_detail"),
    path("opportunities/<int:pk>/edit/", views.opportunity_edit, name="opportunity_edit"),
    path("opportunities/<int:pk>/ai/", views.opportunity_ai_detail, name="opportunity_ai_detail"),

    # CUSTOMERS
    path("customers/", views.customers_list, name="customers_list"),
    path("customers/<int:pk>/", views.customer_detail, name="customer_detail"),
    path("customers/<int:pk>/ai/", views.customer_ai_detail, name="customer_ai_detail"),
    path("customers/ai/overview/", views.customer_ai_overview, name="customer_ai_overview"),
    path("customers/ai/focus/", views.customer_ai_focus, name="customer_ai_focus"),
    path("customers/<int:pk>/ai-insight/", views.customer_ai_insight, name="customer_ai_insight"),

    # PRODUCTS
    path("products/", views.products_list, name="products_list"),
    path("products/add/", views.product_add, name="product_add"),
    path("products/<int:pk>/", views.product_detail, name="product_detail"),
    path("products/<int:pk>/edit/", views.product_edit, name="product_edit"),
    path("products/<int:pk>/ai/", views.product_ai_detail, name="product_ai_detail"),
    path("products/ai/suggest/", views.product_ai_suggest, name="product_ai_suggest"),

    # FABRICS
    path("fabrics/", views.fabrics_list, name="fabrics_list"),
    path("fabrics/add/", views.fabric_add, name="fabric_add"),
    path("fabrics/<int:pk>/", views.fabric_detail, name="fabric_detail"),
    path("fabrics/<int:pk>/edit/", views.fabric_edit, name="fabric_edit"),
    path("fabrics/<int:pk>/ai/", views.fabric_ai_detail, name="fabric_ai_detail"),
    path("fabrics/ai/suggest/", views.fabric_ai_suggest, name="fabric_ai_suggest"),
    path("fabrics/<int:pk>/ai-focus/", views.fabric_ai_focus, name="fabric_ai_focus"),

    # ACCESSORIES
    path("accessories/", views.accessories_list, name="accessories_list"),
    path("accessories/add/", views.accessory_add, name="accessory_add"),
    path("accessories/<int:pk>/", views.accessory_detail, name="accessory_detail"),
    path("accessories/<int:pk>/edit/", views.accessory_edit, name="accessory_edit"),
    path("accessories/ai/suggest/", views.accessory_ai_suggest, name="accessory_ai_suggest"),

    # TRIMS
    path("trims/", views.trims_list, name="trims_list"),
    path("trims/add/", views.trim_add, name="trim_add"),
    path("trims/<int:pk>/", views.trim_detail, name="trim_detail"),
    path("trims/<int:pk>/edit/", views.trim_edit, name="trim_edit"),
    path("trims/ai/suggest/", views.trim_ai_suggest, name="trim_ai_suggest"),

    # THREADS
    path("threads/", views.threads_list, name="threads_list"),
    path("threads/add/", views.thread_add, name="thread_add"),
    path("threads/<int:pk>/", views.thread_detail, name="thread_detail"),
    path("threads/<int:pk>/edit/", views.thread_edit, name="thread_edit"),

    # INVENTORY
    path("inventory/", views.inventory_list, name="inventory_list"),
    path("inventory/add/", views.inventory_add, name="inventory_add"),
    path("inventory/<int:pk>/", views.inventory_detail, name="inventory_detail"),
    path("inventory/<int:pk>/edit/", views.inventory_edit, name="inventory_edit"),
    path("inventory/<int:pk>/delete/", views.inventory_delete, name="inventory_delete"),
    path("inventory/<int:pk>/pdf/", views.inventory_detail_pdf, name="inventory_detail_pdf"),
    path("inventory/<int:pk>/quick-reorder/", views.inventory_quick_reorder, name="inventory_quick_reorder"),
    path("inventory/ai-overview/", views.inventory_ai_overview, name="inventory_ai_overview"),

    # WORLD TOOLS
    path("world-dashboard/", views.world_dashboard, name="world_dashboard"),
    path("world-tools/", views.world_tools, name="world_tools"),
    path("world-tools/ai-fashion/", views.world_ai_fashion_news, name="world_ai_fashion_news"),

    # CALENDAR
    path("calendar/", views.calendar_list, name="calendar_list"),
    path("calendar/add/", views.calendar_add, name="calendar_add"),
    path("calendar/<int:pk>/edit/", views.calendar_edit, name="calendar_edit"),
    path("calendar/event/<int:pk>/", views.calendar_event_detail, name="calendar_event_detail"),
    path("calendar/event/<int:pk>/ai/", views.calendar_event_ai, name="calendar_event_ai"),
    path("calendar/drag-update/", views.calendar_drag_update, name="calendar_drag_update"),
    path("calendar/toggle-done/<int:pk>/", views.calendar_toggle_done, name="calendar_toggle_done"),

    # PRODUCTION
    path("production/", views.production_list, name="production_list"),
    path("production/add/", views.production_add, name="production_add"),
    path("production/<int:pk>/", views.production_detail, name="production_detail"),
    path("production/<int:pk>/edit/", views.production_edit, name="production_edit"),
    path("production/<int:pk>/next-stage/", views.production_next_stage, name="production_next_stage"),
    path("production/<int:pk>/ai-help/", views.production_ai_help, name="production_ai_help"),
    path("production/<int:pk>/dpr/", views.production_dpr, name="production_dpr"),
    path("production/stage/<int:stage_id>/click/", views.production_stage_click, name="production_stage_click"),
    path("production/stage/<int:stage_id>/edit/", views.production_stage_edit, name="production_stage_edit"),
    path("production/from-opportunity/<int:pk>/", views.production_from_opportunity, name="production_from_opportunity"),
    path("production/<int:pk>/attachment/add/", views.production_attachment_add, name="production_attachment_add"),
    path("production/<int:pk>/attachment/<int:att_pk>/delete/", views.production_attachment_delete, name="production_attachment_delete"),

    # SHIPPING
    path("shipments/", views.shipment_list, name="shipment_list"),
    path("shipments/add/", views.shipment_add, name="shipment_add"),
    path("shipments/add/order/<int:pk>/", views.shipping_add_for_order, name="shipping_add_for_order"),
    path("shipments/add/opportunity/<int:pk>/", views.shipping_add_for_opportunity, name="shipping_add_for_opportunity"),
    path("shipments/<int:pk>/", views.shipment_detail, name="shipment_detail"),
    path("shipments/<int:pk>/edit/", views.shipment_edit, name="shipment_edit"),
    path("shipments/<int:pk>/refresh-tracking/", views.shipment_refresh_tracking, name="shipment_refresh_tracking"),
    path("shipments/<int:pk>/notify/", views.shipment_notify_customer, name="shipment_notify_customer"),

    # ACCOUNTING
    path("accounting/", acc.accounting_home, name="accounting_home"),
    path("accounting/ca-master/", acc.accounting_ca_master, name="accounting_ca_master"),
    path("accounting/ca/grid/", acc.accounting_ca_grid, name="accounting_ca_grid"),
    path("accounting/entries/", acc.accounting_entry_list, name="accounting_entry_list"),
    path("accounting/entries/add/", acc.accounting_entry_add, name="accounting_entry_add"),
    path("accounting/entries/add/ca/", acc.accounting_entry_add_ca, name="accounting_entry_add_ca"),
    path("accounting/entries/add/bd/", acc.accounting_entry_add_bd, name="accounting_entry_add_bd"),
    path("accounting/entries/<int:pk>/edit/", acc.accounting_entry_edit, name="accounting_entry_edit"),
    path("accounting/entries/<int:pk>/delete/", acc.accounting_entry_delete, name="accounting_entry_delete"),
    path("accounting/bd-dashboard/", acc.accounting_bd_dashboard, name="accounting_bd_dashboard"),
    path("accounting/bd-grid/", acc.accounting_bd_grid, name="accounting_bd_grid"),
    path("accounting/bd-daily/", acc.accounting_bd_daily, name="accounting_bd_daily"),
    path("accounting/production-profit/", acc.production_profit_report, name="production_profit_report"),
    path("accounting/export/csv/", acc.accounting_list_export_csv, name="accounting_list_export_csv"),
    path("accounting/export/xlsx/", acc.accounting_list_export_xlsx, name="accounting_list_export_xlsx"),
    path("accounting/bd-grid/export/csv/", acc.accounting_bd_grid_export_csv, name="accounting_bd_grid_export_csv"),
    path("accounting/bd-grid/export/xlsx/", acc.accounting_bd_grid_export_xlsx, name="accounting_bd_grid_export_xlsx"),
    path("accounting/month/close/", acc.accounting_close_month, name="accounting_close_month"),
    path("accounting/month/open/", acc.accounting_open_month, name="accounting_open_month"),
    path("accounting/files/", acc.accounting_files, name="accounting_files"),
    path("accounting/audit-trail/", acc.accounting_audit_trail, name="accounting_audit_trail"),
    path("accounting/ai-audit/", acc.accounting_ai_audit, name="accounting_ai_audit"),
    path("accounting/ai-suggest/", acc.accounting_ai_suggest, name="accounting_ai_suggest"),
    path("accounting/docs/upload/ca/", acc.accounting_doc_upload, name="accounting_docs_upload_ca"),
    path("accounting/docs/upload/bd/", acc.accounting_doc_upload, name="accounting_docs_upload_bd"),
    path("accounting/entries/<int:pk>/attach/", acc.accounting_entry_attach, name="accounting_entry_attach"),

    # BD STAFF and MONTHLY PAYROLL INPUT (use only these)
    path("bd-staff/", acc.bd_staff_list, name="bd_staff_list"),
    path("bd-staff/add/", acc.bd_staff_add, name="bd_staff_add"),
    path("bd-staff/<int:pk>/edit/", acc.bd_staff_edit, name="bd_staff_edit"),

    path("bd-staff/months/", acc.bd_staff_month_list, name="bd_staff_month_list"),
    path("bd-staff/months/generate/", acc.bd_staff_month_generate, name="bd_staff_month_generate"),
    path("bd-staff/months/<int:pk>/edit/", acc.bd_staff_month_edit, name="bd_staff_month_edit"),

    # Compatibility aliases (so old templates do not break)
    path("bd-payroll/months/", acc.bd_staff_month_list, name="bd_payroll_months"),
    path("bd-payroll/months/generate/", acc.bd_staff_month_generate, name="bd_payroll_generate"),
    path("bd-payroll/months/<int:pk>/edit/", acc.bd_staff_month_edit, name="bd_payroll_edit"),

    # AI SYSTEM
    path("ai/", ai.ai_hub, name="ai_hub"),
    path("ai/assistant/", ai.ai_assistant, name="ai_assistant"),
    path("ai/assistant/ask/", ai.ai_assistant_ask, name="ai_assistant_ask"),
    path("ai/health/", ai.ai_health_monitor, name="ai_health_monitor"),
    path("ai/status/", ai.ai_system_status, name="ai_system_status"),
    path("ai/opportunities/<int:pk>/suggest/", ai.ai_opportunity_suggest, name="ai_opportunity_suggest"),
    path("ai/production/<int:pk>/suggest/", ai.ai_production_suggest, name="ai_production_suggest"),

    # WhatsApp
    path("whatsapp/", wa.wa_inbox, name="wa_inbox"),
    path("whatsapp/<int:pk>/", wa.wa_thread, name="wa_thread"),
    path("whatsapp/<int:pk>/send/", wa.wa_send, name="wa_send"),
    path("whatsapp/<int:pk>/send-ai-draft/", wa.wa_send_ai_draft, name="wa_send_ai_draft"),
    path("whatsapp/webhook/", wa.wa_webhook, name="wa_webhook"),
    path("api/whatsapp/webhook/", whatsapp_webhook),

    # Email Sync
    path("email-sync/", views_email.email_sync_dashboard, name="email_sync_dashboard"),
    path("email-sync/run/", views_email.email_sync_run, name="email_sync_run"),
    path("invoices/", inv.invoice_list, name="invoice_list"),
    path("invoices/add/", inv.invoice_add, name="invoice_add"),
    path("invoices/<int:pk>/", inv.invoice_view, name="invoice_view"),
    path("invoices/<int:pk>/edit/", inv.invoice_edit, name="invoice_edit"),
]