from django.urls import path
from .provider_registration import PublicRegistrationView, RegistrationStatusView, RegistrationResubmitView, PublicRegistrationRulesView

urlpatterns = [
    path('rules/', PublicRegistrationRulesView.as_view()),
    path('', PublicRegistrationView.as_view()),
    path('status/', RegistrationStatusView.as_view()),
    path('resubmit/', RegistrationResubmitView.as_view()),
]
