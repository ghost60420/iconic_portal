from django.db import models


class EmailInboxConfig(models.Model):
    LABEL_CHOICES = (
        ("lead", "Lead inbox"),
        ("info", "Info inbox"),
    )

    label = models.CharField(max_length=20, choices=LABEL_CHOICES, unique=True)
    imap_host = models.CharField(max_length=200, blank=True, default="")
    imap_port = models.IntegerField(default=993)
    username = models.CharField(max_length=200, blank=True, default="")
    use_ssl = models.BooleanField(default=True)
    enabled = models.BooleanField(default=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("label",)

    def __str__(self):
        return f"{self.label} {self.username}"