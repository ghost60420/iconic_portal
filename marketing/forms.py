from django import forms

from .models import (
    Campaign,
    TrackedLink,
    ContactList,
    OutreachCampaign,
    OutreachMessageTemplate,
    MarketingCompetitor,
    MarketingCompetitorAccount,
    MarketingCompetitorPost,
    MarketingContentIdea,
    MarketingKeywordPlan,
    MarketingVideoIdea,
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


class MarketingCompetitorForm(forms.ModelForm):
    class Meta:
        model = MarketingCompetitor
        fields = ["name", "website", "country", "category", "industry", "status", "last_checked_at", "notes", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "website": forms.URLInput(attrs={"class": "form-control"}),
            "country": forms.Select(attrs={"class": "form-select"}),
            "category": forms.TextInput(attrs={"class": "form-control"}),
            "industry": forms.TextInput(attrs={"class": "form-control"}),
            "status": forms.Select(attrs={"class": "form-select"}),
            "last_checked_at": forms.DateTimeInput(attrs={"class": "form-control", "type": "datetime-local"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
        }


class MarketingKeywordPlanForm(forms.ModelForm):
    class Meta:
        model = MarketingKeywordPlan
        fields = [
            "keyword",
            "target_country",
            "target_audience",
            "product_category",
            "search_intent",
            "priority",
            "trend_status",
            "difficulty_estimate",
            "content_type",
            "landing_page_suggestion",
            "status",
            "notes",
        ]
        widgets = {
            "keyword": forms.TextInput(attrs={"class": "form-control"}),
            "target_country": forms.Select(attrs={"class": "form-select"}),
            "target_audience": forms.TextInput(attrs={"class": "form-control"}),
            "product_category": forms.Select(attrs={"class": "form-select"}),
            "search_intent": forms.Select(attrs={"class": "form-select"}),
            "priority": forms.Select(attrs={"class": "form-select"}),
            "trend_status": forms.Select(attrs={"class": "form-select"}),
            "difficulty_estimate": forms.Select(attrs={"class": "form-select"}),
            "content_type": forms.Select(attrs={"class": "form-select"}),
            "landing_page_suggestion": forms.TextInput(attrs={"class": "form-control"}),
            "status": forms.Select(attrs={"class": "form-select"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }


class MarketingContentIdeaForm(forms.ModelForm):
    class Meta:
        model = MarketingContentIdea
        fields = [
            "title",
            "content_type",
            "target_platform",
            "keyword",
            "audience",
            "funnel_stage",
            "priority",
            "due_date",
            "assigned_to",
            "status",
            "notes",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "content_type": forms.Select(attrs={"class": "form-select"}),
            "target_platform": forms.Select(attrs={"class": "form-select"}),
            "keyword": forms.TextInput(attrs={"class": "form-control"}),
            "audience": forms.TextInput(attrs={"class": "form-control"}),
            "funnel_stage": forms.Select(attrs={"class": "form-select"}),
            "priority": forms.Select(attrs={"class": "form-select"}),
            "due_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "assigned_to": forms.Select(attrs={"class": "form-select"}),
            "status": forms.Select(attrs={"class": "form-select"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }


class MarketingVideoIdeaForm(forms.ModelForm):
    class Meta:
        model = MarketingVideoIdea
        fields = [
            "video_title",
            "platform",
            "hook",
            "main_talking_points",
            "product_category",
            "target_keyword",
            "status",
            "assigned_to",
            "due_date",
        ]
        widgets = {
            "video_title": forms.TextInput(attrs={"class": "form-control"}),
            "platform": forms.Select(attrs={"class": "form-select"}),
            "hook": forms.TextInput(attrs={"class": "form-control"}),
            "main_talking_points": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "product_category": forms.Select(attrs={"class": "form-select"}),
            "target_keyword": forms.TextInput(attrs={"class": "form-control"}),
            "status": forms.Select(attrs={"class": "form-select"}),
            "assigned_to": forms.Select(attrs={"class": "form-select"}),
            "due_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
        }


class MarketingCompetitorAccountForm(forms.ModelForm):
    class Meta:
        model = MarketingCompetitorAccount
        fields = [
            "platform",
            "profile_url",
            "handle",
            "followers_count",
            "following_count",
            "is_active",
            "last_checked_at",
        ]
        widgets = {
            "platform": forms.Select(attrs={"class": "form-select"}),
            "profile_url": forms.URLInput(attrs={"class": "form-control"}),
            "handle": forms.TextInput(attrs={"class": "form-control"}),
            "followers_count": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
            "following_count": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
            "last_checked_at": forms.DateTimeInput(attrs={"class": "form-control", "type": "datetime-local"}),
        }


class MarketingCompetitorPostForm(forms.ModelForm):
    class Meta:
        model = MarketingCompetitorPost
        fields = [
            "post_url",
            "caption_text",
            "content_type",
            "published_at",
            "likes",
            "comments",
            "shares",
            "views",
            "saves",
            "detected_theme",
        ]
        widgets = {
            "post_url": forms.URLInput(attrs={"class": "form-control"}),
            "caption_text": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "content_type": forms.Select(attrs={"class": "form-select"}),
            "published_at": forms.DateTimeInput(attrs={"class": "form-control", "type": "datetime-local"}),
            "likes": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
            "comments": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
            "shares": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
            "views": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
            "saves": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
            "detected_theme": forms.TextInput(attrs={"class": "form-control"}),
        }
