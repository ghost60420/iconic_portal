import socket
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from crm.models import Customer, Shipment
from crm.tasks import send_shipment_status_notification


class ShipmentAsyncNotificationTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            username="shipping-admin",
            email="shipping-admin@example.com",
            password="test-pass",
        )
        self.customer = Customer.objects.create(
            account_brand="Shipping Test Brand",
            contact_name="Shipping Buyer",
            email="shipping-buyer@example.com",
            country="Canada",
        )
        self.shipment = Shipment.objects.create(
            customer=self.customer,
            carrier="dhl",
            tracking_number="DHL123",
            status="planned",
            cost_bdt=Decimal("0.00"),
        )

    def test_status_update_saves_when_notification_queue_fails(self):
        self.client.force_login(self.user)

        with patch("crm.views.send_shipment_status_notification.apply_async", side_effect=RuntimeError("broker down")):
            response = self.client.post(
                reverse("shipment_detail", args=[self.shipment.pk]),
                {"action": "update_status", "status": "shipped", "notify_customer": "1"},
            )

        self.assertEqual(response.status_code, 302)
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, "shipped")

    def test_status_update_without_notify_checkbox_does_not_queue_email(self):
        self.client.force_login(self.user)

        with patch("crm.views.send_shipment_status_notification.apply_async") as apply_async:
            response = self.client.post(
                reverse("shipment_detail", args=[self.shipment.pk]),
                {"action": "update_status", "status": "shipped"},
            )

        self.assertEqual(response.status_code, 302)
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, "shipped")
        apply_async.assert_not_called()

    @override_settings(
        EMAIL_HOST_USER="smtp-user",
        EMAIL_HOST_PASSWORD="smtp-password",
        DEFAULT_FROM_EMAIL="shipping@example.com",
        SHIPMENT_EMAIL_TIMEOUT=1,
    )
    def test_smtp_timeout_raises_for_celery_retry_without_mutating_shipment(self):
        self.shipment.status = "shipped"
        self.shipment.save(update_fields=["status"])

        with patch("crm.services.shipment_notifications.EmailMessage.send", side_effect=socket.timeout("timed out")):
            with self.assertRaises(socket.timeout):
                send_shipment_status_notification.run(self.shipment.pk, "shipped")

        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, "shipped")
        self.assertEqual(self.shipment.last_notified_status, "")

    @override_settings(
        EMAIL_HOST_USER="smtp-user",
        EMAIL_HOST_PASSWORD="smtp-password",
        DEFAULT_FROM_EMAIL="shipping@example.com",
        SHIPMENT_EMAIL_TIMEOUT=1,
    )
    def test_failed_email_logs_and_does_not_mark_notified(self):
        self.shipment.status = "shipped"
        self.shipment.save(update_fields=["status"])

        with patch("crm.services.shipment_notifications.EmailMessage.send", return_value=0):
            result = send_shipment_status_notification.run(self.shipment.pk, "shipped")

        self.shipment.refresh_from_db()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.shipment.last_notified_status, "")
