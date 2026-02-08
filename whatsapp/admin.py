from django.contrib import admin

from whatsapp.models import (
    WhatsAppAccount,
    WhatsAppThread,
    WhatsAppMessage,
    WhatsAppAutomationRule,
    WhatsAppSendQueue,
    DoNotContactPhone,
    WhatsAppEventLog,
)


@admin.register(WhatsAppAccount)
class WhatsAppAccountAdmin(admin.ModelAdmin):
    list_display = ("phone_number", "status", "last_seen_at", "updated_at")
    search_fields = ("phone_number",)


@admin.register(WhatsAppThread)
class WhatsAppThreadAdmin(admin.ModelAdmin):
    list_display = ("contact_phone", "contact_name", "linked_lead", "last_message_at", "automation_enabled")
    search_fields = ("contact_phone", "contact_name")
    list_filter = ("automation_enabled", "is_archived")


@admin.register(WhatsAppMessage)
class WhatsAppMessageAdmin(admin.ModelAdmin):
    list_display = ("thread", "direction", "status", "sent_at", "received_at")
    search_fields = ("body",)
    list_filter = ("direction", "status")


@admin.register(WhatsAppAutomationRule)
class WhatsAppAutomationRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "trigger", "is_active", "max_per_contact_per_day")
    list_filter = ("trigger", "is_active")


@admin.register(WhatsAppSendQueue)
class WhatsAppSendQueueAdmin(admin.ModelAdmin):
    list_display = ("thread", "status", "scheduled_at", "attempts")
    list_filter = ("status",)


@admin.register(DoNotContactPhone)
class DoNotContactPhoneAdmin(admin.ModelAdmin):
    list_display = ("phone", "reason", "created_at")
    search_fields = ("phone",)


@admin.register(WhatsAppEventLog)
class WhatsAppEventLogAdmin(admin.ModelAdmin):
    list_display = ("event", "level", "account", "thread", "created_at")
    list_filter = ("event", "level")
