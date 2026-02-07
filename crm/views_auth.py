from django.contrib.auth.views import LoginView
from django.urls import reverse_lazy


class DashboardLoginView(LoginView):
    template_name = "registration/login.html"

    def get_success_url(self):
        user = self.request.user
        access = getattr(user, "access", None)
        if access:
            if access.can_leads:
                return reverse_lazy("leads_list")
            if access.can_opportunities:
                return reverse_lazy("opportunities_list")
            if access.can_customers:
                return reverse_lazy("customers_list")
        return reverse_lazy("leads_list")
