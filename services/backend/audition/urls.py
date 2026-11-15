from django.urls import path

from .views import (
    AuditionEnterView,
    AuditionExchangeView,
    AuditionPublicView,
    AuditionSignupView,
    MyAuditionSignupView,
)

urlpatterns = [
    path('enter', AuditionEnterView.as_view(), name='audition-enter'),
    path('info', AuditionPublicView.as_view(), name='audition-info'),
    path('exchange', AuditionExchangeView.as_view(), name='audition-exchange'),
    path('signup', AuditionSignupView.as_view(), name='audition-signup'),
    path('my-signups', MyAuditionSignupView.as_view(), name='audition-my-signups'),
]
