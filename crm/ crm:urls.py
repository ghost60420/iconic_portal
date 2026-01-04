from django.urls import path
from . import views

urlpatterns = [
    path("leads/", views.leads_list, name="leads_list"),
]