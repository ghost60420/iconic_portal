from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from crm.models import ProductionOrder
from crm.services.operations_search import search_operations_records


class ProductionPurchaseOrderDisplayTests(TestCase):
    INTERNAL_ORDER_ID = "PO260705511257ABCDEF"
    PURCHASE_ORDER_NUMBER = "PO-511257"

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username="production-po-display-admin",
            email="production-po-display@example.com",
            password="test-pass",
        )
        cls.order = ProductionOrder.objects.create(
            title="Purchase order display regression",
            order_code=cls.INTERNAL_ORDER_ID,
            qty_total=120,
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_canonical_purchase_order_number_preserves_internal_identifier(self):
        self.assertEqual(self.order.purchase_order_number, self.PURCHASE_ORDER_NUMBER)
        self.assertEqual(self.order.short_order_code, self.PURCHASE_ORDER_NUMBER)
        self.assertEqual(self.order.internal_order_id, self.INTERNAL_ORDER_ID)
        self.assertEqual(
            str(self.order),
            f"{self.PURCHASE_ORDER_NUMBER} - Purchase order display regression",
        )

    def test_existing_human_friendly_purchase_order_number_is_preserved(self):
        self.assertEqual(
            ProductionOrder.format_purchase_order_number("PO-SEARCH-001"),
            "PO-SEARCH-001",
        )

    def test_purchase_order_formatting_performs_no_database_queries(self):
        with CaptureQueriesContext(connection) as queries:
            displayed = self.order.purchase_order_number

        self.assertEqual(displayed, self.PURCHASE_ORDER_NUMBER)
        self.assertEqual(len(queries), 0)

    def test_production_pages_use_the_same_visible_purchase_order_number(self):
        list_response = self.client.get(reverse("production_list"), {"status": "all"})
        detail_response = self.client.get(reverse("production_detail", args=[self.order.pk]))
        edit_response = self.client.get(reverse("production_edit", args=[self.order.pk]))

        for response in (list_response, detail_response, edit_response):
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, self.PURCHASE_ORDER_NUMBER)

        self.assertNotContains(list_response, self.INTERNAL_ORDER_ID)
        self.assertNotContains(edit_response, self.INTERNAL_ORDER_ID)
        self.assertContains(detail_response, "Internal Order ID")
        self.assertContains(detail_response, self.INTERNAL_ORDER_ID)

    def test_production_list_search_accepts_purchase_order_number_and_internal_id(self):
        for query in (self.PURCHASE_ORDER_NUMBER, self.INTERNAL_ORDER_ID):
            response = self.client.get(
                reverse("production_list"),
                {"status": "all", "q": query},
            )

            self.assertEqual(response.status_code, 200)
            self.assertContains(response, self.PURCHASE_ORDER_NUMBER)
            self.assertContains(response, reverse("production_detail", args=[self.order.pk]))

    def test_global_search_accepts_both_identifiers_and_returns_friendly_number(self):
        for query in (self.PURCHASE_ORDER_NUMBER, self.INTERNAL_ORDER_ID):
            groups = search_operations_records(self.user, query)
            production_rows = [
                row
                for label, rows in groups
                if label == "Production"
                for row in rows
            ]

            self.assertEqual(len(production_rows), 1)
            self.assertEqual(production_rows[0]["number"], self.PURCHASE_ORDER_NUMBER)

    def test_production_pdf_names_use_the_friendly_purchase_order_number(self):
        drawn_text = []

        class RecordingCanvas:
            def __init__(self, *_args, **_kwargs):
                pass

            def drawString(self, _x, _y, value):
                drawn_text.append(str(value))

            def __getattr__(self, _name):
                return lambda *_args, **_kwargs: None

        with patch("reportlab.pdfgen.canvas.Canvas", RecordingCanvas):
            order_sheet = self.client.get(
                reverse("production_order_sheet_pdf", args=[self.order.pk])
            )
        packing_list = self.client.get(
            reverse("production_packing_list_pdf", args=[self.order.pk])
        )

        self.assertEqual(order_sheet.status_code, 200)
        self.assertEqual(packing_list.status_code, 200)
        self.assertIn(
            f"production_order_sheet_{self.PURCHASE_ORDER_NUMBER}.pdf",
            order_sheet["Content-Disposition"],
        )
        self.assertIn(
            f"packing_list_{self.PURCHASE_ORDER_NUMBER}.pdf",
            packing_list["Content-Disposition"],
        )
        self.assertNotIn(self.INTERNAL_ORDER_ID, order_sheet["Content-Disposition"])
        self.assertNotIn(self.INTERNAL_ORDER_ID, packing_list["Content-Disposition"])
        self.assertIn(
            f"Purchase Order Number: {self.PURCHASE_ORDER_NUMBER}",
            drawn_text,
        )
        self.assertNotIn(self.INTERNAL_ORDER_ID, drawn_text)
