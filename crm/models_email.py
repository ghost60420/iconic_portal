# crm/models_email.py
from django.db import models
from django.conf import settings


class EmailThread(models.Model):
    label = models.CharField(max_length=30, db_index=True)  # lead / info
    mailbox = models.CharField(max_length=150, blank=True, default="")  # lead@... / info@...
    subject = models.CharField(max_length=255, blank=True, default="")
    from_email = models.CharField(max_length=255, blank=True, default="")
    from_name = models.CharField(max_length=255, blank=True, default="")

    # Optional: link a thread to a lead (best effort)
    lead = models.ForeignKey(
        "crm.Lead",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="email_threads",
    )

    last_message_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-last_message_at", "-id")
        indexes = [
            models.Index(fields=["label"]),
            models.Index(fields=["last_message_at"]),
            models.Index(fields=["lead"]),
        ]

    def __str__(self):
        return f"{self.label} {self.subject[:60]}"


class EmailMessage(models.Model):
    thread = models.ForeignKey(EmailThread, on_delete=models.CASCADE, related_name="messages")

    # Unique ID from IMAP (UID) so we do not import twice
    imap_uid = models.CharField(max_length=80, db_index=True)

    subject = models.CharField(max_length=255, blank=True, default="")
    from_email = models.CharField(max_length=255, blank=True, default="")
    from_name = models.CharField(max_length=255, blank=True, default="")
    to_email = models.CharField(max_length=255, blank=True, default="")

    body_text = models.TextField(blank=True, default="")
    body_html = models.TextField(blank=True, default="")

    is_form_entry = models.BooleanField(default=False)
    is_lead_candidate = models.BooleanField(default=False)

    # Store form entry number like "952"
    form_entry_no = models.CharField(max_length=30, blank=True, default="", db_index=True)

    # Quick flag for important emails
    is_important = models.BooleanField(default=False)

    # Optional: link a message to a lead
    lead = models.ForeignKey(
        "crm.Lead",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="email_messages",
    )

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
            models.UniqueConstraint(fields=["thread", "imap_uid"], name="uniq_email_thread_uid")
        ]
        indexes = [
            models.Index(fields=["is_lead_candidate", "is_form_entry"]),
            models.Index(fields=["form_entry_no"]),
            models.Index(fields=["is_important"]),
            models.Index(fields=["lead"]),
        ]

    def __str__(self):
        return f"{self.from_email} {self.subject[:50]}"