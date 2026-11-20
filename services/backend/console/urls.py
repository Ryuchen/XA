from engagement.kook_console import KookPointsConfigView
from .wecom_dispatch import WecomConfigView
from wallet.deposit_applications import (StaffDepositApplicationListView, StaffDepositApplicationProcessView,
                                         StaffDepositApplicationRejectView, StaffDepositApplicationRemindView)
from club_accounts.initial_otp_views import InitialAdminOtpView
from stores.platform_initial_otp import PlatformInitialOtpGrantView
from users.views import QualificationOptionsView
from club_accounts.provider_retirement_views import ProviderRetirementView
from orders.continuations import ContinuationReviewView, ContinuationRetryView
from orders.continuation_staff_claim import StaffContinuationClaimView
from stores.platform_withdrawals import PlatformWithdrawalStoresView, PlatformWithdrawalListView, PlatformWithdrawalActionView
from stores.platform_pass_repayment import PlatformPassRepaymentRegistrationView, PlatformPassRepaymentActionView
from stores.platform_referral_funding import (PlatformReferralFundingView, PlatformReferralFundingVoidView,
    PlatformReferralFundingQuoteView, PlatformReferralFundingReleaseView, PlatformReferralFundingReleaseVoidView,
    PlatformReferralFundingRequestStatusView)
from stores.platform_points_funding import (PlatformOrderFundingQueueView, PlatformOrderFundingStatusView,
                                            PlatformPointsFundingActionView, PlatformPointsFundingRecoveryView)
from stores.platform_pass_repayment_read import PlatformPassRepaymentReconciliationView
from stores.platform_pass_repayment_recovery import PlatformPassRepaymentRecoveryView, PlatformPassRepaymentVoidView
from stores.platform_store_management import PlatformStoreManagementView, PlatformStoreManagementDetailView, PlatformStoreManagementResumeView
from stores.platform_media import PlatformStoreBackgroundUploadView, PlatformStoreLogoUploadView
from stores.platform_domains import PlatformStoreDomainsView
from stores.platform_license_refund_vip import PlatformLicenseRefundVipReviewView, PlatformLicenseRefundVipResolutionView
from stores.platform_store_payroll import StorePayrollView
from stores.platform_salary import PlatformSalaryView, PlatformSalaryConfirmView, PlatformSalaryCloseView, PlatformSalaryRosterView, PlatformLedgerView
from stores.platform_salary_adjustments import PlatformSalaryAdjustmentView
from stores.platform_salary_separation import PlatformSalaryPaymentView, PlatformSalarySeparationView
from stores.platform_salary_supplements import PlatformSalarySupplementView, PlatformSalarySupplementPaymentView
from stores.platform_license_views import (PlatformStoreLicensesView, PlatformLicenseVoidView, PlatformLicenseActivatorView, PlatformLicenseActivatorListView,
                                           PlatformLicenseSettingsView, PlatformStoreLicenseStatusView,
                                           PlatformLicenseTierFeaturesView, PlatformLicenseTierFeatureView,
                                           PlatformLicenseReissueView, PlatformLicenseNoticesView, PlatformLicenseNoticeReadView)
from stores.license_activation_views import StoreLicenseActivationView
from stores.platform_license_billing_views import PlatformLicenseContractView, PlatformLicenseReceiptView, PlatformLicenseReceiptVoidView
from stores.platform_license_points import (PlatformLicenseAddonResolutionView, PlatformLicensePointsResolutionView,
                                            PlatformLicensePointsReviewView)
from stores.platform_store_setup import PlatformSetupView, PlatformSetupCapacityView, PlatformSetupGrabWindowView, PlatformSetupClocksView, PlatformSetupDepositView, PlatformSetupWithdrawalView, PlatformSetupConfirmView, PlatformSetupMonthlyCutoverView, PlatformOpeningReadinessView
from stores.platform_store_setup import PlatformSetupGameCommissionView, PlatformSetupMembershipView, PlatformSetupOrderTimeoutView, PlatformSetupReservationDeadlinesView, PlatformSetupRoomConfirmationView
from stores.platform_wechat import PlatformWechatBindingView
from stores.platform_store_setup import PlatformSetupPointsTermsView, PlatformSetupPointsUsageView
from stores.platform_store_setup import PlatformSetupAccountFieldsView
from stores.platform_store_setup import PlatformSetupProviderPassView
from stores.platform_setup_catalog import PlatformSetupCatalogView
from stores.platform_license_kook import (PlatformKookLicenseReviewView, PlatformKookLicenseResolutionView,
    PlatformKookManualReviewView, PlatformKookManualResolutionView)
from stores.platform_license_gift_vip import PlatformGiftVipReviewView, PlatformGiftVipResolutionView
from stores.platform_license_achievement import PlatformAchievementReviewView, PlatformAchievementResolutionView
from club_accounts.otp_views import OtpSetupView
from stores.platform_views import PlatformPasswordLoginView, PlatformRefreshView, PlatformProfileView, PlatformStoresView, PlatformDashboardView, PlatformOtpSetupView, PlatformAccountsView, PlatformAccountDetailView, PlatformAccountAuditView, PlatformPassRefundShortfallsView, PlatformPassRefundShortfallDetailView
from users.h5_login import ConsoleH5StartView, ConsoleH5CallbackView, ConsoleH5StatusView
from .deposit_rules import DepositRulesView, DepositTargetsView
from .settlement_clocks import SettlementClocksView
from .capacity_config import CapacityConfigView
from .grab_config import GrabConfigView
from orders.grab_review import GrabReviewActionView, GrabReviewView
from .reservation_deadlines import ReservationDeadlineConfigView
from .room_confirmation_config import RoomConfirmationConfigView
from .points_redemption_config import PointsRedemptionConfigView
from .points_usage import PointsUsageConfigView
from .account_fields import OrderAccountFieldsView, OrderAccountFieldsTargetsView
from .game_commission import GameCommissionCatalogView, GameCommissionConfigView, GameCommissionCoverageView, GameCommissionPreviewView
from .addon_configuration import ServiceAddonConfigurationView, ServiceAddonPriceOptionsView
from orders.room_objections import RoomObjectionQueueView, RoomObjectionResolveView
from orders.addon_intents import StaffOrderAddonIntentView, StaffOrderAddonPaymentView
from orders.addon_refund import StaffAddonRefundProposalView, StaffAddonRefundExecuteView
from orders.addon_handover import StaffAddonHandoverView
from orders.addon_completion import StaffAddonCompletionView
from orders.legacy_room_completion import LegacyRoomCompletionView
from orders.legacy_clock_cutover import LegacyClockCutoverView
from orders.room_completion_review import RoomCompletionReviewQueueView, RoomCompletionReviewView, RoomConfirmationProblemQueueView
from site_messages.review_views import NoticeReviewActionView, NoticeReviewListView
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .auth import ConsoleLoginView, ConsoleProfileView, ConsoleRefreshView
from .dual_auth import ConsolePasswordLoginView
from .options import FormOptionsView
from .dictionaries import DictionaryCatalogView, DictionaryItemViewSet, DictionaryOptionsView
from .config_readiness import SetupEvidenceConfirmationView
from .payroll import (
    PayrollConfigView, PayrollConfirmView,
    PayrollEmployeesView, PayrollPreviewView, PayrollRunsView, PayrollSourcesView, PayrollSeparationSettlementView,
    SalaryActivityViewSet, SalaryAssignmentViewSet, SalaryPlanViewSet,
)
from stores.license_operating import OperatingCostViewSet, OperatingRoiView
from .provider_pass import NoPassDelayView, ProviderPassAvailabilityView, ProviderPassConfigView, ProviderPassPurchaseViewSet
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
from orders.segment_reports import SegmentReportReviewView
from orders.segment_settlement_plan import SegmentSettlementPlanView
from orders.segment_refund_preview import SegmentRefundPreviewView
from engagement.kook_console import KookPointsStatusView

from .provider_registration import ProviderRegistrationViewSet

from orders.aftersale import AfterSaleViewSet
router = DefaultRouter()
router.register('dictionary-items', DictionaryItemViewSet, basename='dictionary-item')
router.register('salary-plans', SalaryPlanViewSet, basename='salary-plan')
router.register('salary-assignments', SalaryAssignmentViewSet, basename='salary-assignment')
router.register('salary-activities', SalaryActivityViewSet, basename='salary-activity')
router.register('operating-costs', OperatingCostViewSet, basename='operating-cost')
router.register('aftersales', AfterSaleViewSet, basename='aftersale')
router.register('provider-registrations', ProviderRegistrationViewSet, basename='provider-registration')
from engagement.console import (RewardViewSet, ClaimViewSet, BroadcastRuleViewSet, BroadcastViewSet, ProviderNamingViewSet,
    ContentViewSet, TierViewSet, PackageViewSet, CampaignViewSet, OperationConfigView, VipResumePreviewView, VipUpgradeReviewViewSet, ExternalPointsView, ContentImageUploadView, GiftBenefitViewSet)
router.register('gift-benefits', GiftBenefitViewSet, basename='gift-benefits')
router.register('club-rewards', RewardViewSet, basename='club-reward')
router.register('vip-upgrade-reviews', VipUpgradeReviewViewSet, basename='vip-upgrade-review')
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
from orders.timeout_views import OrderTimeoutConfigView, OrderTimeoutConfigHistoryView
from orders.payment_fee_policy_views import PaymentFeePolicyView, PaymentFeePolicyHistoryView, PaymentFeePolicyPreviewView
from .referral_config import ReferralConfigView

urlpatterns = [
    path('config/referral-draft/', ReferralConfigView.as_view()),
    path('providers/<int:account_id>/retirement/', ProviderRetirementView.as_view()),
    path('continuations/', ContinuationReviewView.as_view()),
    path('orders/<int:order_id>/continuation/', StaffContinuationClaimView.as_view()),
    path('continuations/<int:claim_id>/', ContinuationReviewView.as_view()),
    path('continuations/<int:claim_id>/retry/', ContinuationRetryView.as_view()),
    path('notice-reviews/', NoticeReviewListView.as_view()),
    path('notice-reviews/<str:kind>/<int:notice_id>/', NoticeReviewActionView.as_view()),
    path('orders/<int:order_id>/addons/intents/', StaffOrderAddonIntentView.as_view()),
    path('orders/<int:order_id>/addons/intents/<int:intent_id>/pay/', StaffOrderAddonPaymentView.as_view()),
    path('orders/<int:order_id>/addons/payments/<int:payment_id>/refund-proposals/', StaffAddonRefundProposalView.as_view()),
    path('orders/<int:order_id>/addons/payments/<int:payment_id>/handovers/', StaffAddonHandoverView.as_view()),
    path('orders/<int:order_id>/addons/payments/<int:payment_id>/completion/', StaffAddonCompletionView.as_view()),
    path('orders/<int:order_id>/addons/payments/<int:payment_id>/refund-proposals/<int:proposal_id>/execute/', StaffAddonRefundExecuteView.as_view()),
    path('dictionaries/', DictionaryCatalogView.as_view()),
    path('dictionaries/setup-confirmation/<str:key>/', SetupEvidenceConfirmationView.as_view()),
    path('dictionary-options/', DictionaryOptionsView.as_view()),
    path('payroll/config/', PayrollConfigView.as_view()),
    path('payroll/preview/', PayrollPreviewView.as_view()),
    path('payroll/confirm/', PayrollConfirmView.as_view()),
    path('payroll/runs/', PayrollRunsView.as_view()),
    path('payroll/separation-settlements/', PayrollSeparationSettlementView.as_view()),
    path('payroll/employees/', PayrollEmployeesView.as_view()),
    path('payroll/sources/', PayrollSourcesView.as_view()),
    path('operating-roi/', OperatingRoiView.as_view()),
    path('qualification-options/', QualificationOptionsView.as_view()),
    path('order-traffic/', TrafficConfigView.as_view()),
    path('config/order-timeout/', OrderTimeoutConfigView.as_view()),
    path('config/order-timeout/history/', OrderTimeoutConfigHistoryView.as_view()),
    path('config/payment-fee/', PaymentFeePolicyView.as_view()),
    path('config/payment-fee/history/', PaymentFeePolicyHistoryView.as_view()),
    path('config/payment-fee/preview/', PaymentFeePolicyPreviewView.as_view()),
    path('kook-points-status/',KookPointsStatusView.as_view()),
    path('config/kook-points/', KookPointsConfigView.as_view()),
    path('config/kook-dispatch/', KookConfigView.as_view()),
    path('config/wecom-dispatch/', WecomConfigView.as_view()),
    path('config/kook-dispatch/check/', KookChannelCheckView.as_view()),
    path('config/kook-dispatch/preview/', KookTemplatePreviewView.as_view()),
    path('config/kook-dispatch/test/', KookProbeView.as_view()),
    path('club-config/', OperationConfigView.as_view()),
    path('club-config/vip-resume-preview/', VipResumePreviewView.as_view()),
    path('external-points/', ExternalPointsView.as_view()),
    path('club-content-image/', ContentImageUploadView.as_view()),
    path('catalog-image/', CatalogImageUploadView.as_view()),
    path('options/<slug:resource>/', FormOptionsView.as_view()),
    path('auth/login', ConsoleLoginView.as_view()),
    path('auth/password-login', ConsolePasswordLoginView.as_view()),
    path('auth/initial-otp', InitialAdminOtpView.as_view()),
    path('auth/h5-login/start', ConsoleH5StartView.as_view()),
    path('auth/h5-login/callback', ConsoleH5CallbackView.as_view()),
    path('auth/h5-login/status', ConsoleH5StatusView.as_view()),
    path('auth/refresh', ConsoleRefreshView.as_view()),
    path('auth/profile', ConsoleProfileView.as_view()),
    path('platform/auth/login', PlatformPasswordLoginView.as_view()),
    path('platform/auth/wechat/binding', PlatformWechatBindingView.as_view()),
    path('platform/auth/refresh', PlatformRefreshView.as_view()),
    path('platform/profile', PlatformProfileView.as_view()),
    path('platform/stores', PlatformStoresView.as_view()),
    path('platform/store-management/logo-upload', PlatformStoreLogoUploadView.as_view()),
    path('platform/salaries', PlatformSalaryView.as_view()),
    path('platform/salaries/close', PlatformSalaryCloseView.as_view()),
    path('platform/salary-adjustments', PlatformSalaryAdjustmentView.as_view()),
    path('platform/salary-payments', PlatformSalaryPaymentView.as_view()),
    path('platform/salary-supplements', PlatformSalarySupplementView.as_view()),
    path('platform/salary-supplements/<uuid:supplement_id>/payments', PlatformSalarySupplementPaymentView.as_view()),
    path('platform/salaries/roster', PlatformSalaryRosterView.as_view()),
    path('platform/salaries/<uuid:account_id>', PlatformSalaryView.as_view()),
    path('platform/salaries/<uuid:account_id>/confirm', PlatformSalaryConfirmView.as_view()),
    path('platform/salaries/<uuid:account_id>/separation', PlatformSalarySeparationView.as_view()),
    path('platform/ledger', PlatformLedgerView.as_view()),
    path('platform/store-management', PlatformStoreManagementView.as_view()),
    path('platform/store-management/<int:pk>', PlatformStoreManagementDetailView.as_view()),
    path('platform/store-management/<int:pk>/background-upload', PlatformStoreBackgroundUploadView.as_view()),
    path('platform/store-management/<int:pk>/domains', PlatformStoreDomainsView.as_view()),
    path('platform/store-management/<int:pk>/initial-otp', PlatformInitialOtpGrantView.as_view()),
    path('platform/store-management/<int:pk>/licenses', PlatformStoreLicensesView.as_view()),
    path('platform/store-management/<int:pk>/license-activators', PlatformLicenseActivatorListView.as_view()),
    path('platform/store-management/<int:pk>/license-activators/<int:account_id>', PlatformLicenseActivatorView.as_view()),
    path('platform/store-management/<int:pk>/license-status', PlatformStoreLicenseStatusView.as_view()),
    path('platform/store-management/<int:pk>/license-points-review', PlatformLicensePointsReviewView.as_view()),
    path('platform/store-management/<int:pk>/license-kook-review', PlatformKookLicenseReviewView.as_view()),
    path('platform/store-management/<int:pk>/kook-manual-review', PlatformKookManualReviewView.as_view()),
    path('platform/store-management/<int:pk>/kook-manual-review/<int:review_id>/resolve', PlatformKookManualResolutionView.as_view()),
    path('platform/store-management/<int:pk>/license-kook-review/<int:source_id>/resolve', PlatformKookLicenseResolutionView.as_view()),
    path('platform/store-management/<int:pk>/license-gift-vip-review', PlatformGiftVipReviewView.as_view()),
    path('platform/store-management/<int:pk>/license-gift-vip-review/<int:decision_id>/resolve', PlatformGiftVipResolutionView.as_view()),
    path('platform/store-management/<int:pk>/license-achievement-review', PlatformAchievementReviewView.as_view()),
    path('platform/store-management/<int:pk>/license-achievement-review/<int:event_id>/resolve', PlatformAchievementResolutionView.as_view()),
    path('platform/store-management/<int:pk>/license-vip-review', PlatformLicenseRefundVipReviewView.as_view()),
    path('platform/store-management/<int:pk>/license-vip-review/<int:decision_id>/resolve', PlatformLicenseRefundVipResolutionView.as_view()),
    path('platform/store-management/<int:pk>/license-points-review/<int:decision_id>/resolve', PlatformLicensePointsResolutionView.as_view()),
    path('platform/store-management/<int:pk>/license-points-review/addons/<int:consumption_id>/resolve', PlatformLicenseAddonResolutionView.as_view()),
    path('platform/license-settings', PlatformLicenseSettingsView.as_view()),
    path('platform/license-tiers/<str:tier>/features', PlatformLicenseTierFeaturesView.as_view()),
    path('platform/license-tiers/<str:tier>/features/<str:feature>', PlatformLicenseTierFeatureView.as_view()),
    path('platform/license-notices', PlatformLicenseNoticesView.as_view()),
    path('platform/license-notices/<int:notice_id>/read', PlatformLicenseNoticeReadView.as_view()),
    path('platform/licenses/<uuid:code_id>/void', PlatformLicenseVoidView.as_view()),
    path('platform/licenses/<uuid:code_id>/reissue', PlatformLicenseReissueView.as_view()),
    path('platform/license-contracts/<uuid:contract_id>', PlatformLicenseContractView.as_view()),
    path('platform/license-contracts/<uuid:contract_id>/receipts', PlatformLicenseReceiptView.as_view()),
    path('platform/license-receipts/<uuid:receipt_id>/void', PlatformLicenseReceiptVoidView.as_view()),
    path('license/activate', StoreLicenseActivationView.as_view()),
    path('platform/store-management/<int:pk>/initialize', PlatformStoreManagementResumeView.as_view()),
    path('platform/store-management/<int:pk>/setup', PlatformSetupView.as_view()),
    path('platform/store-management/<int:pk>/setup/readiness', PlatformOpeningReadinessView.as_view()),
    path('platform/store-management/<int:pk>/setup/monthly-cutover', PlatformSetupMonthlyCutoverView.as_view()),
    path('platform/store-management/<int:pk>/setup/capacity', PlatformSetupCapacityView.as_view()),
    path('platform/store-management/<int:pk>/setup/grab-window', PlatformSetupGrabWindowView.as_view()),
    path('platform/store-management/<int:pk>/setup/provider-pass', PlatformSetupProviderPassView.as_view()),
    path('platform/store-management/<int:pk>/setup/order-timeout', PlatformSetupOrderTimeoutView.as_view()),
    path('platform/store-management/<int:pk>/setup/game-commissions/<int:game_id>', PlatformSetupGameCommissionView.as_view()),
    path('platform/store-management/<int:pk>/setup/catalog/<str:kind>', PlatformSetupCatalogView.as_view()),
    path('platform/store-management/<int:pk>/setup/membership/<str:kind>', PlatformSetupMembershipView.as_view()),
    path('platform/store-management/<int:pk>/setup/account-fields/<str:scope>/<int:target_id>', PlatformSetupAccountFieldsView.as_view()),
    path('platform/store-management/<int:pk>/setup/reservation-deadlines', PlatformSetupReservationDeadlinesView.as_view()),
    path('platform/store-management/<int:pk>/setup/room-confirmation', PlatformSetupRoomConfirmationView.as_view()),
    path('platform/store-management/<int:pk>/setup/points-terms', PlatformSetupPointsTermsView.as_view()),
    path('platform/store-management/<int:pk>/setup/points-usage', PlatformSetupPointsUsageView.as_view()),
    path('platform/store-management/<int:pk>/setup/clocks', PlatformSetupClocksView.as_view()),
    path('platform/store-management/<int:pk>/setup/deposit/<str:kind>', PlatformSetupDepositView.as_view()),
    path('platform/store-management/<int:pk>/setup/withdraw', PlatformSetupWithdrawalView.as_view()),
    path('platform/store-management/<int:pk>/setup/confirm/<str:key>', PlatformSetupConfirmView.as_view()),
    path('platform/store-payroll/<slug:slug>/<str:resource>', StorePayrollView.as_view()),
    path('platform/store-payroll/<slug:slug>/<str:resource>/<int:plan_id>', StorePayrollView.as_view()),
    path('platform/dashboard', PlatformDashboardView.as_view()),
    path('platform/withdrawals', PlatformWithdrawalStoresView.as_view()),
    path('platform/withdrawals/<slug:slug>', PlatformWithdrawalListView.as_view()),
    path('platform/withdrawals/<slug:slug>/<int:pk>/<str:action>', PlatformWithdrawalActionView.as_view()),
    path('platform/pass-refund-shortfalls', PlatformPassRefundShortfallsView.as_view()),
    path('platform/pass-refund-shortfalls/<slug:slug>', PlatformPassRefundShortfallDetailView.as_view()),
    path('platform/pass-refund-repayments/<slug:slug>/register', PlatformPassRepaymentRegistrationView.as_view()),
    path('platform/pass-refund-repayments/<slug:slug>/reconciliation', PlatformPassRepaymentReconciliationView.as_view()),
    path('platform/pass-refund-repayments/<slug:slug>/recover/<str:target_type>/<uuid:target_id>', PlatformPassRepaymentRecoveryView.as_view()),
    path('platform/pass-refund-repayments/<slug:slug>/void/<str:target_type>/<uuid:target_id>', PlatformPassRepaymentVoidView.as_view()),
    path('platform/pass-refund-repayments/<slug:slug>/<uuid:claim_id>/points-allocation', PlatformPointsFundingActionView.as_view()),
    path('platform/pass-refund-repayments/<slug:slug>/<uuid:claim_id>/service-allocation', PlatformPointsFundingActionView.as_view()),
    path('platform/pass-refund-repayments/<slug:slug>/<uuid:claim_id>/referral-refund-allocation', PlatformReferralFundingView.as_view()),
    path('platform/referral-funding/<slug:slug>/orders/<int:order_id>/quote', PlatformReferralFundingQuoteView.as_view()),
    path('platform/referral-funding/<slug:slug>/<uuid:operation_id>/void', PlatformReferralFundingVoidView.as_view()),
    path('platform/referral-funding/<slug:slug>/<uuid:operation_id>/release', PlatformReferralFundingReleaseView.as_view()),
    path('platform/referral-funding-release/<slug:slug>/<uuid:release_operation_id>/void', PlatformReferralFundingReleaseVoidView.as_view()),
    path('platform/referral-funding/<slug:slug>/request-status', PlatformReferralFundingRequestStatusView.as_view()),
    path('platform/points-funding/<slug:slug>/<uuid:operation_id>/<str:action>', PlatformPointsFundingRecoveryView.as_view()),
    path('platform/points-funding/<slug:slug>/orders', PlatformOrderFundingQueueView.as_view()),
    path('platform/points-funding/<slug:slug>/orders/<int:order_id>', PlatformOrderFundingStatusView.as_view()),
    path('platform/pass-refund-repayments/<slug:slug>/<uuid:claim_id>/<str:kind>', PlatformPassRepaymentActionView.as_view()),
    path('platform/auth/otp-setup', PlatformOtpSetupView.as_view()),
    path('platform/accounts', PlatformAccountsView.as_view()),
    path('platform/accounts/<uuid:account_id>', PlatformAccountDetailView.as_view()),
    path('platform/accounts/<uuid:account_id>/audit', PlatformAccountAuditView.as_view()),
    path('auth/otp-setup', OtpSetupView.as_view()),
    path('dashboard/stats', DashboardView.as_view()),
    path('dashboard/player', PlayerDashboardView.as_view()),
    path('permissions', PermissionTreeView.as_view()),
    path('config/deposit-rules', DepositRulesView.as_view()),
    path('config/settlement-clocks', SettlementClocksView.as_view()),
    path('config/provider-capacity/', CapacityConfigView.as_view()),
    path('config/grab-window/', GrabConfigView.as_view()),
    path('grab-reviews/', GrabReviewView.as_view()),
    path('grab-reviews/<int:order_id>/', GrabReviewView.as_view()),
    path('grab-reviews/<int:order_id>/actions/', GrabReviewActionView.as_view()),
    path('config/reservation-deadlines/', ReservationDeadlineConfigView.as_view()),
    path('config/room-confirmation/', RoomConfirmationConfigView.as_view()),
    path('config/points-redemption/', PointsRedemptionConfigView.as_view()),
    path('config/order-account-fields/targets/', OrderAccountFieldsTargetsView.as_view()),
    path('config/order-account-fields/<str:scope>/<int:pk>/', OrderAccountFieldsView.as_view()),
    path('config/points-usage/', PointsUsageConfigView.as_view()),
    path('config/game-commissions/', GameCommissionCatalogView.as_view()),
    path('config/game-commissions/coverage/', GameCommissionCoverageView.as_view()),
    path('config/game-commissions/<int:game_id>/', GameCommissionConfigView.as_view()),
    path('config/game-commissions/<int:game_id>/preview/<int:service_id>/', GameCommissionPreviewView.as_view()),
    path('config/service-addons/<int:service_id>/', ServiceAddonConfigurationView.as_view()),
    path('config/service-addons/<int:service_id>/<int:addon_id>/', ServiceAddonConfigurationView.as_view()),
    path('config/service-addons/<int:service_id>/<int:addon_id>/price-options/', ServiceAddonPriceOptionsView.as_view()),
    path('room-objections/', RoomObjectionQueueView.as_view()),
    path('orders/<int:order_id>/legacy-room-completion/', LegacyRoomCompletionView.as_view()),
    path('orders/<int:order_id>/legacy-clock-cutover/', LegacyClockCutoverView.as_view()),
    path('room-objections/<int:objection_id>/resolve/', RoomObjectionResolveView.as_view()),
    path('room-completion-requests/', RoomCompletionReviewQueueView.as_view()),
    path('room-completion-requests/<int:completion_id>/review/', RoomCompletionReviewView.as_view()),
    path('room-confirmation-problems/', RoomConfirmationProblemQueueView.as_view()),
    path('config/deposit-targets', DepositTargetsView.as_view()),
    path('config/commission', CommissionConfigView.as_view()),
    path('config/withdraw', WithdrawConfigView.as_view()),
    path('config/checkin', CheckinRuleConfigView.as_view()),
    path('config/provider-pass', ProviderPassConfigView.as_view()),
    path('config/provider-pass/availability', ProviderPassAvailabilityView.as_view()),
    path('config/provider-pass/no-pass-delay', NoPassDelayView.as_view()),
    path('orders/<int:order_id>/segments/<int:segment_id>/report-review/', SegmentReportReviewView.as_view()),
    path('orders/<int:order_id>/segment-settlement-plan/', SegmentSettlementPlanView.as_view()),
    path('orders/<int:order_id>/segment-refund-preview/', SegmentRefundPreviewView.as_view()),
    path('deposit-applications/', StaffDepositApplicationListView.as_view()),
    path('deposit-applications/<int:pk>/process/', StaffDepositApplicationProcessView.as_view()),
    path('deposit-applications/<int:pk>/reject/', StaffDepositApplicationRejectView.as_view()),
    path('deposit-applications/<int:pk>/remind/', StaffDepositApplicationRemindView.as_view()),
    path('', include(router.urls)),
]
