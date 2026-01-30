# crm/models_access.py

from django.conf import settings
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver


class UserAccess(models.Model):
    ROLE_CA = "CA"
    ROLE_BD = "BD"

    ROLE_CHOICES = [
        (ROLE_CA, "CA Team"),
        (ROLE_BD, "BD Team"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="access",
    )

    role = models.CharField(max_length=2, choices=ROLE_CHOICES, default=ROLE_BD)

    # Module checkmarks
    can_leads = models.BooleanField(default=True)
    can_opportunities = models.BooleanField(default=True)
    can_customers = models.BooleanField(default=True)
    can_inventory = models.BooleanField(default=True)
    can_production = models.BooleanField(default=True)
    can_shipping = models.BooleanField(default=True)
    can_ai = models.BooleanField(default=True)
    can_calendar = models.BooleanField(default=True)

    # Accounting checkmarks
    can_accounting_bd = models.BooleanField(default=True)
    can_accounting_ca = models.BooleanField(default=False)
    can_library = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "User Access"
        verbose_name_plural = "User Access"

    def __str__(self):
        return f"Access: {self.user.username} ({self.role})"

    @property
    def is_bd(self):
        return self.role == self.ROLE_BD

    @property
    def is_ca(self):
        return self.role == self.ROLE_CA

    def clean(self):
        # Hard rule: BD can never have CA accounting
        if self.role == self.ROLE_BD:
            self.can_accounting_ca = False

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def ensure_user_access(sender, instance, created, **kwargs):
    # Auto create access row for every new user
    if created:
        UserAccess.objects.get_or_create(user=instance)