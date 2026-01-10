from django.conf import settings
from django.db import models

class UserAccess(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    can_leads = models.BooleanField(default=True)
    can_opportunities = models.BooleanField(default=True)
    can_customers = models.BooleanField(default=True)
    can_inventory = models.BooleanField(default=True)
    can_production = models.BooleanField(default=True)
    can_shipping = models.BooleanField(default=True)
    can_ai = models.BooleanField(default=True)
    can_calendar = models.BooleanField(default=True)

    # Accounting switches
    can_accounting_bd = models.BooleanField(default=True)
    can_accounting_ca = models.BooleanField(default=False)  # keep false for BD users

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Access: {self.user.username}"