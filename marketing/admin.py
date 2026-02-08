from django.contrib import admin

from .models import (
    SeoProperty,
    SeoQueryDaily,
    SeoPageDaily,
    SocialAccount,
    SocialContent,
    SocialMetricDaily,
    SocialAudienceDaily,
    AccountMetricDaily,
    AdAccount,
    AdCampaign,
    AdMetricDaily,
    Campaign,
    TrackedLink,
    Contact,
    ContactList,
    ContactListMembership,
    OutreachCampaign,
    OutreachMessageTemplate,
    OutreachSendLog,
    UnsubscribeEvent,
    CallTask,
    InsightItem,
    BestPracticeLibrary,
    OAuthCredential,
)


@admin.register(SeoProperty)
class SeoPropertyAdmin(admin.ModelAdmin):
    list_display = ("name", "gsc_site_url", "ga4_property_id", "is_active", "last_sync_at")
    search_fields = ("name", "gsc_site_url")


@admin.register(SeoQueryDaily)
class SeoQueryDailyAdmin(admin.ModelAdmin):
    list_display = ("property", "date", "query", "clicks", "impressions")
    list_filter = ("date",)


@admin.register(SeoPageDaily)
class SeoPageDailyAdmin(admin.ModelAdmin):
    list_display = ("property", "date", "page", "clicks", "impressions")
    list_filter = ("date",)


@admin.register(SocialAccount)
class SocialAccountAdmin(admin.ModelAdmin):
    list_display = ("platform", "display_name", "external_account_id", "is_active")
    list_filter = ("platform",)


@admin.register(SocialContent)
class SocialContentAdmin(admin.ModelAdmin):
    list_display = ("title", "platform", "published_at")
    search_fields = ("title", "external_content_id")


@admin.register(SocialMetricDaily)
class SocialMetricDailyAdmin(admin.ModelAdmin):
    list_display = ("content", "date", "views", "likes")
    list_filter = ("date",)


@admin.register(SocialAudienceDaily)
class SocialAudienceDailyAdmin(admin.ModelAdmin):
    list_display = ("account", "date")


@admin.register(AccountMetricDaily)
class AccountMetricDailyAdmin(admin.ModelAdmin):
    list_display = ("account", "date", "followers_total", "impressions", "reach", "views")
    list_filter = ("date",)


@admin.register(AdAccount)
class AdAccountAdmin(admin.ModelAdmin):
    list_display = ("platform_account", "external_ad_account_id", "currency", "is_active")
    list_filter = ("is_active",)


@admin.register(AdCampaign)
class AdCampaignAdmin(admin.ModelAdmin):
    list_display = ("name", "ad_account", "status", "objective")
    list_filter = ("status",)


@admin.register(AdMetricDaily)
class AdMetricDailyAdmin(admin.ModelAdmin):
    list_display = ("ad_campaign", "date", "spend", "impressions", "clicks")
    list_filter = ("date",)


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ("name", "goal", "start_date", "end_date", "is_active")
    search_fields = ("name",)


@admin.register(TrackedLink)
class TrackedLinkAdmin(admin.ModelAdmin):
    list_display = ("campaign", "name", "utm_campaign")


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ("email", "company", "consent_status", "do_not_contact")
    search_fields = ("email", "company")
    list_filter = ("consent_status", "do_not_contact")


@admin.register(ContactList)
class ContactListAdmin(admin.ModelAdmin):
    list_display = ("name", "created_by", "created_at")


@admin.register(ContactListMembership)
class ContactListMembershipAdmin(admin.ModelAdmin):
    list_display = ("contact_list", "contact", "created_at")


@admin.register(OutreachCampaign)
class OutreachCampaignAdmin(admin.ModelAdmin):
    list_display = ("name", "channel", "status", "daily_limit")
    list_filter = ("status", "channel")


@admin.register(OutreachMessageTemplate)
class OutreachMessageTemplateAdmin(admin.ModelAdmin):
    list_display = ("campaign", "subject_template")


@admin.register(OutreachSendLog)
class OutreachSendLogAdmin(admin.ModelAdmin):
    list_display = ("campaign", "contact", "send_type", "status", "sent_at")
    list_filter = ("status",)


@admin.register(UnsubscribeEvent)
class UnsubscribeEventAdmin(admin.ModelAdmin):
    list_display = ("contact", "channel", "event_at")


@admin.register(CallTask)
class CallTaskAdmin(admin.ModelAdmin):
    list_display = ("campaign", "contact", "status", "priority_score")
    list_filter = ("status",)


@admin.register(InsightItem)
class InsightItemAdmin(admin.ModelAdmin):
    list_display = ("source", "platform", "title", "priority_score", "status")
    list_filter = ("source", "status", "platform")


@admin.register(BestPracticeLibrary)
class BestPracticeLibraryAdmin(admin.ModelAdmin):
    list_display = ("title", "platform", "category", "created_at")
    list_filter = ("platform", "category")


@admin.register(OAuthCredential)
class OAuthCredentialAdmin(admin.ModelAdmin):
    list_display = ("platform", "social_account", "platform_account", "expires_at")
