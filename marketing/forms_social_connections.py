from django import forms


SOCIAL_CONNECTION_CHOICES = [
    ("google", "Google"),
    ("ga4", "Google Analytics 4"),
    ("gsc", "Google Search Console"),
    ("facebook", "Facebook"),
    ("instagram", "Instagram"),
    ("meta_ads", "Meta Ads"),
    ("linkedin", "LinkedIn"),
    ("tiktok", "TikTok Business"),
    ("youtube", "YouTube"),
    ("google_business", "Google Business Profile"),
]


class MarketingSocialConnectionForm(forms.Form):
    connection_id = forms.IntegerField(required=False, widget=forms.HiddenInput())
    platform = forms.ChoiceField(
        choices=SOCIAL_CONNECTION_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    account_name = forms.CharField(
        max_length=200,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    account_id = forms.CharField(
        max_length=120,
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
    token_expires_at = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs={"class": "form-control", "type": "datetime-local"}),
    )
    scopes = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    is_active = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def __init__(self, *args, connection=None, platform=None, **kwargs):
        initial = kwargs.setdefault("initial", {})
        if connection:
            initial.setdefault("connection_id", connection.pk)
            initial.setdefault("platform", connection.platform)
            initial.setdefault("account_name", connection.account_name)
            initial.setdefault("account_id", connection.account_id)
            initial.setdefault("token_expires_at", connection.expires_at)
            initial.setdefault("scopes", connection.scopes)
            initial.setdefault("is_active", connection.is_active)
        elif platform:
            initial.setdefault("platform", platform)
        super().__init__(*args, **kwargs)

    def clean_account_name(self):
        return (self.cleaned_data.get("account_name") or "").strip()

    def clean_account_id(self):
        return (self.cleaned_data.get("account_id") or "").strip()

    def clean_scopes(self):
        return (self.cleaned_data.get("scopes") or "").strip()
