from django.test import TestCase

from crm.models import Lead, Opportunity


class OpportunityStatusTests(TestCase):
    def setUp(self):
        self.lead = Lead.objects.create(account_brand="Test Brand")

    def test_opportunity_status_label(self):
        opp_open = Opportunity.objects.create(
            lead=self.lead,
            stage="Prospecting",
            is_open=True,
        )
        opp_won = Opportunity.objects.create(
            lead=self.lead,
            stage="Closed Won",
            is_open=False,
        )
        opp_lost = Opportunity.objects.create(
            lead=self.lead,
            stage="Closed Lost",
            is_open=False,
        )

        self.assertEqual(opp_open.status_label, "Open")
        self.assertEqual(opp_won.status_label, "Closed Won")
        self.assertEqual(opp_lost.status_label, "Closed Lost")
