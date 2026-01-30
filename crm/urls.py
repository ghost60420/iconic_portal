# crm/urls.py

from django.urls import path, include
from django.shortcuts import redirect
from django.contrib.auth.decorators import login_required

from . import views
from . import views_ai as ai
from . import views_invoice as inv
from . import views_email
from . import views_whatsapp as wa
from . import views_accounting as acc
from . import views_access as access

from .whatsapp_webhook import whatsapp_webhook
from .permissions import bd_blocked, require_access, require_any_access


def home_redirect(request):
    if request.user.is_authenticated:
        return redirect("main_dashboard")
    return redirect("login")


def perm(flag_name, view_func):
    return login_required(require_access(flag_name)(view_func))


def ca_only(view_func):
    return login_required(require_access("can_accounting_ca")(bd_blocked(view_func)))


def acc_any(view_func):
    return login_required(require_any_access("can_accounting_ca", "can_accounting_bd")(view_func))


urlpatterns = [
    path("", home_redirect, name="home"),
    path("accounts/", include("django.contrib.auth.urls")),

    path("main-dashboard/", login_required(views.main_dashboard), name="main_dashboard"),

    path("leads/", perm("can_leads", views.leads_list), name="leads_list"),
    path("leads/add/", perm("can_leads", views.add_lead), name="lead_add"),
    path("leads/<int:pk>/", perm("can_leads", views.lead_detail), name="lead_detail"),
    path("leads/<int:pk>/edit/", perm("can_leads", views.edit_lead), name="lead_edit"),
    path("leads/<int:pk>/convert/", perm("can_leads", views.convert_lead_to_opportunity), name="convert_lead_to_opportunity"),

    path("leads/ai/overview/", perm("can_ai", views.leads_ai_overview), name="leads_ai_overview"),
    path("leads/<int:pk>/ai/", perm("can_ai", views.lead_ai_detail), name="lead_ai_detail"),
    path("leads/<int:pk>/ai/suggest/", perm("can_ai", ai.ai_lead_suggest), name="ai_lead_suggest"),
    path("leads/<int:pk>/ai/thank-you/", perm("can_ai", ai.ai_lead_send_thankyou), name="ai_lead_send_thankyou"),
    path("leads/<int:pk>/ai/meeting-confirm/", perm("can_ai", ai.ai_lead_send_meeting_confirm), name="ai_lead_send_meeting_confirm"),

    path("opportunities/", perm("can_opportunities", views.opportunities_list), name="opportunities_list"),
    path("opportunities/add/", perm("can_opportunities", views.add_opportunity), name="add_opportunity"),
    path("opportunities/<int:pk>/", perm("can_opportunities", views.opportunity_detail), name="opportunity_detail"),
    path("opportunities/<int:pk>/edit/", perm("can_opportunities", views.opportunity_edit), name="opportunity_edit"),
    path("opportunities/<int:pk>/ai/", perm("can_ai", views.opportunity_ai_detail), name="opportunity_ai_detail"),

    path("customers/", perm("can_customers", views.customers_list), name="customers_list"),
    path("customers/<int:pk>/", perm("can_customers", views.customer_detail), name="customer_detail"),
    path("customers/<int:pk>/ai/", perm("can_ai", views.customer_ai_detail), name="customer_ai_detail"),
    path("customers/ai/overview/", perm("can_ai", views.customer_ai_overview), name="customer_ai_overview"),
    path("customers/ai/focus/", perm("can_ai", views.customer_ai_focus), name="customer_ai_focus"),
    path("customers/<int:pk>/ai-insight/", perm("can_ai", views.customer_ai_insight), name="customer_ai_insight"),

    path("inventory/", perm("can_inventory", views.inventory_list), name="inventory_list"),
    path("inventory/add/", perm("can_inventory", views.inventory_add), name="inventory_add"),
    path("inventory/<int:pk>/", perm("can_inventory", views.inventory_detail), name="inventory_detail"),
    path("inventory/<int:pk>/edit/", perm("can_inventory", views.inventory_edit), name="inventory_edit"),
    path("inventory/<int:pk>/delete/", perm("can_inventory", views.inventory_delete), name="inventory_delete"),
    path("inventory/<int:pk>/pdf/", perm("can_inventory", views.inventory_detail_pdf), name="inventory_detail_pdf"),
    path("inventory/<int:pk>/quick-reorder/", perm("can_inventory", views.inventory_quick_reorder), name="inventory_quick_reorder"),
    path("inventory/ai-overview/", perm("can_ai", views.inventory_ai_overview), name="inventory_ai_overview"),

    # Library
    path("library/", login_required(views.library_home), name="library_home"),

    # Products
    path("library/products/", login_required(views.products_list), name="products_list"),
    path("library/products/add/", login_required(views.product_add), name="product_add"),
    path("library/products/<int:pk>/", login_required(views.product_detail), name="product_detail"),
    path("library/products/<int:pk>/edit/", login_required(views.product_edit), name="product_edit"),
    path("library/products/<int:pk>/ai/", login_required(views.product_ai_detail), name="product_ai_detail"),
    path("library/products/ai-suggest/", login_required(views.product_ai_suggest), name="product_ai_suggest"),

    # Fabrics
    path("library/fabrics/", login_required(views.fabrics_list), name="fabrics_list"),
    path("library/fabrics/add/", login_required(views.fabric_add), name="fabric_add"),
    path("library/fabrics/<int:pk>/", login_required(views.fabric_detail), name="fabric_detail"),
    path("library/fabrics/<int:pk>/edit/", login_required(views.fabric_edit), name="fabric_edit"),
    path("library/fabrics/<int:pk>/ai/", login_required(views.fabric_ai_detail), name="fabric_ai_detail"),
    path("library/fabrics/ai-suggest/", login_required(views.fabric_ai_suggest), name="fabric_ai_suggest"),
    path("library/fabrics/ai-focus/<int:pk>/", login_required(views.fabric_ai_focus), name="fabric_ai_focus"),

    # Accessories
    path("library/accessories/", login_required(views.accessories_list), name="accessories_list"),
    path("library/accessories/add/", login_required(views.accessory_add), name="accessory_add"),
    path("library/accessories/<int:pk>/", login_required(views.accessory_detail), name="accessory_detail"),
    path("library/accessories/<int:pk>/edit/", login_required(views.accessory_edit), name="accessory_edit"),
    path("library/accessories/ai-suggest/", login_required(views.accessory_ai_suggest), name="accessory_ai_suggest"),

    # Trims
    path("library/trims/", login_required(views.trims_list), name="trims_list"),
    path("library/trims/add/", login_required(views.trim_add), name="trim_add"),
    path("library/trims/<int:pk>/", login_required(views.trim_detail), name="trim_detail"),
    path("library/trims/<int:pk>/edit/", login_required(views.trim_edit), name="trim_edit"),
    path("library/trims/ai-suggest/", login_required(views.trim_ai_suggest), name="trim_ai_suggest"),

    # Threads
    path("library/threads/", login_required(views.threads_list), name="threads_list"),
    path("library/threads/add/", login_required(views.thread_add), name="thread_add"),
    path("library/threads/<int:pk>/", login_required(views.thread_detail), name="thread_detail"),
    path("library/threads/<int:pk>/edit/", login_required(views.thread_edit), name="thread_edit"),

    path("world-dashboard/", login_required(views.world_dashboard), name="world_dashboard"),
    path("world-tools/", login_required(views.world_tools), name="world_tools"),
    path("world-tools/ai-fashion/", perm("can_ai", views.world_ai_fashion_news), name="world_ai_fashion_news"),

    path("calendar/", perm("can_calendar", views.calendar_list), name="calendar_list"),
    path("calendar/add/", perm("can_calendar", views.calendar_add), name="calendar_add"),
    path("calendar/<int:pk>/edit/", perm("can_calendar", views.calendar_edit), name="calendar_edit"),
    path("calendar/event/<int:pk>/", perm("can_calendar", views.calendar_event_detail), name="calendar_event_detail"),
    path("calendar/event/<int:pk>/ai/", perm("can_ai", views.calendar_event_ai), name="calendar_event_ai"),
    path("calendar/drag-update/", perm("can_calendar", views.calendar_drag_update), name="calendar_drag_update"),
    path("calendar/toggle-done/<int:pk>/", perm("can_calendar", views.calendar_toggle_done), name="calendar_toggle_done"),

    path("production/", perm("can_production", views.production_list), name="production_list"),
    path("production/add/", perm("can_production", views.production_add), name="production_add"),
    path("production/<int:pk>/", perm("can_production", views.production_detail), name="production_detail"),
    path("production/<int:pk>/edit/", perm("can_production", views.production_edit), name="production_edit"),
    path("production/<int:pk>/next-stage/", perm("can_production", views.production_next_stage), name="production_next_stage"),
    path("production/<int:pk>/ai-help/", perm("can_ai", views.production_ai_help), name="production_ai_help"),
    path("production/<int:pk>/dpr/", perm("can_production", views.production_dpr), name="production_dpr"),
    path("production/stage/<int:stage_id>/click/", perm("can_production", views.production_stage_click), name="production_stage_click"),
    path("production/stage/<int:stage_id>/edit/", perm("can_production", views.production_stage_edit), name="production_stage_edit"),
    path("production/from-opportunity/<int:pk>/", perm("can_production", views.production_from_opportunity), name="production_from_opportunity"),
    path("production/<int:pk>/attachment/add/", perm("can_production", views.production_attachment_add), name="production_attachment_add"),
    path("production/<int:pk>/attachment/<int:att_pk>/delete/", perm("can_production", views.production_attachment_delete), name="production_attachment_delete"),

    path("shipments/", perm("can_shipping", views.shipment_list), name="shipment_list"),
    path("shipments/add/", perm("can_shipping", views.shipment_add), name="shipment_add"),
    path("shipments/add/order/<int:pk>/", perm("can_shipping", views.shipping_add_for_order), name="shipping_add_for_order"),
    path("shipments/add/opportunity/<int:pk>/", perm("can_shipping", views.shipping_add_for_opportunity), name="shipping_add_for_opportunity"),
    path("shipments/<int:pk>/", perm("can_shipping", views.shipment_detail), name="shipment_detail"),
    path("shipments/<int:pk>/edit/", perm("can_shipping", views.shipment_edit), name="shipment_edit"),
    path("shipments/<int:pk>/refresh-tracking/", perm("can_shipping", views.shipment_refresh_tracking), name="shipment_refresh_tracking"),
    path("shipments/<int:pk>/notify/", perm("can_shipping", views.shipment_notify_customer), name="shipment_notify_customer"),

    # Accounting
    path("accounting/", acc_any(acc.accounting_home), name="accounting_home"),

    path("accounting/ca-master/", ca_only(acc.accounting_ca_master), name="accounting_ca_master"),

    path("accounting/entries/add/ca/", ca_only(acc.accounting_entry_add_ca), name="accounting_entry_add_ca"),
    path("accounting/docs/upload/ca/", ca_only(acc.accounting_doc_upload), name="accounting_docs_upload_ca"),

    path("accounting/entries/add/bd/", perm("can_accounting_bd", acc.accounting_entry_add_bd), name="accounting_entry_add_bd"),
    path("accounting/bd-dashboard/", perm("can_accounting_bd", acc.accounting_bd_dashboard), name="accounting_bd_dashboard"),
    path("accounting/bd-grid/", perm("can_accounting_bd", acc.accounting_bd_grid), name="accounting_bd_grid"),
    path("accounting/bd-daily/", perm("can_accounting_bd", acc.accounting_bd_daily), name="accounting_bd_daily"),
    path("accounting/docs/upload/bd/", perm("can_accounting_bd", acc.accounting_doc_upload), name="accounting_docs_upload_bd"),

    # Shared accounting pages (CA or BD)
    path("accounting/entries/", acc_any(acc.accounting_entry_list), name="accounting_entry_list"),
    path("accounting/entries/add/", acc_any(acc.accounting_entry_add), name="accounting_entry_add"),
    path("accounting/entries/<int:pk>/edit/", acc_any(acc.accounting_entry_edit), name="accounting_entry_edit"),
    path("accounting/entries/<int:pk>/delete/", acc_any(acc.accounting_entry_delete), name="accounting_entry_delete"),
    path("accounting/entries/<int:pk>/attach/", acc_any(acc.accounting_entry_attach), name="accounting_entry_attach"),
    path("accounting/production-profit/", acc_any(acc.production_profit_report), name="production_profit_report"),
    path("accounting/export/csv/", acc_any(acc.accounting_list_export_csv), name="accounting_list_export_csv"),
    path("accounting/export/xlsx/", acc_any(acc.accounting_list_export_xlsx), name="accounting_list_export_xlsx"),
    path("accounting/month/close/", acc_any(acc.accounting_close_month), name="accounting_close_month"),
    path("accounting/month/open/", acc_any(acc.accounting_open_month), name="accounting_open_month"),
    path("accounting/files/", acc_any(acc.accounting_files), name="accounting_files"),
    path("accounting/audit-trail/", acc_any(acc.accounting_audit_trail), name="accounting_audit_trail"),
    path("accounting/ai-audit/", acc_any(acc.accounting_ai_audit), name="accounting_ai_audit"),
    path("accounting/ai-suggest/", acc_any(acc.accounting_ai_suggest), name="accounting_ai_suggest"),

    # BD only exports
    path("accounting/bd-grid/export/csv/", perm("can_accounting_bd", acc.accounting_bd_grid_export_csv), name="accounting_bd_grid_export_csv"),
    path("accounting/bd-grid/export/xlsx/", perm("can_accounting_bd", acc.accounting_bd_grid_export_xlsx), name="accounting_bd_grid_export_xlsx"),

    # BD staff (BD only)
    path("bd-staff/", perm("can_accounting_bd", acc.bd_staff_list), name="bd_staff_list"),
    path("bd-staff/add/", perm("can_accounting_bd", acc.bd_staff_add), name="bd_staff_add"),
    path("bd-staff/<int:pk>/edit/", perm("can_accounting_bd", acc.bd_staff_edit), name="bd_staff_edit"),
    path("bd-staff/months/", perm("can_accounting_bd", acc.bd_staff_month_list), name="bd_staff_month_list"),
    path("bd-staff/months/generate/", perm("can_accounting_bd", acc.bd_staff_month_generate), name="bd_staff_month_generate"),
    path("bd-staff/months/<int:pk>/edit/", perm("can_accounting_bd", acc.bd_staff_month_edit), name="bd_staff_month_edit"),

    # WhatsApp webhook
    path("whatsapp/webhook/", whatsapp_webhook, name="wa_webhook"),
    path("api/whatsapp/webhook/", whatsapp_webhook, name="api_wa_webhook"),

    # WhatsApp inbox UI
    path("whatsapp/", perm("can_leads", wa.wa_inbox), name="wa_inbox"),
    path("whatsapp/<int:pk>/", perm("can_leads", wa.wa_thread), name="wa_thread"),
    path("whatsapp/<int:pk>/send/", perm("can_leads", wa.wa_send), name="wa_send"),
    path("whatsapp/<int:pk>/send-ai/", perm("can_leads", wa.wa_send_ai_draft), name="wa_send_ai_draft"),

    # Email sync
    path("email-sync/", login_required(views_email.email_sync_dashboard), name="email_sync_dashboard"),
    path("email-sync/run/", login_required(views_email.email_sync_run), name="email_sync_run"),

    # Invoices
    path("invoices/", login_required(inv.invoice_list), name="invoice_list"),
    path("invoices/add/", login_required(inv.invoice_add), name="invoice_add"),
    path("invoices/<int:pk>/", login_required(inv.invoice_view), name="invoice_view"),
    path("invoices/<int:pk>/edit/", login_required(inv.invoice_edit), name="invoice_edit"),

    # Access
    path("access/", login_required(access.access_list), name="access_list"),
    path("access/<int:user_id>/", login_required(access.access_edit), name="access_edit"),
]
