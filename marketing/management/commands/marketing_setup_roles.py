from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType

from marketing import models as marketing_models


class Command(BaseCommand):
    help = "Create Marketing Manager group with marketing permissions"

    def handle(self, *args, **options):
        group, _ = Group.objects.get_or_create(name="Marketing Manager")

        marketing_model_list = [
            marketing_models.SeoProperty,
            marketing_models.SeoQueryDaily,
            marketing_models.SeoPageDaily,
            marketing_models.SocialAccount,
            marketing_models.SocialContent,
            marketing_models.SocialMetricDaily,
            marketing_models.SocialAudienceDaily,
            marketing_models.AccountMetricDaily,
            marketing_models.AdAccount,
            marketing_models.AdCampaign,
            marketing_models.AdMetricDaily,
            marketing_models.Campaign,
            marketing_models.TrackedLink,
            marketing_models.Contact,
            marketing_models.ContactList,
            marketing_models.ContactListMembership,
            marketing_models.OutreachCampaign,
            marketing_models.OutreachMessageTemplate,
            marketing_models.OutreachSendLog,
            marketing_models.UnsubscribeEvent,
            marketing_models.CallTask,
            marketing_models.InsightItem,
            marketing_models.BestPracticeLibrary,
            marketing_models.OAuthCredential,
        ]

        cts = ContentType.objects.get_for_models(*marketing_model_list).values()
        perms = Permission.objects.filter(content_type__in=cts)
        group.permissions.set(perms)

        self.stdout.write(self.style.SUCCESS("Marketing Manager group updated."))
