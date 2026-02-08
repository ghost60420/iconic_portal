from django.urls import path

from whatsapp import views

urlpatterns = [
    path("", views.inbox, name="wa_inbox"),
    path("thread/<int:pk>/", views.thread_view, name="wa_thread"),
    path("start/", views.start_chat, name="wa_start"),
    path("thread/<int:pk>/send/", views.enqueue_message, name="wa_send"),
    path("thread/<int:pk>/followup/", views.schedule_followup, name="wa_followup"),
    path("thread/<int:pk>/toggle-automation/", views.toggle_automation, name="wa_toggle_automation"),
    path("thread/<int:pk>/create-lead/", views.create_lead, name="wa_create_lead"),
    path("automation/", views.automation_view, name="wa_automation"),
    path("settings/", views.settings_view, name="wa_settings"),
    path("settings/logout/", views.logout_view, name="wa_logout"),
    path("settings/refresh/", views.refresh_qr, name="wa_refresh"),
    path("qr/", views.qr_image, name="wa_qr"),
    path("status/", views.status_json, name="wa_status"),
    path("webhook/", views.webhook, name="wa_webhook"),
]
