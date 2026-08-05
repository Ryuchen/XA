from django.urls import path

from .views import (
    AccountLoginView,
    AccountRefreshView,
    CheckinView,
    ProviderPassView,
    CustomerAchievementView,
    EscortMeView,
    EscortProfileListView,
    EscortScheduleView,
    EscortSkillOptionsView,
    MeView,
    ProviderStatsView,
    UpdateEscortStatusView,
    WechatLoginView,
)

urlpatterns = [
    path('wechat-login/', WechatLoginView.as_view()),
    path('account-login/', AccountLoginView.as_view()),
    path('refresh/', AccountRefreshView.as_view()),
    path('me/', MeView.as_view()),
    path('escorts/', EscortProfileListView.as_view()),
    path('escorts/me/', EscortMeView.as_view()),
    path('escorts/status/', UpdateEscortStatusView.as_view()),
    path('escorts/schedule/', EscortScheduleView.as_view()),
    path('escorts/skill-options/', EscortSkillOptionsView.as_view()),
    path('provider-stats/', ProviderStatsView.as_view()),
    path('provider-pass/', ProviderPassView.as_view()),
    path('achievements/', CustomerAchievementView.as_view()),
    path('checkin/', CheckinView.as_view()),
]
