from django.db import models
from django.conf import settings

class OutboundEmailLog(models.Model):
    lead = models.ForeignKey(
        "crm.Lead",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="outbound_emails",
    )

    to_email = models.CharField(max_length=255, blank=True, default="")
    subject = models.CharField(max_length=255, blank=True, default="")
    body = models.TextField(blank=True, default="")

    message_type = models.CharField(max_length=40, blank=True, default="")  # thank_you, meeting_confirm, etc
    sent_ok = models.BooleanField(default=False)
    error = models.CharField(max_length=300, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["message_type"]),
            models.Index(fields=["sent_ok"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.message_type} to {self.to_email} ok={self.sent_ok}"