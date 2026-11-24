from django.urls import path
from club_accounts.otp_views import OtpSetupView

from .password_login import PasswordLoginView, PasswordView
from .boss_registration import BossRegistrationSmsView, BossRegistrationView
from .pass_refund import ProviderPassRefundView
from .pass_points import ProviderPassPointsQuoteView
from .voice_views import VoiceRecordingDeleteView, VoiceRecordingListView, VoiceRecordingResolveView
from .h5_login import H5LoginStartView, H5LoginCallbackView, H5LoginStatusView
from .views import (
    AccountLoginView,
    AccountRefreshView,
    CheckinView,
    ProviderPassView,
    CustomerAchievementView,
    EscortMeView,
    QualificationOptionsView,
    EscortProfileListView,
    EscortProfileDetailView,
    EscortScheduleView,
    EscortSkillOptionsView,
    MeView,
    MeAvatarView,
    ProviderStatsView,
    UpdateEscortStatusView,
)

urlpatterns = [
    path('recordings/', VoiceRecordingListView.as_view()),
    path('recordings/resolve/', VoiceRecordingResolveView.as_view()),
    path('recordings/<int:recording_id>/', VoiceRecordingDeleteView.as_view()),
    path('otp-setup/', OtpSetupView.as_view()),
    path('password-login/', PasswordLoginView.as_view()),
    path('boss-registration/sms/', BossRegistrationSmsView.as_view()),
    path('boss-registration/', BossRegistrationView.as_view()),
    path('password/', PasswordView.as_view()),
    path('h5-login/start/', H5LoginStartView.as_view()),
    path('h5-login/callback/', H5LoginCallbackView.as_view()),
    path('h5-login/status/', H5LoginStatusView.as_view()),
    path('account-login/', AccountLoginView.as_view()),
    path('refresh/', AccountRefreshView.as_view()),
    path('me/', MeView.as_view()),
    path('me/avatar/', MeAvatarView.as_view()),
    path('qualification-options/', QualificationOptionsView.as_view()),
    path('escorts/', EscortProfileListView.as_view()),
    path('escorts/me/', EscortMeView.as_view()),
    path('escorts/status/', UpdateEscortStatusView.as_view()),
    path('escorts/schedule/', EscortScheduleView.as_view()),
    path('escorts/skill-options/', EscortSkillOptionsView.as_view()),
    path('escorts/<int:provider_id>/', EscortProfileDetailView.as_view()),
    path('provider-stats/', ProviderStatsView.as_view()),
    path('provider-pass/', ProviderPassView.as_view()),
    path('provider-pass/points-quote/', ProviderPassPointsQuoteView.as_view()),
    path('provider-pass/<int:purchase_id>/refund/', ProviderPassRefundView.as_view()),
    path('achievements/', CustomerAchievementView.as_view()),
    path('checkin/', CheckinView.as_view()),
]
