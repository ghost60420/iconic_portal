# crm/urls.py

from django.urls import path, include
from django.shortcuts import redirect
from django.contrib.auth.decorators import login_required

from . import views_access as access
from . import views
from . import views_ai as ai
from . import views_invoice as inv
from . import views_email
from . import views_whatsapp as wa
from . import views_accounting as acc
from .whatsapp_webhook import whatsapp_webhook

from .permissions import bd_blocked


def home_redirect(request):
    # Root domain behavior
    if request.user.is_authenticated:
        return redirect("main_dashboard")
    return redirect("login")


def bd_blocked_view(view_func):
    # login first, then block BD_TEAM
    return login_required(bd_blocked(view_func))


urlpatterns = [
    # Home
    path("", home_redirect, name="home"),

    # Auth (login url name is "login")
    path("accounts/", include("django.contrib.auth.urls")),

    # Main dashboard
    path("main-dashboard/", login_required(views.main_dashboard), name="main_dashboard"),

    # LEADS
    path("leads/", login_required(views.leads_list), name="leads_list"),
    path("leads/add/", login_required(views.add_lead), name="add_lead"),
    path("leads/<int:pk>/", login_required(views.lead_detail), name="lead_detail"),
    path("leads/<int:pk>/edit/", login_required(views.edit_lead), name="edit_lead"),
    path("leads/<int:pk>/convert/", login_required(views.convert_lead_to_opportunity), name="convert_lead_to_opportunity"),

    # Old AI
    path("leads/ai/overview/", login_required(views.leads_ai_overview), name="leads_ai_overview"),
    path("leads/<int:pk>/ai/", login_required(views.lead_ai_detail), name="lead_ai_detail"),

    # New AI
    path("leads/<int:pk>/ai/suggest/", login_required(ai.ai_lead_suggest), name="ai_lead_suggest"),
    path("leads/<int:pk>/ai/thank-you/", login_required(ai.ai_lead_send_thankyou), name="ai_lead_send_thankyou"),
    path("leads/<int:pk>/ai/meeting-confirm/", login_required(ai.ai_lead_send_meeting_confirm), name="ai_lead_send_meeting_confirm"),

    # OPPORTUNITIES
    path("opportunities/", login_required(views.opportunities_list), name="opportunities_list"),
    path("opportunities/add/", login_required(views.add_opportunity), name="add_opportunity"),
    path("opportunities/<int:pk>/", login_required(views.opportunity_detail), name="opportunity_detail"),
    path("opportunities/<int:pk>/edit/", login_required(views.opportunity_edit), name="opportunity_edit"),
    path("opportunities/<int:pk>/ai/", login_required(views.opportunity_ai_detail), name="opportunity_ai_detail"),

    # CUSTOMERS
    path("customers/", login_required(views.customers_list), name="customers_list"),
    path("customers/<int:pk>/", login_required(views.customer_detail), name="customer_detail"),
    path("customers/<int:pk>/ai/", login_required(views.customer_ai_detail), name="customer_ai_detail"),
    path("customers/ai/overview/", login_required(views.customer_ai_overview), name="customer_ai_overview"),
    path("customers/ai/focus/", login_required(views.customer_ai_focus), name="customer_ai_focus"),
    path("customers/<int:pk>/ai-insight/", login_required(views.customer_ai_insight), name="customer_ai_insight"),

    # INVENTORY
    path("inventory/", login_required(views.inventory_list), name="inventory_list"),
    path("inventory/add/", login_required(views.inventory_add), name="inventory_add"),
    path("inventory/<int:pk>/", login_required(views.inventory_detail), name="inventory_detail"),
    path("inventory/<int:pk>/edit/", login_required(views.inventory_edit), name="inventory_edit"),
    path("inventory/<int:pk>/delete/", login_required(views.inventory_delete), name="inventory_delete"),
    path("inventory/<int:pk>/pdf/", login_required(views.inventory_detail_pdf), name="inventory_detail_pdf"),
    path("inventory/<int:pk>/quick-reorder/", login_required(views.inventory_quick_reorder), name="inventory_quick_reorder"),
    path("inventory/ai-overview/", login_required(views.inventory_ai_overview), name="inventory_ai_overview"),

    # WORLD
    path("world-dashboard/", login_required(views.world_dashboard), name="world_dashboard"),
    path("world-tools/", login_required(views.world_tools), name="world_tools"),
    path("world-tools/ai-fashion/", login_required(views.world_ai_fashion_news), name="world_ai_fashion_news"),

    # CALENDAR
    path("calendar/", login_required(views.calendar_list), name="calendar_list"),
    path("calendar/add/", login_required(views.calendar_add), name="calendar_add"),
    path("calendar/<int:pk>/edit/", login_required(views.calendar_edit), name="calendar_edit"),
    path("calendar/event/<int:pk>/", login_required(views.calendar_event_detail), name="calendar_event_detail"),
    path("calendar/event/<int:pk>/ai/", login_required(views.calendar_event_ai), name="calendar_event_ai"),
    path("calendar/drag-update/", login_required(views.calendar_drag_update), name="calendar_drag_update"),
    path("calendar/toggle-done/<int:pk>/", login_required(views.calendar_toggle_done), name="calendar_toggle_done"),

    # PRODUCTION
    path("production/", login_required(views.production_list), name="production_list"),
    path("production/add/", login_required(views.production_add), name="production_add"),
    path("production/<int:pk>/", login_required(views.production_detail), name="production_detail"),
    path("production/<int:pk>/edit/", login_required(views.production_edit), name="production_edit"),
    path("production/<int:pk>/next-stage/", login_required(views.production_next_stage), name="production_next_stage"),
    path("production/<int:pk>/ai-help/", login_required(views.production_ai_help), name="production_ai_help"),
    path("production/<int:pk>/dpr/", login_required(views.production_dpr), name="production_dpr"),
    path("production/stage/<int:stage_id>/click/", login_required(views.production_stage_click), name="production_stage_click"),
    path("production/stage/<int:stage_id>/edit/", login_required(views.production_stage_edit), name="production_stage_edit"),
    path("production/from-opportunity/<int:pk>/", login_required(views.production_from_opportunity), name="production_from_opportunity"),
    path("production/<int:pk>/attachment/add/", login_required(views.production_attachment_add), name="production_attachment_add"),
    path("production/<int:pk>/attachment/<int:att_pk>/delete/", login_required(views.production_attachment_delete), name="production_attachment_delete"),

    # SHIPPING
    path("shipments/", login_required(views.shipment_list), name="shipment_list"),
    path("shipments/add/", login_required(views.shipment_add), name="shipment_add"),
    path("shipments/add/order/<int:pk>/", login_required(views.shipping_add_for_order), name="shipping_add_for_order"),
    path("shipments/add/opportunity/<int:pk>/", login_required(views.shipping_add_for_opportunity), name="shipping_add_for_opportunity"),
    path("shipments/<int:pk>/", login_required(views.shipment_detail), name="shipment_detail"),
    path("shipments/<int:pk>/edit/", login_required(views.shipment_edit), name="shipment_edit"),
    path("shipments/<int:pk>/refresh-tracking/", login_required(views.shipment_refresh_tracking), name="shipment_refresh_tracking"),
    path("shipments/<int:pk>/notify/", login_required(views.shipment_notify_customer), name="shipment_notify_customer"),

    # ACCOUNTING HOME (allowed for all logged in users)
    path("accounting/", login_required(acc.accounting_home), name="accounting_home"),

    # CA ACCOUNTING (block BD)
    path("accounting/ca-master/", bd_blocked_view(acc.accounting_ca_master), name="accounting_ca_master"),
    path("accounting/ca/grid/", bd_blocked_view(acc.accounting_ca_grid), name="accounting_ca_grid"),
    path("accounting/entries/", bd_blocked_view(acc.accounting_entry_list), name="accounting_entry_list"),
    path("accounting/entries/add/ca/", bd_blocked_view(acc.accounting_entry_add_ca), name="accounting_entry_add_ca"),
    path("accounting/docs/upload/ca/", bd_blocked_view(acc.accounting_doc_upload), name="accounting_docs_upload_ca"),

    # BD ACCOUNTING (allowed)
    path("accounting/entries/add/bd/", login_required(acc.accounting_entry_add_bd), name="accounting_entry_add_bd"),
    path("accounting/bd-dashboard/", login_required(acc.accounting_bd_dashboard), name="accounting_bd_dashboard"),
    path("accounting/bd-grid/", login_required(acc.accounting_bd_grid), name="accounting_bd_grid"),
    path("accounting/bd-daily/", login_required(acc.accounting_bd_daily), name="accounting_bd_daily"),
    path("accounting/docs/upload/bd/", login_required(acc.accounting_doc_upload), name="accounting_docs_upload_bd"),

    # Shared accounting tools
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
    path("ai/", login_required(ai.ai_hub), name="ai_hub"),
    path("ai/assistant/", login_required(ai.ai_assistant), name="ai_assistant"),
    path("ai/assistant/ask/", login_required(ai.ai_assistant_ask), name="ai_assistant_ask"),
    path("ai/health/", login_required(ai.ai_health_monitor), name="ai_health_monitor"),
    path("ai/status/", login_required(ai.ai_system_status), name="ai_system_status"),
    path("ai/opportunities/<int:pk>/suggest/", login_required(ai.ai_opportunity_suggest), name="ai_opportunity_suggest"),
    path("ai/production/<int:pk>/suggest/", login_required(ai.ai_production_suggest), name="ai_production_suggest"),

    # WhatsApp UI
    path("whatsapp/", login_required(wa.wa_inbox), name="wa_inbox"),
    path("whatsapp/<int:pk>/", login_required(wa.wa_thread), name="wa_thread"),
    path("whatsapp/<int:pk>/send/", login_required(wa.wa_send), name="wa_send"),
    path("whatsapp/<int:pk>/send-ai-draft/", login_required(wa.wa_send_ai_draft), name="wa_send_ai_draft"),

    # WhatsApp Webhook
    path("api/whatsapp/webhook/", whatsapp_webhook, name="whatsapp_webhook"),

    # Email Sync
    path("email-sync/", login_required(views_email.email_sync_dashboard), name="email_sync_dashboard"),
    path("email-sync/run/", login_required(views_email.email_sync_run), name="email_sync_run"),

    # Invoices
    path("invoices/", login_required(inv.invoice_list), name="invoice_list"),
    path("invoices/add/", login_required(inv.invoice_add), name="invoice_add"),
    path("invoices/<int:pk>/", login_required(inv.invoice_view), name="invoice_view"),
    path("invoices/<int:pk>/edit/", login_required(inv.invoice_edit), name="invoice_edit"),

    # Access pages
    path("access/", login_required(access.access_list), name="access_list"),
    path("access/<int:user_id>/", login_required(access.access_edit), name="access_edit"),
]