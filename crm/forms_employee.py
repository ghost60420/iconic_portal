from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from crm.models import Department, EmployeeProfile, Position
from crm.services.employee_identity import alias_conflicts


class EmployeeProfileForm(forms.ModelForm):
    POSITION_ALIASES = {
        "salesperson": "sales_executive",
        "operations_manager": "general_manager",
        "merchandising_manager": "senior_merchandiser",
        "production_coordinator": "production_executive",
        "quality_controller": "quality_inspector",
        "accountant": "accounts_executive",
        "staff": "production_executive",
    }
    DEPARTMENT_ALIASES = {
        "quality_control": "quality",
        "logistics": "shipping",
        "it": "administration",
        "marketing": "sales",
        "customer_service": "administration",
    }
    username = forms.CharField(max_length=150)
    full_name = forms.CharField(max_length=300)
    email = forms.EmailField(required=False)
    is_active = forms.BooleanField(required=False)
    aliases = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Enter one historical name per line or separate names with commas.",
    )
    roles = forms.ModelMultipleChoiceField(
        queryset=Group.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = EmployeeProfile
        fields = [
            "display_name",
            "aliases",
            "phone",
            "position_ref",
            "department_ref",
            "status",
            "manager",
            "profile_photo",
            "notes",
        ]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 4}),
        }
        help_texts = {
            "display_name": "Use the first name or short display name shown in chatter and notifications.",
            "profile_photo": "Optional profile image.",
            "status": "Employment status. Suspended, resigned, and inactive employees cannot sign in.",
        }

    def __init__(self, *args, user_instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user_instance = user_instance
        if self.user_instance is None and getattr(self.instance, "user_id", None):
            self.user_instance = self.instance.user
        User = get_user_model()
        self.fields["manager"].queryset = User.objects.filter(
            is_active=True,
            employee_profile__status__in=EmployeeProfile.MENTIONABLE_STATUSES,
        ).exclude(pk=getattr(self.user_instance, "pk", None)).select_related("employee_profile").order_by(
            "employee_profile__display_name", "first_name", "username"
        )
        self.fields["manager"].label_from_instance = lambda user: (
            f"{user.employee_profile.public_name} — {user.employee_profile.position_name}"
            if user.employee_profile.position_ref_id or user.employee_profile.position
            else user.employee_profile.public_name
        )
        self.fields["roles"].queryset = Group.objects.order_by("name")
        self.fields["position_ref"].queryset = Position.objects.filter(is_active=True).order_by("sort_order", "name")
        self.fields["department_ref"].queryset = Department.objects.filter(is_active=True).order_by("sort_order", "name")
        self.fields["position_ref"].required = False
        self.fields["department_ref"].required = False
        self.fields["position_ref"].label = "Position"
        self.fields["department_ref"].label = "Department"
        self.fields["position_ref"].help_text = "Job title for company structure; permissions come from roles."
        self.fields["department_ref"].help_text = "Select the employee's single primary department."
        self.fields["roles"].widget.attrs["class"] = "people-role-grid"
        self.fields["position_ref"].widget.attrs["required"] = True
        self.fields["department_ref"].widget.attrs["required"] = True
        for field in self.fields.values():
            if not isinstance(field.widget, forms.CheckboxSelectMultiple):
                field.widget.attrs["class"] = "people-input"

        if self.user_instance:
            self.fields["username"].initial = self.user_instance.username
            self.fields["full_name"].initial = self.user_instance.get_full_name()
            self.fields["email"].initial = self.user_instance.email
            self.fields["is_active"].initial = self.user_instance.is_active
            self.fields["roles"].initial = self.user_instance.groups.all()
            self.initial["aliases"] = "\n".join(self.instance.aliases or [])
            if not self.instance.position_ref_id and self.instance.position:
                code = self.POSITION_ALIASES.get(self.instance.position, self.instance.position)
                self.fields["position_ref"].initial = Position.objects.filter(code=code).first()
            if not self.instance.department_ref_id and self.instance.department:
                code = self.DEPARTMENT_ALIASES.get(self.instance.department, self.instance.department)
                self.fields["department_ref"].initial = Department.objects.filter(code=code).first()
        else:
            self.fields["is_active"].initial = True
        self.fields["is_active"].label = "CRM Login Active"
        self.fields["is_active"].help_text = "Controls whether this employee can sign in to the CRM."

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        User = get_user_model()
        matches = User.objects.filter(username__iexact=username)
        if self.user_instance:
            matches = matches.exclude(pk=self.user_instance.pk)
        if matches.exists():
            raise forms.ValidationError("A user with this username already exists.")
        return username

    def clean_full_name(self):
        full_name = " ".join(self.cleaned_data["full_name"].split())
        if not full_name:
            raise forms.ValidationError("Enter the employee's full name.")
        return full_name

    def clean_display_name(self):
        display_name = " ".join((self.cleaned_data.get("display_name") or "").split())
        if not display_name:
            raise forms.ValidationError("Enter the public display name.")
        if "@" in display_name:
            raise forms.ValidationError("Display name cannot contain @.")
        return display_name

    def clean_aliases(self):
        raw_aliases = (self.cleaned_data.get("aliases") or "").replace(",", "\n").splitlines()
        aliases = []
        seen = set()
        for value in raw_aliases:
            alias = " ".join(value.split())
            key = alias.casefold()
            if alias and key not in seen:
                seen.add(key)
                aliases.append(alias)
        conflicts = alias_conflicts(aliases, exclude_profile_id=getattr(self.instance, "pk", None))
        if conflicts:
            details = ", ".join(f"{alias} ({employee})" for alias, employee in conflicts)
            raise forms.ValidationError(f"These aliases already identify another employee: {details}.")
        return aliases

    def clean_position_ref(self):
        value = self.cleaned_data.get("position_ref")
        if value:
            return value
        legacy_code = self.POSITION_ALIASES.get((self.data.get("position") or "").strip(), (self.data.get("position") or "").strip())
        value = Position.objects.filter(code=legacy_code, is_active=True).first()
        if not value:
            raise forms.ValidationError("Select a position from the position library.")
        return value

    def clean_department_ref(self):
        value = self.cleaned_data.get("department_ref")
        if value:
            return value
        legacy_code = self.DEPARTMENT_ALIASES.get((self.data.get("department") or "").strip(), (self.data.get("department") or "").strip())
        value = Department.objects.filter(code=legacy_code, is_active=True).first()
        if not value:
            raise forms.ValidationError("Select a department from the department library.")
        return value

    def clean_manager(self):
        manager = self.cleaned_data.get("manager")
        if not manager or not self.user_instance:
            return manager
        if manager.pk == self.user_instance.pk:
            raise forms.ValidationError("An employee cannot report to themselves.")
        reporting = dict(
            EmployeeProfile.objects.exclude(manager_id=None).values_list("user_id", "manager_id")
        )
        current_id = manager.pk
        visited = set()
        while current_id and current_id not in visited:
            if current_id == self.user_instance.pk:
                raise forms.ValidationError("This manager assignment would create a circular reporting line.")
            visited.add(current_id)
            current_id = reporting.get(current_id)
        return manager

    def save_user_fields(self, user):
        full_name = self.cleaned_data["full_name"].split(maxsplit=1)
        user.username = self.cleaned_data["username"]
        user.first_name = full_name[0]
        user.last_name = full_name[1] if len(full_name) > 1 else ""
        user.email = self.cleaned_data.get("email", "")
        user.is_active = self.requested_user_active()
        user.save(update_fields=["username", "first_name", "last_name", "email", "is_active"])
        return user

    def requested_user_active(self):
        status = self.cleaned_data.get("status")
        if status in {
            EmployeeProfile.STATUS_SUSPENDED,
            EmployeeProfile.STATUS_RESIGNED,
            EmployeeProfile.STATUS_INACTIVE,
        }:
            return False
        return bool(self.cleaned_data.get("is_active"))
