from datetime import date, datetime
import inspect
from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse

import crm.ai.lead_brain as lead_brain_module
from crm.ai.lead_brain import build_iconic_ai_brain
from crm.views_iconic_ai_brain import iconic_ai_brain_refresh


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
        self.assertEqual(
            result["suggested_next_step"],
            "Send a focused follow-up asking for tech pack and target date.",
        )
        self.assertIn("Outreach is recorded but no reply is recorded yet.", result["risk_flags"])
        self.assertIn("Existing insight: strong hoodie fit", result["latest_existing_insight"])
        self.assertNotIn("Budget", result["missing_info"])

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


class _RelationList:
    def __init__(self, items):
        self.items = list(items)

    def all(self):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def __iter__(self):
        return iter(self.items)

    def __getitem__(self, value):
        return self.items[value]

    def __len__(self):
        return len(self.items)


class IconicAIBrainRefreshViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.lead = SimpleNamespace(
            pk=7,
            account_brand="Refresh Brand",
            contact_name="Refresh Contact",
            opportunities=_RelationList([]),
            tasks=_RelationList([]),
            activities=_RelationList([]),
            ai_insights=_RelationList([]),
        )

    def test_refresh_url_pattern(self):
        self.assertEqual(reverse("lead_iconic_ai_brain_refresh", args=[7]), "/leads/7/iconic-ai-brain/")

    def test_refresh_renders_partial(self):
        payload = {
            "lead_summary": ["Brand: Refresh Brand"],
            "missing_info": ["Website"],
            "suggested_next_step": "Review recent outreach, then set a clear next follow-up date.",
            "risk_flags": ["No major risk flags detected from current CRM data."],
            "recent_outreach_facts": ["Last outreach: Not recorded"],
            "latest_existing_insight": "No existing AI insight is saved for this lead.",
        }
        request = self.factory.get("/leads/7/iconic-ai-brain/", HTTP_X_REQUESTED_WITH="XMLHttpRequest")

        with patch("crm.views_iconic_ai_brain.get_object_or_404", return_value=self.lead), \
             patch("crm.views_iconic_ai_brain._chatter_for_lead", return_value=[]), \
             patch("crm.views_iconic_ai_brain.build_iconic_ai_brain", return_value=payload) as build_brain:
            response = iconic_ai_brain_refresh(request, self.lead.pk)

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="iconic-ai-brain-panel"', response.content.decode())
        self.assertIn("Generate Again", response.content.decode())
        build_brain.assert_called_once()

    def test_refresh_failure_returns_server_error(self):
        request = self.factory.get("/leads/7/iconic-ai-brain/", HTTP_X_REQUESTED_WITH="XMLHttpRequest")

        with patch("crm.views_iconic_ai_brain.get_object_or_404", return_value=self.lead), \
             patch("crm.views_iconic_ai_brain._chatter_for_lead", return_value=[]), \
             patch("crm.views_iconic_ai_brain.build_iconic_ai_brain", side_effect=RuntimeError("boom")), \
             patch("crm.views_iconic_ai_brain.logger.exception"):
            response = iconic_ai_brain_refresh(request, self.lead.pk)

        self.assertEqual(response.status_code, 500)
        self.assertIn("Iconic AI Brain refresh failed.", response.content.decode())
