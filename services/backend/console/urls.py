from users.views import QualificationOptionsView
from .deposit_rules import DepositRulesView
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .auth import ConsoleLoginView, ConsoleProfileView, ConsoleRefreshView
from .options import FormOptionsView
from .provider_pass import ProviderPassConfigView, ProviderPassPurchaseViewSet
from .kook_dispatch import KookConfigView, KookChannelCheckView, KookTemplatePreviewView, KookProbeView
from .views import (
    AchievementViewSet,
    AdminMembershipViewSet,
    AnnouncementViewSet,
    AuditLogViewSet,
    AuditionLinkViewSet,
    AuditionSignupViewSet,
    BannerViewSet,
    BanViewSet,
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

from .gameplay import GameplayViewSet, CatalogImageUploadView
from engagement.kook_console import KookPointsStatusView

from .provider_registration import ProviderRegistrationViewSet

from orders.aftersale import AfterSaleViewSet
router = DefaultRouter()
router.register('aftersales', AfterSaleViewSet, basename='aftersale')
router.register('provider-registrations', ProviderRegistrationViewSet, basename='provider-registration')
from engagement.console import (RewardViewSet, ClaimViewSet, BroadcastRuleViewSet, BroadcastViewSet, ProviderNamingViewSet,
    ContentViewSet, TierViewSet, PackageViewSet, CampaignViewSet, OperationConfigView, ExternalPointsView, ContentImageUploadView, GiftBenefitViewSet)
router.register('gift-benefits', GiftBenefitViewSet, basename='gift-benefits')
router.register('club-rewards', RewardViewSet, basename='club-reward')
router.register('club-claims', ClaimViewSet, basename='club-claim')
router.register('broadcast-rules', BroadcastRuleViewSet, basename='broadcast-rule')
router.register('broadcasts', BroadcastViewSet, basename='broadcast')
router.register('provider-namings', ProviderNamingViewSet, basename='provider-naming')
router.register('club-content', ContentViewSet, basename='club-content')
router.register('vip-tiers', TierViewSet, basename='vip-tier')
router.register('recharge-packages', PackageViewSet, basename='recharge-package')
router.register('lotteries', CampaignViewSet, basename='lottery')
router.register('gameplays', GameplayViewSet, basename='admin-gameplay')
router.register('users', UserViewSet, basename='admin-user')
router.register('escorts', EscortViewSet, basename='admin-escort')
router.register('orders', OrderViewSet, basename='admin-order')
router.register('evaluations', EvaluationViewSet, basename='admin-evaluation')
router.register('reports', ProviderReportViewSet, basename='admin-report')
router.register('withdrawals', WithdrawRequestViewSet, basename='admin-withdrawal')
router.register('wallets', WalletViewSet, basename='admin-wallet')
router.register('pass-purchases', ProviderPassPurchaseViewSet, basename='admin-pass-purchase')
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
router.register('bans', BanViewSet, basename='admin-ban')

from orders.traffic_views import TrafficConfigView

urlpatterns = [
    path('qualification-options/', QualificationOptionsView.as_view()),
    path('order-traffic/', TrafficConfigView.as_view()),
    path('kook-points-status/',KookPointsStatusView.as_view()),
    path('config/kook-dispatch/', KookConfigView.as_view()),
    path('config/kook-dispatch/check/', KookChannelCheckView.as_view()),
    path('config/kook-dispatch/preview/', KookTemplatePreviewView.as_view()),
    path('config/kook-dispatch/test/', KookProbeView.as_view()),
    path('club-config/', OperationConfigView.as_view()),
    path('external-points/', ExternalPointsView.as_view()),
    path('club-content-image/', ContentImageUploadView.as_view()),
    path('catalog-image/', CatalogImageUploadView.as_view()),
    path('options/<slug:resource>/', FormOptionsView.as_view()),
    path('auth/login', ConsoleLoginView.as_view()),
    path('auth/refresh', ConsoleRefreshView.as_view()),
    path('auth/profile', ConsoleProfileView.as_view()),
    path('dashboard/stats', DashboardView.as_view()),
    path('dashboard/player', PlayerDashboardView.as_view()),
    path('permissions', PermissionTreeView.as_view()),
    path('config/deposit-rules', DepositRulesView.as_view()),
    path('config/commission', CommissionConfigView.as_view()),
    path('config/withdraw', WithdrawConfigView.as_view()),
    path('config/checkin', CheckinRuleConfigView.as_view()),
    path('config/provider-pass', ProviderPassConfigView.as_view()),
    path('', include(router.urls)),
]
