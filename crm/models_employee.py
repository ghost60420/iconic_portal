from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models.deletion import ProtectedError
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver


class EmployeeIdSequence(models.Model):
    key = models.CharField(max_length=30, primary_key=True, default="employee")
    last_value = models.PositiveBigIntegerField(default=0)

    @classmethod
    def next_employee_id(cls):
        with transaction.atomic():
            sequence, _created = cls.objects.select_for_update().get_or_create(key="employee")
            sequence.last_value += 1
            sequence.save(update_fields=["last_value"])
            return f"EMP{sequence.last_value:04d}"


class EmployeeProfile(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_ON_LEAVE = "on_leave"
    STATUS_SUSPENDED = "suspended"
    STATUS_RESIGNED = "resigned"
    STATUS_INACTIVE = "inactive"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_ON_LEAVE, "On Leave"),
        (STATUS_SUSPENDED, "Suspended"),
        (STATUS_RESIGNED, "Resigned"),
        (STATUS_INACTIVE, "Inactive"),
    ]
    MENTIONABLE_STATUSES = (STATUS_ACTIVE, STATUS_ON_LEAVE)
    POSITION_CHOICES = [
        ("ceo", "CEO"),
        ("director", "Director"),
        ("general_manager", "General Manager"),
        ("operations_manager", "Operations Manager"),
        ("sales_manager", "Sales Manager"),
        ("production_manager", "Production Manager"),
        ("merchandising_manager", "Merchandising Manager"),
        ("accounts_manager", "Accounts Manager"),
        ("sales_executive", "Sales Executive"),
        ("merchandiser", "Merchandiser"),
        ("production_coordinator", "Production Coordinator"),
        ("quality_controller", "Quality Controller"),
        ("accountant", "Accountant"),
        ("customer_service", "Customer Service"),
        ("administrator", "Administrator"),
        ("staff", "Staff"),
        ("other", "Other"),
    ]
    DEPARTMENT_CHOICES = [
        ("management", "Management"),
        ("sales", "Sales"),
        ("merchandising", "Merchandising"),
        ("production", "Production"),
        ("accounts", "Accounts"),
        ("administration", "Administration"),
        ("quality_control", "Quality Control"),
        ("logistics", "Logistics"),
        ("it", "IT"),
        ("marketing", "Marketing"),
        ("customer_service", "Customer Service"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="employee_profile",
    )
    display_name = models.CharField(max_length=100, blank=True, default="", db_index=True)
    phone = models.CharField(max_length=50, blank=True, default="")
    employee_id = models.CharField(max_length=40, unique=True, editable=False)
    position = models.CharField(max_length=40, choices=POSITION_CHOICES, blank=True, default="")
    department = models.CharField(max_length=40, choices=DEPARTMENT_CHOICES, blank=True, default="")
    position_ref = models.ForeignKey(
        "crm.Position",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="employees",
    )
    department_ref = models.ForeignKey(
        "crm.Department",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="employees",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="managed_employee_profiles",
    )
    profile_photo = models.ImageField(upload_to="employee_profiles/", null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("display_name", "user__username")
        permissions = [
            ("manage_employee_profiles", "Can manage employee profiles and roles"),
            ("view_all_sales_profiles", "Can view all salesperson profiles"),
        ]

    def save(self, *args, **kwargs):
        self.display_name = " ".join((self.display_name or "").split())
        if self.position_ref_id:
            self.position = self.position_ref.code
        if self.department_ref_id:
            self.department = self.department_ref.code
        allocated_employee_id = False
        if not self.employee_id:
            self.employee_id = EmployeeIdSequence.next_employee_id()
            allocated_employee_id = True
        if self.manager_id:
            reporting = dict(
                EmployeeProfile.objects.exclude(manager_id=None).values_list("user_id", "manager_id")
            )
            current_id = self.manager_id
            visited = set()
            while current_id and current_id not in visited:
                if current_id == self.user_id:
                    raise ValidationError({"manager": "This manager assignment would create a circular reporting line."})
                visited.add(current_id)
                current_id = reporting.get(current_id)
        if allocated_employee_id and kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"employee_id"}
        super().save(*args, **kwargs)

    @property
    def public_name(self):
        return self.display_name or self.user.first_name or self.user.get_username()

    @property
    def position_name(self):
        return self.position_ref.name if self.position_ref_id else self.get_position_display()

    @property
    def department_name(self):
        return self.department_ref.name if self.department_ref_id else self.get_department_display()

    @property
    def is_mentionable(self):
        return self.user.is_active and self.status in self.MENTIONABLE_STATUSES

    @property
    def initials(self):
        return (self.public_name[:1] or "?").upper()

    def __str__(self):
        return self.public_name


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def ensure_employee_profile(sender, instance, created, **kwargs):
    if created:
        EmployeeProfile.objects.get_or_create(
            user=instance,
            defaults={"display_name": instance.first_name or instance.get_username()},
        )


@receiver(pre_delete, sender=EmployeeProfile)
def prevent_employee_profile_deletion(sender, instance, **kwargs):
    raise ProtectedError(
        "Employee profiles are retained for historical records. Mark the employee Resigned or Inactive instead.",
        [instance],
    )
