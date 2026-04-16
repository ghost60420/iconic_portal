from datetime import date, datetime
import inspect
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

import crm.ai.lead_brain as lead_brain_module
from crm.ai.lead_brain import build_iconic_ai_brain


class WriteTrap(SimpleNamespace):
    def save(self, *args, **kwargs):
        raise AssertionError("Iconic AI Brain helper must not save objects")


class Activity(SimpleNamespace):
    def get_activity_type_display(self):
        return "Follow up sent"


class IconicAIBrainTests(SimpleTestCase):
    def test_builds_read_only_panel_sections_from_existing_data(self):
        lead = WriteTrap(
            account_brand="Acme Apparel",
            contact_name="Sam Buyer",
            email="sam@example.com",
            phone="",
            website="",
            company_website="",
            product_interest="Hoodie",
            product_category="",
            order_quantity="500",
            budget="",
            lead_status="New",
            priority="High",
            lead_type="outbound",
            brand_fit_score=72,
            qualification_status="Outreach Ready",
            recommended_channel="Email",
            recommended_next_action="Send a focused follow-up asking for tech pack and target date.",
            last_outreach_date=date(2026, 4, 1),
            last_reply_date=None,
            next_follow_up_date=date(2026, 4, 20),
            next_followup=None,
            qualification_reason="",
            disqualification_reason="",
        )
        activities = [
            Activity(
                activity_type="follow_up_sent",
                channel="Email",
                outcome="No reply yet",
                created_at=datetime(2026, 4, 1, 9, 30),
            )
        ]
        insights = [
            SimpleNamespace(summary_text="Existing insight: strong hoodie fit with missing budget.")
        ]

        result = build_iconic_ai_brain(
            lead=lead,
            opportunities=[SimpleNamespace()],
            comments=[SimpleNamespace()],
            tasks=[SimpleNamespace()],
            activities=activities,
            insights=insights,
            today=date(2026, 4, 16),
        )

        self.assertEqual(
            set(result.keys()),
            {
                "lead_summary",
                "missing_info",
                "suggested_next_step",
                "risk_flags",
                "recent_outreach_facts",
                "latest_existing_insight",
            },
        )
        self.assertIn("Brand: Acme Apparel", result["lead_summary"])
        self.assertIn("Phone", result["missing_info"])
        self.assertIn("Budget", result["missing_info"])
        self.assertEqual(
            result["suggested_next_step"],
            "Send a focused follow-up asking for tech pack and target date.",
        )
        self.assertIn("Outreach is recorded but no reply is recorded yet.", result["risk_flags"])
        self.assertIn("Existing insight: strong hoodie fit", result["latest_existing_insight"])

    def test_helper_does_not_save_send_or_reference_external_ai(self):
        lead = WriteTrap(
            account_brand="No Write Brand",
            contact_name="",
            email="",
            phone="",
            product_interest="",
            product_category="",
            order_quantity="",
            budget="",
            lead_status="New",
            priority="Medium",
            brand_fit_score=0,
            next_follow_up_date=None,
            next_followup=None,
        )

        with patch("django.core.mail.send_mail") as send_mail:
            result = build_iconic_ai_brain(
                lead=lead,
                opportunities=[],
                comments=[],
                tasks=[],
                activities=[],
                insights=[],
                today=date(2026, 4, 16),
            )

        send_mail.assert_not_called()
        self.assertEqual(result["suggested_next_step"], "Add a valid email or phone before planning outreach.")

        source = inspect.getsource(lead_brain_module)
        self.assertNotIn("OpenAI", source)
        self.assertNotIn("ask_openai", source)
        self.assertNotIn("send_mail", source)
        self.assertNotIn(".save(", source)
        self.assertNotIn("objects.create", source)
