import json

from django.core.management.base import BaseCommand, CommandError

from marketing.services.errors import MarketingServiceError
from marketing.services.google_oauth import (
    ANALYTICS_READONLY_SCOPE,
    credential_has_analytics_readonly_scope,
    get_google_credential,
    get_valid_access_token,
    list_ga4_admin_inventory,
    log_ga4_admin_inventory,
    upsert_ga4_properties,
)


class Command(BaseCommand):
    help = "Dump Google Analytics Admin API accounts and GA4 properties for the connected Google account."

    def add_arguments(self, parser):
        parser.add_argument(
            "--raw",
            action="store_true",
            help="Print raw Analytics Admin API responses. Tokens are not included.",
        )
        parser.add_argument(
            "--no-save",
            action="store_true",
            help="Do not save the discovered GA4 property to SeoProperty.",
        )

    def handle(self, *args, **options):
        credential = get_google_credential(fallback_platform="ga4")
        if not credential:
            raise CommandError("No active Google OAuthCredential found.")

        scope_ok = credential_has_analytics_readonly_scope(credential)
        self.stdout.write(f"OAuthCredential ID: {credential.pk}")
        self.stdout.write(f"Google account: {credential.account_name or credential.account_id or '(not set)'}")
        self.stdout.write(f"Required scope: {ANALYTICS_READONLY_SCOPE}")
        self.stdout.write(f"Required scope present: {'YES' if scope_ok else 'NO'}")
        if credential.scopes:
            self.stdout.write(f"Saved scopes: {credential.scopes}")

        access_token = get_valid_access_token(credential)
        try:
            inventory = list_ga4_admin_inventory(access_token, include_raw=options["raw"])
        except MarketingServiceError as exc:
            self.stdout.write(self.style.ERROR("Analytics Admin API status: FAILED"))
            message = str(exc)
            lowered = message.lower()
            if "service_disabled" in lowered or "has not been used" in lowered or "disabled" in lowered:
                self.stdout.write(self.style.ERROR("Analytics Admin API enabled: NO or not enabled for this OAuth project."))
            else:
                self.stdout.write(self.style.WARNING("Analytics Admin API enabled: could not confirm from this error."))
            raise CommandError(message)

        log_ga4_admin_inventory(credential=credential, inventory=inventory)
        self.stdout.write(self.style.SUCCESS("Analytics Admin API status: OK"))
        self.stdout.write("Analytics Admin API enabled: request succeeded")

        accounts = inventory.get("accounts", [])
        properties = inventory.get("properties", [])
        errors = inventory.get("errors", [])

        self.stdout.write(f"Accounts discovered: {len(accounts)}")
        for account in accounts:
            self.stdout.write(
                "  "
                f"account_id={account.get('account_id') or '(missing)'} "
                f"resource={account.get('account_resource') or '(missing)'} "
                f"name={account.get('display_name') or '(missing)'}"
            )

        self.stdout.write(f"Properties discovered: {len(properties)}")
        for prop in properties:
            self.stdout.write(
                "  "
                f"property_id={prop.get('property_id') or '(missing)'} "
                f"resource={prop.get('property_resource') or '(missing)'} "
                f"account_id={prop.get('account_id') or '(missing)'} "
                f"name={prop.get('display_name') or '(missing)'}"
            )

        if len(properties) == 1 and not options["no_save"]:
            saved = upsert_ga4_properties(properties)
            saved_id = saved[0].pk if saved else "(not saved)"
            self.stdout.write(
                self.style.SUCCESS(
                    f"Auto selected GA4 property ID {properties[0]['property_id']} and saved it to SeoProperty id={saved_id}."
                )
            )
        elif len(properties) > 1:
            self.stdout.write("Multiple GA4 properties found. No auto-select was made.")
        elif not properties:
            self.stdout.write(self.style.WARNING("No GA4 properties were returned for this Google account."))

        if errors:
            self.stdout.write(self.style.WARNING("Partial discovery errors:"))
            for error in errors:
                self.stdout.write(f"  {error}")

        if options["raw"]:
            self.stdout.write("Raw Analytics Admin API response:")
            self.stdout.write(json.dumps(inventory.get("raw", {}), indent=2, sort_keys=True))
