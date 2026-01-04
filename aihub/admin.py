from django.contrib import admin
from .models import AIAgent, AIConversation, AIMessage


@admin.register(AIAgent)
class AIAgentAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "category", "role")
    search_fields = ("name", "code", "category", "role")


@admin.register(AIConversation)
class AIConversationAdmin(admin.ModelAdmin):
    list_display = ("id", "agent", "user", "lead", "opportunity", "created_at")
    list_filter = ("agent", "created_at")
    search_fields = ("lead__account_brand", "opportunity__opportunity_id")


@admin.register(AIMessage)
class AIMessageAdmin(admin.ModelAdmin):
    list_display = ("conversation", "sender", "created_at")
    list_filter = ("sender", "created_at")
    search_fields = ("content",)
