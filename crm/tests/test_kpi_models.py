from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from crm.models import (
    KPIItemDefinition,
    KPIRoleTemplate,
    KPISettings,
    KPITemplateVersion,
)


class KPIModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("kpi-model-owner")

    def _version(self):
        template = KPIRoleTemplate.objects.create(
            code="project-manager",
            name="Project Manager",
            created_by=self.user,
        )
        return KPITemplateVersion.objects.create(
            template=template,
            version=1,
            created_by=self.user,
        )

    def _item(self, version, name, weight):
        return KPIItemDefinition.objects.create(
            template_version=version,
            name=name,
            description=f"{name} description",
            purpose=f"{name} purpose",
            measurement_method="Approved CRM records",
            target="Target",
            weight=weight,
            data_source="Iconic CRM",
        )

    def test_template_version_requires_exactly_one_hundred_percent_to_publish(self):
        version = self._version()
        self._item(version, "Timeline control", Decimal("60.00"))
        second = self._item(version, "Documentation", Decimal("39.99"))

        version.status = KPITemplateVersion.STATUS_PUBLISHED
        version.published_by = self.user
        with self.assertRaisesMessage(
            ValidationError,
            "Active KPI item weights must total exactly 100.00 percent.",
        ):
            version.save()

        second.weight = Decimal("40.00")
        second.save()
        version.save()
        version.refresh_from_db()

        self.assertEqual(version.status, KPITemplateVersion.STATUS_PUBLISHED)
        self.assertEqual(version.active_weight_total, Decimal("100.00"))
        self.assertIsNotNone(version.published_at)

    def test_published_template_items_and_version_are_immutable(self):
        version = self._version()
        first = self._item(version, "Project setup", Decimal("50.00"))
        self._item(version, "Client updates", Decimal("50.00"))
        version.status = KPITemplateVersion.STATUS_PUBLISHED
        version.published_by = self.user
        version.save()

        first.weight = Decimal("40.00")
        with self.assertRaisesMessage(
            ValidationError,
            "KPI items on published or retired versions are immutable.",
        ):
            first.save()

        version.notes = "Changed after publication"
        with self.assertRaisesMessage(
            ValidationError,
            "Published and retired template versions are immutable.",
        ):
            version.save()

        with self.assertRaisesMessage(
            ValidationError,
            "Published and retired template versions cannot be deleted.",
        ):
            version.delete()

    def test_item_ranges_must_cover_zero_through_one_hundred_without_gaps(self):
        version = self._version()
        item = KPIItemDefinition(
            template_version=version,
            name="Approval management",
            weight=Decimal("100.00"),
            red_max=Decimal("69.98"),
        )
        with self.assertRaisesMessage(
            ValidationError,
            "Yellow range must begin 0.01 above the Red range.",
        ):
            item.full_clean()

    def test_item_weight_database_constraint_rejects_values_over_one_hundred(self):
        version = self._version()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                KPIItemDefinition.objects.bulk_create(
                    [
                        KPIItemDefinition(
                            template_version=version,
                            name="Unsafe weight",
                            weight=Decimal("100.01"),
                        )
                    ]
                )

    def test_item_range_database_constraint_rejects_gaps(self):
        version = self._version()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                KPIItemDefinition.objects.bulk_create(
                    [
                        KPIItemDefinition(
                            template_version=version,
                            name="Unsafe ranges",
                            weight=Decimal("100.00"),
                            red_max=Decimal("69.98"),
                        )
                    ]
                )

    def test_settings_require_exact_bonus_weights_and_one_active_version(self):
        settings = KPISettings.objects.create(version=1, is_active=True)
        self.assertEqual(
            settings.individual_bonus_weight
            + settings.team_bonus_weight
            + settings.company_bonus_weight,
            Decimal("100.00"),
        )

        invalid = KPISettings(
            version=2,
            individual_bonus_weight=Decimal("60.00"),
            team_bonus_weight=Decimal("20.00"),
            company_bonus_weight=Decimal("15.00"),
        )
        with self.assertRaisesMessage(
            ValidationError,
            "must total exactly 100.00 percent",
        ):
            invalid.full_clean()

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                KPISettings.objects.bulk_create(
                    [
                        KPISettings(
                            version=2,
                            individual_bonus_weight=Decimal("60.00"),
                            team_bonus_weight=Decimal("20.00"),
                            company_bonus_weight=Decimal("15.00"),
                        )
                    ]
                )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                KPISettings.objects.bulk_create(
                    [KPISettings(version=3, is_active=True)]
                )
