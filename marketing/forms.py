from django import forms

from .models import (
    Campaign,
    TrackedLink,
    ContactList,
    OutreachCampaign,
    OutreachMessageTemplate,
    SocialAccount,
)


class CampaignForm(forms.ModelForm):
    class Meta:
        model = Campaign
        fields = ["name", "goal", "start_date", "end_date", "budget", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "goal": forms.Select(attrs={"class": "form-select"}),
            "start_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "end_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "budget": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
        }


class TrackedLinkForm(forms.ModelForm):
    class Meta:
        model = TrackedLink
        fields = [
            "name",
            "base_url",
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_content",
            "utm_term",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "base_url": forms.URLInput(attrs={"class": "form-control"}),
            "utm_source": forms.TextInput(attrs={"class": "form-control"}),
            "utm_medium": forms.TextInput(attrs={"class": "form-control"}),
            "utm_campaign": forms.TextInput(attrs={"class": "form-control"}),
            "utm_content": forms.TextInput(attrs={"class": "form-control"}),
            "utm_term": forms.TextInput(attrs={"class": "form-control"}),
        }


class ContactListForm(forms.ModelForm):
    class Meta:
        model = ContactList
        fields = ["name", "description"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        }


class OutreachCampaignForm(forms.ModelForm):
    class Meta:
        model = OutreachCampaign
        fields = [
            "name",
            "channel",
            "status",
            "sending_account",
            "daily_limit",
            "hourly_limit",
            "contact_list",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "channel": forms.Select(attrs={"class": "form-select"}),
            "status": forms.Select(attrs={"class": "form-select"}),
            "sending_account": forms.TextInput(attrs={"class": "form-control"}),
            "daily_limit": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "hourly_limit": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "contact_list": forms.Select(attrs={"class": "form-select"}),
        }


class OutreachMessageTemplateForm(forms.ModelForm):
    class Meta:
        model = OutreachMessageTemplate
        fields = ["subject_template", "body_template"]
        widgets = {
            "subject_template": forms.TextInput(attrs={"class": "form-control"}),
            "body_template": forms.Textarea(attrs={"class": "form-control", "rows": 6}),
        }


class CSVUploadForm(forms.Form):
    csv_file = forms.FileField(widget=forms.ClearableFileInput(attrs={"class": "form-control"}))
    contact_list = forms.ModelChoiceField(
        queryset=ContactList.objects.all(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["contact_list"].empty_label = "Select list (optional)"


class SocialAccountConnectForm(forms.Form):
    platform = forms.ChoiceField(
        choices=SocialAccount.PLATFORM_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    display_name = forms.CharField(
        max_length=200,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    external_account_id = forms.CharField(
        max_length=120,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    timezone = forms.CharField(
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    access_token = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )
    refresh_token = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )
    expires_at = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs={"class": "form-control", "type": "datetime-local"}),
    )
    scopes = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
