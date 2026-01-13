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
from .permissions import bd_blocked, require_access


def home_redirect(request):
    if request.user.is_authenticated:
        return redirect("main_dashboard")
    return redirect("login")


def perm(flag_name, view_func):
    # login first, then check the checkmark flag
    return login_required(require_access(flag_name)(view_func))


def ca_only(view_func):
    # login first, then block BD, then also require CA accounting checkmark
    return login_required(require_access("can_accounting_ca")(bd_blocked(view_func)))


urlpatterns = [
    # Home and Auth
    path("", home_redirect, name="home"),
    path("accounts/", include("django.contrib.auth.urls")),

    # Main dashboard
    path("main-dashboard/", login_required(views.main_dashboard), name="main_dashboard"),

    # LEADS
    path("leads/", perm("can_leads", views.leads_list), name="leads_list"),
    path("leads/add/", perm("can_leads", views.add_lead), name="add_lead"),
    path("leads/<int:pk>/", perm("can_leads", views.lead_detail), name="lead_detail"),
    path("leads/<int:pk>/edit/", perm("can_leads", views.edit_lead), name="edit_lead"),
    path("leads/<int:pk>/convert/", perm("can_leads", views.convert_lead_to_opportunity), name="convert_lead_to_opportunity"),

    path("leads/ai/overview/", perm("can_ai", views.leads_ai_overview), name="leads_ai_overview"),
    path("leads/<int:pk>/ai/", perm("can_ai", views.lead_ai_detail), name="lead_ai_detail"),
    path("leads/<int:pk>/ai/suggest/", perm("can_ai", ai.ai_lead_suggest), name="ai_lead_suggest"),
    path("leads/<int:pk>/ai/thank-you/", perm("can_ai", ai.ai_lead_send_thankyou), name="ai_lead_send_thankyou"),
    path("leads/<int:pk>/ai/meeting-confirm/", perm("can_ai", ai.ai_lead_send_meeting_confirm), name="ai_lead_send_meeting_confirm"),

    # OPPORTUNITIES
    path("opportunities/", perm("can_opportunities", views.opportunities_list), name="opportunities_list"),
    path("opportunities/add/", perm("can_opportunities", views.add_opportunity), name="add_opportunity"),
    path("opportunities/<int:pk>/", perm("can_opportunities", views.opportunity_detail), name="opportunity_detail"),
    path("opportunities/<int:pk>/edit/", perm("can_opportunities", views.opportunity_edit), name="opportunity_edit"),
    path("opportunities/<int:pk>/ai/", perm("can_ai", views.opportunity_ai_detail), name="opportunity_ai_detail"),

    # CUSTOMERS
    path("customers/", perm("can_customers", views.customers_list), name="customers_list"),
    path("customers/<int:pk>/", perm("can_customers", views.customer_detail), name="customer_detail"),
    path("customers/<int:pk>/ai/", perm("can_ai", views.customer_ai_detail), name="customer_ai_detail"),
    path("customers/ai/overview/", perm("can_ai", views.customer_ai_overview), name="customer_ai_overview"),
    path("customers/ai/focus/", perm("can_ai", views.customer_ai_focus), name="customer_ai_focus"),
    path("customers/<int:pk>/ai-insight/", perm("can_ai", views.customer_ai_insight), name="customer_ai_insight"),

    # INVENTORY
    path("inventory/", perm("can_inventory", views.inventory_list), name="inventory_list"),
    path("inventory/add/", perm("can_inventory", views.inventory_add), name="inventory_add"),
    path("inventory/<int:pk>/", perm("can_inventory", views.inventory_detail), name="inventory_detail"),
    path("inventory/<int:pk>/edit/", perm("can_inventory", views.inventory_edit), name="inventory_edit"),
    path("inventory/<int:pk>/delete/", perm("can_inventory", views.inventory_delete), name="inventory_delete"),
    path("inventory/<int:pk>/pdf/", perm("can_inventory", views.inventory_detail_pdf), name="inventory_detail_pdf"),
    path("inventory/<int:pk>/quick-reorder/", perm("can_inventory", views.inventory_quick_reorder), name="inventory_quick_reorder"),
    path("inventory/ai-overview/", perm("can_ai", views.inventory_ai_overview), name="inventory_ai_overview"),

    # WORLD
    path("world-dashboard/", login_required(views.world_dashboard), name="world_dashboard"),
    path("world-tools/", login_required(views.world_tools), name="world_tools"),
    path("world-tools/ai-fashion/", perm("can_ai", views.world_ai_fashion_news), name="world_ai_fashion_news"),

    # CALENDAR
    path("calendar/", perm("can_calendar", views.calendar_list), name="calendar_list"),
    path("calendar/add/", perm("can_calendar", views.calendar_add), name="calendar_add"),
    path("calendar/<int:pk>/edit/", perm("can_calendar", views.calendar_edit), name="calendar_edit"),
    path("calendar/event/<int:pk>/", perm("can_calendar", views.calendar_event_detail), name="calendar_event_detail"),
    path("calendar/event/<int:pk>/ai/", perm("can_ai", views.calendar_event_ai), name="calendar_event_ai"),
    path("calendar/drag-update/", perm("can_calendar", views.calendar_drag_update), name="calendar_drag_update"),
    path("calendar/toggle-done/<int:pk>/", perm("can_calendar", views.calendar_toggle_done), name="calendar_toggle_done"),

    # PRODUCTION
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

    # SHIPPING
    path("shipments/", perm("can_shipping", views.shipment_list), name="shipment_list"),
    path("shipments/add/", perm("can_shipping", views.shipment_add), name="shipment_add"),
    path("shipments/add/order/<int:pk>/", perm("can_shipping", views.shipping_add_for_order), name="shipping_add_for_order"),
    path("shipments/add/opportunity/<int:pk>/", perm("can_shipping", views.shipping_add_for_opportunity), name="shipping_add_for_opportunity"),
    path("shipments/<int:pk>/", perm("can_shipping", views.shipment_detail), name="shipment_detail"),
    path("shipments/<int:pk>/edit/", perm("can_shipping", views.shipment_edit), name="shipment_edit"),
    path("shipments/<int:pk>/refresh-tracking/", perm("can_shipping", views.shipment_refresh_tracking), name="shipment_refresh_tracking"),
    path("shipments/<int:pk>/notify/", perm("can_shipping", views.shipment_notify_customer), name="shipment_notify_customer"),

    # ACCOUNTING HOME
    path("accounting/", login_required(acc.accounting_home), name="accounting_home"),

    # CA ACCOUNTING (CA only)
    path("accounting/ca-master/", ca_only(acc.accounting_ca_master), name="accounting_ca_master"),
    path("accounting/ca/grid/", ca_only(acc.accounting_ca_grid), name="accounting_ca_grid"),
    path("accounting/entries/", ca_only(acc.accounting_entry_list), name="accounting_entry_list"),
    path("accounting/entries/add/ca/", ca_only(acc.accounting_entry_add_ca), name="accounting_entry_add_ca"),
    path("accounting/docs/upload/ca/", ca_only(acc.accounting_doc_upload), name="accounting_docs_upload_ca"),

    # BD ACCOUNTING (BD flag)
    path("accounting/entries/add/bd/", perm("can_accounting_bd", acc.accounting_entry_add_bd), name="accounting_entry_add_bd"),
    path("accounting/bd-dashboard/", perm("can_accounting_bd", acc.accounting_bd_dashboard), name="accounting_bd_dashboard"),
    path("accounting/bd-grid/", perm("can_accounting_bd", acc.accounting_bd_grid), name="accounting_bd_grid"),
    path("accounting/bd-daily/", perm("can_accounting_bd", acc.accounting_bd_daily), name="accounting_bd_daily"),
    path("accounting/docs/upload/bd/", perm("can_accounting_bd", acc.accounting_doc_upload), name="accounting_docs_upload_bd"),

    # Shared accounting tools (keep login only)
    path("accounting/entries/add/", login_required(acc.accounting_entry_add), name="accounting_entry_add"),
    path("accounting/entries/<int:pk>/edit/", login_required(acc.accounting_entry_edit), name="accounting_entry_edit"),
    path("accounting/entries/<int:pk>/delete/", login_required(acc.accounting_entry_delete), name="accounting_entry_delete"),
    path("accounting/entries/<int:pk>/attach/", login_required(acc.accounting_entry_attach), name="accounting_entry_attach"),
    path("accounting/production-profit/", login_required(acc.production_profit_report), name="production_profit_report"),
    path("accounting/export/csv/", login_required(acc.accounting_list_export_csv), name="accounting_list_export_csv"),
    path("accounting/export/xlsx/", login_required(acc.accounting_list_export_xlsx), name="accounting_list_export_xlsx"),
    path("accounting/bd-grid/export/csv/", login_required(acc.accounting_bd_grid_export_csv), name="accounting_bd_grid_export_csv"),
    path("accounting/bd-grid/export/xlsx/", login_required(acc.accounting_bd_grid_export_xlsx), name="accounting_bd_grid_export_xlsx"),
    path("accounting/month/close/", login_required(acc.accounting_close_month), name="accounting_close_month"),
    path("accounting/month/open/", login_required(acc.accounting_open_month), name="accounting_open_month"),
    path("accounting/files/", login_required(acc.accounting_files), name="accounting_files"),
    path("accounting/audit-trail/", login_required(acc.accounting_audit_trail), name="accounting_audit_trail"),
    path("accounting/ai-audit/", login_required(acc.accounting_ai_audit), name="accounting_ai_audit"),
    path("accounting/ai-suggest/", login_required(acc.accounting_ai_suggest), name="accounting_ai_suggest"),

    # BD STAFF
    path("bd-staff/", login_required(acc.bd_staff_list), name="bd_staff_list"),
    path("bd-staff/add/", login_required(acc.bd_staff_add), name="bd_staff_add"),
    path("bd-staff/<int:pk>/edit/", login_required(acc.bd_staff_edit), name="bd_staff_edit"),
    path("bd-staff/months/", login_required(acc.bd_staff_month_list), name="bd_staff_month_list"),
    path("bd-staff/months/generate/", login_required(acc.bd_staff_month_generate), name="bd_staff_month_generate"),
    path("bd-staff/months/<int:pk>/edit/", login_required(acc.bd_staff_month_edit), name="bd_staff_month_edit"),

    # AI SYSTEM
    path("ai/", perm("can_ai", ai.ai_hub), name="ai_hub"),
    path("ai/assistant/", perm("can_ai", ai.ai_assistant), name="ai_assistant"),
    path("ai/assistant/ask/", perm("can_ai", ai.ai_assistant_ask), name="ai_assistant_ask"),
    path("ai/health/", perm("can_ai", ai.ai_health_monitor), name="ai_health_monitor"),
    path("ai/status/", perm("can_ai", ai.ai_system_status), name="ai_system_status"),
    path("ai/opportunities/<int:pk>/suggest/", perm("can_ai", ai.ai_opportunity_suggest), name="ai_opportunity_suggest"),
    path("ai/production/<int:pk>/suggest/", perm("can_ai", ai.ai_production_suggest), name="ai_production_suggest"),

    # WhatsApp UI
    path("whatsapp/", login_required(wa.wa_inbox), name="wa_inbox"),
    path("whatsapp/<int:pk>/", login_required(wa.wa_thread), name="wa_thread"),
    path("whatsapp/<int:pk>/send/", login_required(wa.wa_send), name="wa_send"),
    path("whatsapp/<int:pk>/send-ai-draft/", login_required(wa.wa_send_ai_draft), name="wa_send_ai_draft"),

    # WhatsApp Webhook (no login)
    path("whatsapp/webhook/", whatsapp_webhook, name="wa_webhook"),
    path("api/whatsapp/webhook/", whatsapp_webhook, name="api_wa_webhook"),

    # Email Sync
    path("email-sync/", login_required(views_email.email_sync_dashboard), name="email_sync_dashboard"),
    path("email-sync/run/", login_required(views_email.email_sync_run), name="email_sync_run"),

    # Invoices
    path("invoices/", login_required(inv.invoice_list), name="invoice_list"),
    path("invoices/add/", login_required(inv.invoice_add), name="invoice_add"),
    path("invoices/<int:pk>/", login_required(inv.invoice_view), name="invoice_view"),
    path("invoices/<int:pk>/edit/", login_required(inv.invoice_edit), name="invoice_edit"),

    # Access checkmark pages (admin only is inside the views)
    # Access pages
    path("access/", login_required(access.access_list), name="access_list"),
    path("access/<int:user_id>/", login_required(access.access_edit), name="access_edit"),
]