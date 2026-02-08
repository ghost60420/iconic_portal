from django.contrib.auth.views import LoginView
from django.urls import reverse_lazy


class DashboardLoginView(LoginView):
    template_name = "registration/login.html"

    def get_success_url(self):
        return reverse_lazy("main_dashboard")
