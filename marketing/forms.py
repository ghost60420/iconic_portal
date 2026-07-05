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
    MarketingKeywordGeneration,
    MarketingKeywordPlan,
    MarketingTask,
    MarketingTrendEntry,
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
        fields = [
            "name", "website", "country", "category", "industry", "keywords", "strengths",
            "weaknesses", "content_frequency", "content_ideas", "status", "last_checked_at", "notes", "is_active",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "website": forms.URLInput(attrs={"class": "form-control"}),
            "country": forms.Select(attrs={"class": "form-select"}),
            "category": forms.TextInput(attrs={"class": "form-control"}),
            "industry": forms.TextInput(attrs={"class": "form-control"}),
            "keywords": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
            "strengths": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
            "weaknesses": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
            "content_frequency": forms.TextInput(attrs={"class": "form-control", "placeholder": "Example: 3 posts per week"}),
            "content_ideas": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
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
            "monthly_search_estimate",
            "competition",
            "content_type",
            "landing_page_suggestion",
            "suggested_article",
            "suggested_video",
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
            "monthly_search_estimate": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
            "competition": forms.Select(attrs={"class": "form-select"}),
            "content_type": forms.Select(attrs={"class": "form-select"}),
            "landing_page_suggestion": forms.TextInput(attrs={"class": "form-control"}),
            "suggested_article": forms.TextInput(attrs={"class": "form-control"}),
            "suggested_video": forms.TextInput(attrs={"class": "form-control"}),
            "status": forms.Select(attrs={"class": "form-select"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["competition"].required = False

    def clean_competition(self):
        return self.cleaned_data.get("competition") or "unknown"


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

    def __init__(self, *args, assignee_choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        if assignee_choices is not None:
            self.fields["assigned_to"].widget.choices = assignee_choices


class MarketingVideoIdeaForm(forms.ModelForm):
    class Meta:
        model = MarketingVideoIdea
        fields = [
            "video_title",
            "platform",
            "hook",
            "thumbnail_text",
            "opening",
            "main_talking_points",
            "closing_cta",
            "video_length",
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
            "thumbnail_text": forms.TextInput(attrs={"class": "form-control"}),
            "opening": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
            "main_talking_points": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "closing_cta": forms.TextInput(attrs={"class": "form-control"}),
            "video_length": forms.TextInput(attrs={"class": "form-control", "placeholder": "Example: 60 seconds"}),
            "product_category": forms.Select(attrs={"class": "form-select"}),
            "target_keyword": forms.TextInput(attrs={"class": "form-control"}),
            "status": forms.Select(attrs={"class": "form-select"}),
            "assigned_to": forms.Select(attrs={"class": "form-select"}),
            "due_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
        }

    def __init__(self, *args, assignee_choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        if assignee_choices is not None:
            self.fields["assigned_to"].widget.choices = assignee_choices


class MarketingKeywordGenerationForm(forms.ModelForm):
    class Meta:
        model = MarketingKeywordGeneration
        fields = ["country", "industry", "product", "target_customer"]
        widgets = {
            "country": forms.Select(attrs={"class": "form-select"}),
            "industry": forms.TextInput(attrs={"class": "form-control", "placeholder": "Apparel manufacturing"}),
            "product": forms.TextInput(attrs={"class": "form-control", "placeholder": "Private label hoodies"}),
            "target_customer": forms.TextInput(attrs={"class": "form-control", "placeholder": "Canadian startup clothing brands"}),
        }


class MarketingBlogPlanForm(forms.ModelForm):
    class Meta:
        model = MarketingContentIdea
        fields = [
            "title", "keyword", "secondary_keywords", "meta_title", "meta_description",
            "outline", "call_to_action", "audience", "estimated_read_time", "author",
            "assigned_to", "priority", "due_date", "status", "notes",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "keyword": forms.TextInput(attrs={"class": "form-control"}),
            "secondary_keywords": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
            "meta_title": forms.TextInput(attrs={"class": "form-control"}),
            "meta_description": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
            "outline": forms.Textarea(attrs={"class": "form-control", "rows": 5}),
            "call_to_action": forms.TextInput(attrs={"class": "form-control"}),
            "audience": forms.TextInput(attrs={"class": "form-control"}),
            "estimated_read_time": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "author": forms.Select(attrs={"class": "form-select"}),
            "assigned_to": forms.Select(attrs={"class": "form-select"}),
            "priority": forms.Select(attrs={"class": "form-select"}),
            "due_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "status": forms.Select(attrs={"class": "form-select"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, assignee_choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        if assignee_choices is not None:
            self.fields["author"].widget.choices = assignee_choices
            self.fields["assigned_to"].widget.choices = assignee_choices

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.content_type = "blog"
        instance.target_platform = "website"
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class MarketingTrendEntryForm(forms.ModelForm):
    class Meta:
        model = MarketingTrendEntry
        fields = [
            "trend_category", "country", "product", "keyword", "trend_direction",
            "recommended_content_idea", "notes",
        ]
        widgets = {
            "trend_category": forms.TextInput(attrs={"class": "form-control", "placeholder": "Example: Seasonal demand"}),
            "country": forms.Select(attrs={"class": "form-select"}),
            "product": forms.TextInput(attrs={"class": "form-control"}),
            "keyword": forms.TextInput(attrs={"class": "form-control"}),
            "trend_direction": forms.Select(attrs={"class": "form-select"}),
            "recommended_content_idea": forms.TextInput(attrs={"class": "form-control"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }


class MarketingTaskForm(forms.Form):
    title = forms.CharField(max_length=300, widget=forms.TextInput(attrs={"class": "form-control"}))
    source = forms.ChoiceField(required=False, choices=(), widget=forms.Select(attrs={"class": "form-select"}))
    assigned_to = forms.ChoiceField(required=False, choices=(), widget=forms.Select(attrs={"class": "form-select"}))
    due_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}))
    priority = forms.ChoiceField(choices=MarketingTask._meta.get_field("priority").choices, widget=forms.Select(attrs={"class": "form-select"}))
    platform = forms.ChoiceField(choices=MarketingTask.PLATFORM_CHOICES, widget=forms.Select(attrs={"class": "form-select"}))
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}))

    def __init__(self, *args, assignee_choices=None, source_choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"].choices = assignee_choices or [("", "Unassigned")]
        self.fields["source"].choices = source_choices or [("", "No source")]


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
