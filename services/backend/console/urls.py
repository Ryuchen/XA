from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .auth import ConsoleLoginView, ConsoleProfileView, ConsoleRefreshView
from .views import (
    AchievementViewSet,
    AdminMembershipViewSet,
    AnnouncementViewSet,
    AuditLogViewSet,
    AuditionLinkViewSet,
    AuditionSignupViewSet,
    BannerViewSet,
    BossTypeViewSet,
    ChatSessionViewSet,
    CheckinGiftViewSet,
    CheckinProgressViewSet,
    CheckinRuleConfigView,
    CommissionConfigView,
    CouponViewSet,
    DashboardView,
    DisposeRecordViewSet,
    EscortLevelViewSet,
    EscortViewSet,
    EvaluationViewSet,
    GameCategoryViewSet,
    MessageViewSet,
    OrderViewSet,
    PermissionTreeView,
    PlayerDashboardView,
    ProviderReportViewSet,
    PromotionViewSet,
    RechargeRecordViewSet,
    RoleViewSet,
    ServiceCategoryViewSet,
    ServiceItemViewSet,
    SupportCardViewSet,
    TransactionViewSet,
    UserCouponViewSet,
    UserViewSet,
    WalletViewSet,
    WithdrawConfigView,
    WithdrawRequestViewSet,
)

router = DefaultRouter()
router.register('users', UserViewSet, basename='admin-user')
router.register('escorts', EscortViewSet, basename='admin-escort')
router.register('orders', OrderViewSet, basename='admin-order')
router.register('evaluations', EvaluationViewSet, basename='admin-evaluation')
router.register('reports', ProviderReportViewSet, basename='admin-report')
router.register('withdrawals', WithdrawRequestViewSet, basename='admin-withdrawal')
router.register('wallets', WalletViewSet, basename='admin-wallet')
router.register('recharge-records', RechargeRecordViewSet, basename='admin-recharge-record')
router.register('dispose-records', DisposeRecordViewSet, basename='admin-dispose-record')
router.register('transactions', TransactionViewSet, basename='admin-transaction')
router.register('coupons', CouponViewSet, basename='admin-coupon')
router.register('user-coupons', UserCouponViewSet, basename='admin-user-coupon')
router.register('announcements', AnnouncementViewSet, basename='admin-announcement')
router.register('banners', BannerViewSet, basename='admin-banner')
router.register('achievements', AchievementViewSet, basename='admin-achievement')
router.register('checkin-gifts', CheckinGiftViewSet, basename='admin-checkin-gift')
router.register('checkin-progress', CheckinProgressViewSet, basename='admin-checkin-progress')
router.register('service-items', ServiceItemViewSet, basename='admin-service-item')
router.register('game-categories', GameCategoryViewSet, basename='admin-game-category')
router.register('service-categories', ServiceCategoryViewSet, basename='admin-service-category')
router.register('boss-types', BossTypeViewSet, basename='admin-boss-type')
router.register('escort-levels', EscortLevelViewSet, basename='admin-escort-level')
router.register('promotions', PromotionViewSet, basename='admin-promotion')
router.register('support-cards', SupportCardViewSet, basename='admin-support-card')
router.register('audition-links', AuditionLinkViewSet, basename='admin-audition-link')
router.register('audition-signups', AuditionSignupViewSet, basename='admin-audition-signup')
router.register('chat-sessions', ChatSessionViewSet, basename='admin-chat-session')
router.register('messages', MessageViewSet, basename='admin-message')
router.register('roles', RoleViewSet, basename='admin-role')
router.register('admins', AdminMembershipViewSet, basename='admin-membership')
router.register('audit-logs', AuditLogViewSet, basename='admin-audit-log')

urlpatterns = [
    path('auth/login', ConsoleLoginView.as_view()),
    path('auth/refresh', ConsoleRefreshView.as_view()),
    path('auth/profile', ConsoleProfileView.as_view()),
    path('dashboard/stats', DashboardView.as_view()),
    path('dashboard/player', PlayerDashboardView.as_view()),
    path('permissions', PermissionTreeView.as_view()),
    path('config/commission', CommissionConfigView.as_view()),
    path('config/withdraw', WithdrawConfigView.as_view()),
    path('config/checkin', CheckinRuleConfigView.as_view()),
    path('', include(router.urls)),
]
