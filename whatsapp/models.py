from django.conf import settings
from django.db import models
from django.utils import timezone


class WhatsAppAccount(models.Model):
    STATUS_CHOICES = [
        ("disconnected", "Disconnected"),
        ("qr_required", "QR required"),
        ("connected", "Connected"),
        ("error", "Error"),
    ]

    phone_number = models.CharField(max_length=30, db_index=True)
    display_name = models.CharField(max_length=120, blank=True, default="")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="disconnected")
    last_seen_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("phone_number",)

    def __str__(self):
        return self.phone_number


class WhatsAppThread(models.Model):
    account = models.ForeignKey(WhatsAppAccount, on_delete=models.CASCADE, related_name="threads")
    wa_chat_id = models.CharField(max_length=120)
    contact_phone = models.CharField(max_length=30, db_index=True)
    contact_name = models.CharField(max_length=120, blank=True, default="")

    linked_lead = models.ForeignKey(
        "crm.Lead",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="wa_threads_web",
    )

    last_message_at = models.DateTimeField(null=True, blank=True)
    is_archived = models.BooleanField(default=False)
    automation_enabled = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-last_message_at", "-id")
        constraints = [
            models.UniqueConstraint(fields=["account", "wa_chat_id"], name="uniq_wa_web_thread"),
        ]
        indexes = [
            models.Index(fields=["contact_phone"]),
            models.Index(fields=["last_message_at"]),
        ]

    def __str__(self):
        return f"{self.contact_phone}"


class WhatsAppMessage(models.Model):
    DIRECTION_CHOICES = [
        ("inbound", "Inbound"),
        ("outbound", "Outbound"),
    ]
    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("sent", "Sent"),
        ("delivered", "Delivered"),
        ("read", "Read"),
        ("failed", "Failed"),
    ]

    thread = models.ForeignKey(WhatsAppThread, on_delete=models.CASCADE, related_name="messages")
    direction = models.CharField(max_length=20, choices=DIRECTION_CHOICES)
    wa_message_id = models.CharField(max_length=120, blank=True, default="")
    body = models.TextField(blank=True, default="")
    media_url = models.TextField(blank=True, default="")
    media_type = models.CharField(max_length=50, blank=True, default="")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued")
    error_text = models.TextField(blank=True, default="")

    sent_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="wa_web_messages",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("id",)
        constraints = [
            models.UniqueConstraint(fields=["thread", "wa_message_id"], name="uniq_wa_web_msg"),
        ]
        indexes = [
            models.Index(fields=["thread"]),
            models.Index(fields=["sent_at"]),
            models.Index(fields=["received_at"]),
        ]


class WhatsAppAutomationRule(models.Model):
    TRIGGER_CHOICES = [
        ("first_inbound", "First inbound"),
        ("after_hours", "After hours"),
        ("keyword_match", "Keyword match"),
        ("no_reply_followup", "No reply followup"),
    ]

    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)
    trigger = models.CharField(max_length=30, choices=TRIGGER_CHOICES)
    keyword_list_json = models.JSONField(blank=True, default=list)
    response_template = models.TextField(blank=True, default="")
    send_delay_seconds = models.PositiveIntegerField(default=0)
    max_per_contact_per_day = models.PositiveIntegerField(default=2)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return self.name


class WhatsAppSendQueue(models.Model):
    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("processing", "Processing"),
        ("sent", "Sent"),
        ("failed", "Failed"),
        ("canceled", "Canceled"),
    ]

    account = models.ForeignKey(WhatsAppAccount, on_delete=models.CASCADE, related_name="queue_items")
    thread = models.ForeignKey(WhatsAppThread, on_delete=models.CASCADE, related_name="queue_items")
    message_body = models.TextField()
    scheduled_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued")
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("scheduled_at", "id")
        indexes = [
            models.Index(fields=["status", "scheduled_at"]),
        ]


class DoNotContactPhone(models.Model):
    phone = models.CharField(max_length=30, unique=True)
    reason = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.phone


class WhatsAppEventLog(models.Model):
    LEVEL_CHOICES = [
        ("info", "Info"),
        ("warn", "Warn"),
        ("error", "Error"),
    ]

    account = models.ForeignKey(WhatsAppAccount, on_delete=models.SET_NULL, null=True, blank=True)
    thread = models.ForeignKey(WhatsAppThread, on_delete=models.SET_NULL, null=True, blank=True)
    event = models.CharField(max_length=80)
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES, default="info")
    message = models.TextField(blank=True, default="")
    payload_json = models.JSONField(blank=True, default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["event", "level"]),
        ]
