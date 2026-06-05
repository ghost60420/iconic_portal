from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crm.models import Event


@override_settings(
    CALENDAR_INVITES_ASYNC=False,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="calendar@example.com",
)
class CalendarInviteReminderTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.creator = user_model.objects.create_user(
            username="creator",
            password="pass1234",
            email="creator@example.com",
            first_name="Calendar",
            last_name="Owner",
        )
        self.attendee = user_model.objects.create_user(
            username="attendee",
            password="pass1234",
            email="attendee@example.com",
            first_name="Internal",
            last_name="User",
        )
        self.unrelated = user_model.objects.create_user(
            username="unrelated",
            password="pass1234",
            email="unrelated@example.com",
        )

    def _event_payload(self, **overrides):
        start = overrides.pop("start_datetime", timezone.localtime(timezone.now() + timedelta(hours=2)))
        end = overrides.pop("end_datetime", start + timedelta(minutes=30))
        payload = {
            "title": "Fit Review Meeting",
            "start_datetime": start.strftime("%Y-%m-%dT%H:%M"),
            "end_datetime": end.strftime("%Y-%m-%dT%H:%M"),
            "event_type": "call",
            "priority": "medium",
            "status": "planned",
            "location": "Showroom",
            "meeting_link": "https://meet.example.com/fit-review",
            "assigned_to_name": "",
            "assigned_to_email": "",
            "attendees": [str(self.attendee.pk)],
            "external_attendees": "buyer@example.com",
            "reminder_minutes_before": "60",
            "note": "Review hoodie sample specs.",
        }
        payload.update(overrides)
        return payload

    def test_create_meeting_links_internal_attendee_and_sends_invite(self):
        self.client.force_login(self.creator)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("calendar_add"), self._event_payload())

        self.assertEqual(response.status_code, 302)
        event = Event.objects.get(title="Fit Review Meeting")
        self.assertEqual(event.created_by, self.creator)
        self.assertEqual(list(event.attendees.all()), [self.attendee])
        self.assertEqual(event.external_attendees, "buyer@example.com")

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Meeting invite: Fit Review Meeting", mail.outbox[0].subject)
        self.assertCountEqual(mail.outbox[0].to, ["attendee@example.com", "buyer@example.com"])

    def test_internal_attendee_and_creator_can_see_meeting_but_unrelated_user_cannot(self):
        event = Event.objects.create(
            title="Private Production Sync",
            start_datetime=timezone.now() + timedelta(hours=3),
            created_by=self.creator,
        )
        event.attendees.add(self.attendee)

        self.client.force_login(self.creator)
        response = self.client.get(reverse("calendar_list"))
        self.assertContains(response, "Private Production Sync")

        self.client.force_login(self.attendee)
        response = self.client.get(reverse("calendar_list"))
        self.assertContains(response, "Private Production Sync")

        self.client.force_login(self.unrelated)
        response = self.client.get(reverse("calendar_list"))
        self.assertNotContains(response, "Private Production Sync")

    def test_update_email_sends_only_when_important_fields_change(self):
        start = timezone.localtime(timezone.now() + timedelta(hours=4)).replace(second=0, microsecond=0)
        event = Event.objects.create(
            title="Sample Approval",
            start_datetime=start,
            end_datetime=start + timedelta(hours=1),
            created_by=self.creator,
            external_attendees="buyer@example.com",
        )
        event.attendees.add(self.attendee)
        self.client.force_login(self.creator)
        mail.outbox = []

        unchanged_payload = self._event_payload(
            title="Sample Approval",
            start_datetime=timezone.localtime(event.start_datetime),
            end_datetime=timezone.localtime(event.end_datetime),
            location="",
            meeting_link="",
            external_attendees="buyer@example.com",
            reminder_minutes_before="",
            note="",
        )
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("calendar_edit", args=[event.pk]), unchanged_payload)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 0)

        changed_start = timezone.localtime(event.start_datetime + timedelta(hours=1))
        changed_payload = self._event_payload(
            title="Sample Approval",
            start_datetime=changed_start,
            end_datetime=changed_start + timedelta(hours=1),
            location="",
            meeting_link="",
            external_attendees="buyer@example.com",
            reminder_minutes_before="",
            note="",
        )
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("calendar_edit", args=[event.pk]), changed_payload)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Updated meeting: Sample Approval", mail.outbox[0].subject)

    def test_upcoming_reminder_can_be_dismissed_by_user(self):
        event = Event.objects.create(
            title="Reminder Popup Meeting",
            start_datetime=timezone.now() + timedelta(minutes=45),
            created_by=self.creator,
        )
        event.attendees.add(self.attendee)
        self.client.force_login(self.attendee)

        response = self.client.get(reverse("calendar_list"))
        reminder_ids = [item.pk for item in response.context["upcoming_reminders"]]
        self.assertIn(event.pk, reminder_ids)

        response = self.client.post(reverse("calendar_dismiss_reminder", args=[event.pk]))
        self.assertEqual(response.status_code, 200)

        response = self.client.get(reverse("calendar_list"))
        reminder_ids = [item.pk for item in response.context["upcoming_reminders"]]
        self.assertNotIn(event.pk, reminder_ids)
