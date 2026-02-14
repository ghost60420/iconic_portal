# crm/models_whatsapp.py
import re

from django.db import models
from django.conf import settings


class WhatsAppThread(models.Model):
    lead = models.ForeignKey(
        "crm.Lead",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="wa_threads",
    )

    wa_phone = models.CharField(max_length=30, db_index=True)
    wa_name = models.CharField(max_length=120, blank=True, default="")

    last_message_at = models.DateTimeField(null=True, blank=True)

    # Auto reply control
    last_auto_reply_at = models.DateTimeField(null=True, blank=True)

    # AI + handoff
    needs_human = models.BooleanField(default=False)
    ai_enabled = models.BooleanField(default=True)
    ai_draft = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-last_message_at", "-id")
        indexes = [
            models.Index(fields=["wa_phone"]),
            models.Index(fields=["last_message_at"]),
            models.Index(fields=["needs_human", "ai_enabled"]),
        ]

    def __str__(self):
        name = self.wa_name or "Unknown"
        return f"WA {self.wa_phone} ({name})"

    @property
    def display_phone(self) -> str:
        digits = re.sub(r"\D", "", self.wa_phone or "")
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        if len(digits) == 10:
            return f"({digits[0:3]}) {digits[3:6]}-{digits[6:]}"
        return digits or (self.wa_phone or "")


class WhatsAppMessage(models.Model):
    thread = models.ForeignKey(
        WhatsAppThread,
        on_delete=models.CASCADE,
        related_name="messages",
    )

    direction = models.CharField(max_length=10, choices=(("in", "in"), ("out", "out")))
    body = models.TextField(blank=True, default="")
    meta_id = models.CharField(max_length=120, blank=True, default="", db_index=True)
    media_url = models.TextField(blank=True, default="")
    media_type = models.CharField(max_length=50, blank=True, default="")
    media_path = models.TextField(blank=True, default="")
    media_filename = models.CharField(max_length=180, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        ordering = ("id",)
        constraints = [
            models.UniqueConstraint(fields=["thread", "meta_id"], name="uniq_wa_thread_meta_id"),
        ]

    def __str__(self):
        return f"{self.direction} {self.thread.wa_phone}"
