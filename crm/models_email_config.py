from django.db import models


class EmailInboxConfig(models.Model):
    """
    Stores IMAP config in DB so Admin can edit it.
    """
    LABEL_CHOICES = (
        ("lead", "Lead inbox"),
        ("info", "Info inbox"),
    )

    label = models.CharField(max_length=30, choices=LABEL_CHOICES, unique=True)
    imap_host = models.CharField(max_length=120, blank=True, default="imap.gmail.com")
    imap_port = models.IntegerField(default=993)
    username = models.CharField(max_length=150, blank=True, default="")
    password = models.CharField(max_length=150, blank=True, default="")
    use_ssl = models.BooleanField(default=True)
    is_enabled = models.BooleanField(default=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("label",)

    def __str__(self):
        return f"{self.label} ({self.username})"