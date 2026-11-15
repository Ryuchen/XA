import { useEffect, useState } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router'
import './App.css'
import { PrototypeShell } from '@/components/prototype/PrototypeShell'
import { AdminPrototypeShell } from '@/components/admin/AdminPrototypeShell'
import { AdminDesignShell } from '@/components/admin/AdminDesignShell'
import { adminPathFor, type AdminNavigablePage } from '@/components/admin/admin-navigation'
import { ProviderArea } from '@/pages/provider/ProviderArea'
import { adminAnnouncementsScenarioOptions, adminAuditionLinksScenarioOptions, adminAuditionSignupsScenarioOptions, adminBannersScenarioOptions, adminBroadcastRecordsScenarioOptions, adminBroadcastRulesScenarioOptions, adminChatScenarioOptions, adminCommissionScenarioOptions, adminContentsScenarioOptions, adminDashboardScenarioOptions, adminDisposesScenarioOptions, adminGameplaysScenarioOptions, adminLevelsScenarioOptions, adminOperationConfigScenarioOptions, adminLotteriesScenarioOptions, adminRewardsScenarioOptions, adminOrderDetailScenarioOptions, adminOrdersScenarioOptions, adminPassesScenarioOptions, adminPaymentFeeScenarioOptions, adminProviderDetailScenarioOptions, adminProvidersScenarioOptions, adminRechargesScenarioOptions, adminRegistrationDetailScenarioOptions, adminRegistrationsScenarioOptions, adminReportsScenarioOptions, adminReservationsScenarioOptions, adminServicesScenarioOptions, adminTransactionsScenarioOptions, adminWalletsScenarioOptions, adminWithdrawScenarioOptions, type AdminAnnouncementsScenario, type AdminAuditionLinksScenario, type AdminAuditionSignupsScenario, type AdminBannersScenario, type AdminBroadcastRecordsScenario, type AdminBroadcastRulesScenario, type AdminChatScenario, type AdminCommissionScenario, type AdminContentsScenario, type AdminDashboardScenario, type AdminDisposesScenario, type AdminGameplaysScenario, type AdminLevelsScenario, type AdminLoginScenario, type AdminOperationConfigScenario, type AdminRewardsScenario, type AdminOrderDetailScenario, type AdminOrdersScenario, type AdminPassesScenario, type AdminPaymentFeeScenario, type AdminProviderDetailScenario, type AdminProvidersScenario, type AdminRechargesScenario, type AdminRegistrationDetailScenario, type AdminRegistrationsScenario, type AdminReportsScenario, type AdminReservationsScenario, type AdminServicesScenario, type AdminTransactionsScenario, type AdminWalletsScenario, type AdminWithdrawScenario } from '@/components/admin/admin-config'
import { adminBossesScenarioOptions, adminClaimsScenarioOptions, adminLoginScenarioOptions, type AdminBossesScenario, type AdminClaimsScenario } from '@/components/admin/admin-config'
import { AdminClaimsPage } from '@/pages/admin/AdminClaimsPage'
import { AdminLoginPage } from '@/pages/admin/AdminLoginPage'
import { AdminStoresPage } from '@/pages/admin/AdminStoresPage'
import { AdminChatPage } from '@/pages/admin/AdminChatPage'
import { AdminReportsPage } from '@/pages/admin/AdminReportsPage'
import { AdminCommissionPage } from '@/pages/admin/AdminCommissionPage'
import { AdminServicesPage } from '@/pages/admin/AdminServicesPage'
import { AdminGameplaysPage } from '@/pages/admin/AdminGameplaysPage'
import { AdminBannersPage } from '@/pages/admin/AdminBannersPage'
import { AdminAnnouncementsPage } from '@/pages/admin/AdminAnnouncementsPage'
import { AdminContentsPage } from '@/pages/admin/AdminContentsPage'
import { AdminBroadcastRulesPage } from '@/pages/admin/AdminBroadcastRulesPage'
import { AdminBroadcastRecordsPage } from '@/pages/admin/AdminBroadcastRecordsPage'
import { AdminOperationConfigPage } from '@/pages/admin/AdminOperationConfigPage'
import { AdminRewardsPage } from '@/pages/admin/AdminRewardsPage'
import { AdminLotteriesPage } from '@/pages/admin/AdminLotteriesPage'
import type { AdminLotteriesScenario } from '@/components/admin/admin-config'
import { AdminDashboardPage } from '@/pages/admin/AdminDashboardPage'
import { AdminOrdersPage } from '@/pages/admin/AdminOrdersPage'
import { AdminOrderDetailPage } from '@/pages/admin/AdminOrderDetailPage'
import { AdminBossesPage } from '@/pages/admin/AdminBossesPage'
import { AdminProvidersPage } from '@/pages/admin/AdminProvidersPage'
import { AdminProviderDetailPage } from '@/pages/admin/AdminProviderDetailPage'
import { AdminRegistrationsPage } from '@/pages/admin/AdminRegistrationsPage'
import { AdminRegistrationDetailPage } from '@/pages/admin/AdminRegistrationDetailPage'
import { AdminLevelsPage } from '@/pages/admin/AdminLevelsPage'
import { AdminPassesPage } from '@/pages/admin/AdminPassesPage'
import { AdminDisposesPage } from '@/pages/admin/AdminDisposesPage'
import { AdminAuditionLinksPage } from '@/pages/admin/AdminAuditionLinksPage'
import { AdminAuditionSignupsPage } from '@/pages/admin/AdminAuditionSignupsPage'
import { AdminWithdrawPage } from '@/pages/admin/AdminWithdrawPage'
import { AdminWalletPage } from '@/pages/admin/AdminWalletPage'
import { AdminTransactionsPage } from '@/pages/admin/AdminTransactionsPage'
import { AdminRechargesPage } from '@/pages/admin/AdminRechargesPage'
import { AdminReservationsPage } from '@/pages/admin/AdminReservationsPage'
import { AdminPaymentFeePage } from '@/pages/admin/AdminPaymentFeePage'
import { adminOrders, type AdminOrder } from '@/pages/admin/admin-order-data'
import { adminProviders, type AdminProvider } from '@/pages/admin/admin-provider-data'
import { adminRegistrations, type AdminRegistration } from '@/pages/admin/admin-registration-data'
import {
  catalogScenarioOptions,
  chatScenarioOptions,
  checkoutScenarioOptions,
  companionDetailScenarioOptions,
  companionScenarioOptions,
  companionPickerScenarioOptions,
  couponsScenarioOptions,
  membershipScenarioOptions,
  pointsScenarioOptions,
  lotteryScenarioOptions,
  rewardClaimScenarioOptions,
  favoritesScenarioOptions,
  checkinScenarioOptions,
  achievementsScenarioOptions,
  rankingsScenarioOptions,
  announcementsScenarioOptions,
  announcementDetailScenarioOptions,
  clubContentScenarioOptions,
  clubContentDetailScenarioOptions,
  activitiesScenarioOptions,
  activityDetailScenarioOptions,
  guideContentScenarioOptions,
  guideContentDetailScenarioOptions,
  messageDetailScenarioOptions,
  auditionScenarioOptions,
  giftsScenarioOptions,
  homeScenarioOptions,
  loginScenarioOptions,
  messagesScenarioOptions,
  mineScenarioOptions,
  orderDetailScenarioOptions,
  ordersScenarioOptions,
  profileScenarioOptions,
  reservationsScenarioOptions,
  reservationDetailScenarioOptions,
  reservationCreateScenarioOptions,
  serviceDetailScenarioOptions,
  securityScenarioOptions,
  supportScenarioOptions,
  walletScenarioOptions,
  type BossPage,
  type LoginScenario,
} from '@/components/prototype/prototype-config'
import { CheckoutPage, type CheckoutScenario } from '@/pages/boss/CheckoutPage'
import { CompanionsPage, type CompanionScenario } from '@/pages/boss/CompanionsPage'
import { CompanionPickerPage, type CompanionPickerScenario } from '@/pages/boss/CompanionPickerPage'
import { CompanionDetailPage, type CompanionDetailScenario } from '@/pages/boss/CompanionDetailPage'
import { HomePage, type HomeScenario } from '@/pages/boss/HomePage'
import { LoginPage } from '@/pages/boss/LoginPage'
import { OrderDetailPage, type OrderDetailScenario } from '@/pages/boss/OrderDetailPage'
import { OrdersPage, type OrdersScenario } from '@/pages/boss/OrdersPage'
import { bossOrders, type BossOrder } from '@/pages/boss/order-data'
import { ServiceCatalogPage, type CatalogScenario, type CatalogService } from '@/pages/boss/ServiceCatalogPage'
import { ServiceDetailPage, type ServiceDetailScenario } from '@/pages/boss/ServiceDetailPage'
import { defaultCatalogService } from '@/pages/boss/catalog-data'
import { WalletPage, type WalletScenario } from '@/pages/boss/WalletPage'
import { GiftsPage, type GiftsScenario } from '@/pages/boss/GiftsPage'
import { MinePage, type MineScenario } from '@/pages/boss/MinePage'
import { MessagesPage, type MessagesScenario } from '@/pages/boss/MessagesPage'
import { ChatPage, type ChatScenario } from '@/pages/boss/ChatPage'
import { SupportPage, type SupportScenario } from '@/pages/boss/SupportPage'
import { SecurityPage, type SecurityScenario } from '@/pages/boss/SecurityPage'
import { ProfilePage, type ProfileScenario } from '@/pages/boss/ProfilePage'
import { ReservationsPage, type ReservationsScenario } from '@/pages/boss/ReservationsPage'
import { ReservationDetailPage, type ReservationDetailScenario } from '@/pages/boss/ReservationDetailPage'
import { ReservationCreatePage, type ReservationCreateScenario } from '@/pages/boss/ReservationCreatePage'
import { CouponsPage, type CouponsScenario } from '@/pages/boss/CouponsPage'
import { MembershipPage, type MembershipScenario } from '@/pages/boss/MembershipPage'
import { PointsPage, type PointsScenario } from '@/pages/boss/PointsPage'
import { LotteryPage, type LotteryScenario } from '@/pages/boss/LotteryPage'
import { RewardClaimPage, type RewardClaimScenario } from '@/pages/boss/RewardClaimPage'
import { FavoritesPage, type FavoritesScenario } from '@/pages/boss/FavoritesPage'
import { CheckinPage, type CheckinScenario } from '@/pages/boss/CheckinPage'
import { AchievementsPage, type AchievementsScenario } from '@/pages/boss/AchievementsPage'
import { RankingsPage, type RankingsScenario } from '@/pages/boss/RankingsPage'
import { AnnouncementsPage, AnnouncementDetailPage, type AnnouncementsScenario, type AnnouncementDetailScenario } from '@/pages/boss/AnnouncementsPage'
import { bossAnnouncements, type Announcement } from '@/pages/boss/announcement-data'
import { ClubContentPage, ClubContentDetailPage, type ClubContentScenario, type ClubContentDetailScenario } from '@/pages/boss/ClubContentPage'
import { clubRuleItems, type ClubContentItem } from '@/pages/boss/club-content-data'
import { ActivitiesPage, ActivityDetailPage, type ActivitiesScenario, type ActivityDetailScenario } from '@/pages/boss/ActivitiesPage'
import { clubActivities, type ClubActivity } from '@/pages/boss/activity-data'
import { GuideContentPage, GuideContentDetailPage, type GuideContentScenario, type GuideContentDetailScenario } from '@/pages/boss/GuideContentPage'
import { getGuideItems, type GuideContentItem } from '@/pages/boss/guide-content-data'
import { MessageDetailPage, type MessageDetailScenario } from '@/pages/boss/MessageDetailPage'
import { initialNotices, type Notice } from '@/pages/boss/message-data'
import { AuditionPage, type AuditionScenario } from '@/pages/boss/AuditionPage'
import { bossReservations, type Reservation } from '@/pages/boss/reservation-data'

export default function App() {
  const [dark, setDark] = useState(true)
  const [adminLoginScenario, setAdminLoginScenario] = useState<AdminLoginScenario>('default')
  const [adminDashboardScenario, setAdminDashboardScenario] = useState<AdminDashboardScenario>('default')
  const [adminOrdersScenario, setAdminOrdersScenario] = useState<AdminOrdersScenario>('default')
  const [adminOrderSearchKeyword, setAdminOrderSearchKeyword] = useState('')
  const [adminChatScenario, setAdminChatScenario] = useState<AdminChatScenario>('default')
  const [adminReportsScenario, setAdminReportsScenario] = useState<AdminReportsScenario>('default')
  const [adminCommissionScenario, setAdminCommissionScenario] = useState<AdminCommissionScenario>('default')
  const [adminServicesScenario, setAdminServicesScenario] = useState<AdminServicesScenario>('default')
  const [adminServicesGameplayScope, setAdminServicesGameplayScope] = useState<string | undefined>(undefined)
  const [adminGameplaysScenario, setAdminGameplaysScenario] = useState<AdminGameplaysScenario>('default')
  const [adminBannersScenario, setAdminBannersScenario] = useState<AdminBannersScenario>('default')
  const [adminAnnouncementsScenario, setAdminAnnouncementsScenario] = useState<AdminAnnouncementsScenario>('default')
  const [adminContentsScenario, setAdminContentsScenario] = useState<AdminContentsScenario>('default')
  const [adminBroadcastRulesScenario, setAdminBroadcastRulesScenario] = useState<AdminBroadcastRulesScenario>('default')
  const [adminBroadcastRecordsScenario, setAdminBroadcastRecordsScenario] = useState<AdminBroadcastRecordsScenario>('default')
  const [adminOperationConfigScenario, setAdminOperationConfigScenario] = useState<AdminOperationConfigScenario>('default')
  const [adminRewardsScenario, setAdminRewardsScenario] = useState<AdminRewardsScenario>('default')
  const [adminLotteriesScenario, setAdminLotteriesScenario] = useState<AdminLotteriesScenario>('default')
  const [adminClaimsScenario, setAdminClaimsScenario] = useState<AdminClaimsScenario>('default')
  const [adminOrderDetailScenario, setAdminOrderDetailScenario] = useState<AdminOrderDetailScenario>('default')
  const [selectedAdminOrder, setSelectedAdminOrder] = useState<AdminOrder>(adminOrders[0])
  const [adminBossesScenario, setAdminBossesScenario] = useState<AdminBossesScenario>('default')
  const [adminProvidersScenario, setAdminProvidersScenario] = useState<AdminProvidersScenario>('default')
  const [createdAdminProviders, setCreatedAdminProviders] = useState<AdminProvider[]>([])
  const [adminProviderDetailScenario, setAdminProviderDetailScenario] = useState<AdminProviderDetailScenario>('default')
  const [selectedAdminProvider, setSelectedAdminProvider] = useState<AdminProvider>(adminProviders[0])
  const [adminRegistrationsScenario, setAdminRegistrationsScenario] = useState<AdminRegistrationsScenario>('default')
  const [adminRegistrationItems, setAdminRegistrationItems] = useState<AdminRegistration[]>(adminRegistrations)
  const [adminRegistrationDetailScenario, setAdminRegistrationDetailScenario] = useState<AdminRegistrationDetailScenario>('default')
  const [selectedAdminRegistration, setSelectedAdminRegistration] = useState<AdminRegistration>(adminRegistrations[0])
  const [adminLevelsScenario, setAdminLevelsScenario] = useState<AdminLevelsScenario>('default')
  const [adminPassesScenario, setAdminPassesScenario] = useState<AdminPassesScenario>('default')
  const [adminDisposesScenario, setAdminDisposesScenario] = useState<AdminDisposesScenario>('default')
  const [adminAuditionLinksScenario, setAdminAuditionLinksScenario] = useState<AdminAuditionLinksScenario>('default')
  const [adminAuditionSignupsScenario, setAdminAuditionSignupsScenario] = useState<AdminAuditionSignupsScenario>('default')
  const [adminWithdrawScenario, setAdminWithdrawScenario] = useState<AdminWithdrawScenario>('default')
  const [adminWalletsScenario, setAdminWalletsScenario] = useState<AdminWalletsScenario>('default')
  const [adminProviderWalletsScenario, setAdminProviderWalletsScenario] = useState<AdminWalletsScenario>('default')
  const [adminTransactionsScenario, setAdminTransactionsScenario] = useState<AdminTransactionsScenario>('default')
  const [adminTransactionWalletScope, setAdminTransactionWalletScope] = useState<number | undefined>(undefined)
  const [adminRechargesScenario, setAdminRechargesScenario] = useState<AdminRechargesScenario>('default')
  const [adminRechargeWalletScope, setAdminRechargeWalletScope] = useState<number | undefined>(undefined)
  const [adminReservationsScenario, setAdminReservationsScenario] = useState<AdminReservationsScenario>('default')
  const [adminPaymentFeeScenario, setAdminPaymentFeeScenario] = useState<AdminPaymentFeeScenario>('default')
  const [loginScenario, setLoginScenario] = useState<LoginScenario>('default')
  const [homeScenario, setHomeScenario] = useState<HomeScenario>('default')
  const [companionScenario, setCompanionScenario] = useState<CompanionScenario>('default')
  const [companionPickerScenario, setCompanionPickerScenario] = useState<CompanionPickerScenario>('default')
  const [companionPickerOrigin, setCompanionPickerOrigin] = useState<'service' | 'checkout'>('service')
  const [companionDetailScenario, setCompanionDetailScenario] = useState<CompanionDetailScenario>('default')
  const [catalogScenario, setCatalogScenario] = useState<CatalogScenario>('default')
  const [serviceDetailScenario, setServiceDetailScenario] = useState<ServiceDetailScenario>('default')
  const [checkoutScenario, setCheckoutScenario] = useState<CheckoutScenario>('default')
  const [ordersScenario, setOrdersScenario] = useState<OrdersScenario>('default')
  const [orderDetailScenario, setOrderDetailScenario] = useState<OrderDetailScenario>('default')
  const [walletScenario, setWalletScenario] = useState<WalletScenario>('default')
  const [giftsScenario, setGiftsScenario] = useState<GiftsScenario>('default')
  const [mineScenario, setMineScenario] = useState<MineScenario>('default')
  const [messagesScenario, setMessagesScenario] = useState<MessagesScenario>('default')
  const [messageDetailScenario, setMessageDetailScenario] = useState<MessageDetailScenario>('default')
  const [selectedNotice, setSelectedNotice] = useState<Notice>(initialNotices[0])
  const [readNoticeIds, setReadNoticeIds] = useState<number[]>(() => initialNotices.filter(item => item.isRead).map(item => item.id))
  const [auditionScenario, setAuditionScenario] = useState<AuditionScenario>('default')
  const [chatScenario, setChatScenario] = useState<ChatScenario>('default')
  const [chatOrderNo, setChatOrderNo] = useState(bossOrders[3].orderNo)
  const [supportScenario, setSupportScenario] = useState<SupportScenario>('default')
  const [supportOrigin, setSupportOrigin] = useState<BossPage>('mine')
  const [supportOrderNo, setSupportOrderNo] = useState(bossOrders[0].orderNo)
  const [securityScenario, setSecurityScenario] = useState<SecurityScenario>('default')
  const [securityOrigin, setSecurityOrigin] = useState<BossPage>('mine')
  const [profileScenario, setProfileScenario] = useState<ProfileScenario>('default')
  const [reservationsScenario, setReservationsScenario] = useState<ReservationsScenario>('default')
  const [reservationDetailScenario, setReservationDetailScenario] = useState<ReservationDetailScenario>('default')
  const [reservationCreateScenario, setReservationCreateScenario] = useState<ReservationCreateScenario>('default')
  const [couponsScenario, setCouponsScenario] = useState<CouponsScenario>('default')
  const [membershipScenario, setMembershipScenario] = useState<MembershipScenario>('default')
  const [pointsScenario, setPointsScenario] = useState<PointsScenario>('default')
  const [lotteryScenario, setLotteryScenario] = useState<LotteryScenario>('default')
  const [rewardClaimScenario, setRewardClaimScenario] = useState<RewardClaimScenario>('default')
  const [favoritesScenario, setFavoritesScenario] = useState<FavoritesScenario>('default')
  const [checkinScenario, setCheckinScenario] = useState<CheckinScenario>('default')
  const [achievementsScenario, setAchievementsScenario] = useState<AchievementsScenario>('default')
  const [rankingsScenario, setRankingsScenario] = useState<RankingsScenario>('default')
  const [announcementsScenario, setAnnouncementsScenario] = useState<AnnouncementsScenario>('default')
  const [announcementDetailScenario, setAnnouncementDetailScenario] = useState<AnnouncementDetailScenario>('default')
  const [announcementOrigin, setAnnouncementOrigin] = useState<'home' | 'mine' | 'announcements'>('announcements')
  const [selectedAnnouncement, setSelectedAnnouncement] = useState<Announcement>(bossAnnouncements[0])
  const [clubContentScenario, setClubContentScenario] = useState<ClubContentScenario>('default')
  const [clubContentDetailScenario, setClubContentDetailScenario] = useState<ClubContentDetailScenario>('default')
  const [selectedClubContent, setSelectedClubContent] = useState<ClubContentItem>(clubRuleItems[0])
  const [activitiesScenario, setActivitiesScenario] = useState<ActivitiesScenario>('default')
  const [activityDetailScenario, setActivityDetailScenario] = useState<ActivityDetailScenario>('default')
  const [activityOrigin, setActivityOrigin] = useState<'home' | 'activities'>('activities')
  const [selectedActivity, setSelectedActivity] = useState<ClubActivity>(clubActivities[0])
  const [rightsScenario, setRightsScenario] = useState<GuideContentScenario>('default')
  const [rightsDetailScenario, setRightsDetailScenario] = useState<GuideContentDetailScenario>('default')
  const [pointRulesScenario, setPointRulesScenario] = useState<GuideContentScenario>('default')
  const [pointRulesDetailScenario, setPointRulesDetailScenario] = useState<GuideContentDetailScenario>('default')
  const [rightsOrigin, setRightsOrigin] = useState<'home' | 'mine' | 'membership'>('mine')
  const [selectedRightsContent, setSelectedRightsContent] = useState<GuideContentItem>(getGuideItems('RIGHTS')[0])
  const [selectedPointRuleContent, setSelectedPointRuleContent] = useState<GuideContentItem>(getGuideItems('POINT_RULES')[0])
  const [selectedReservation, setSelectedReservation] = useState<Reservation>(bossReservations[0])
  const [selectedService, setSelectedService] = useState<CatalogService>(defaultCatalogService)
  const [selectedOrder, setSelectedOrder] = useState<BossOrder>(bossOrders[0])
  const [checkoutUnitPrice, setCheckoutUnitPrice] = useState(defaultCatalogService.price)
  const [checkoutVariantName, setCheckoutVariantName] = useState('体验 1 局')
  const [checkoutProviderIds, setCheckoutProviderIds] = useState<number[] | undefined>(undefined)
  const [giftRecipient, setGiftRecipient] = useState('')
  const location = useLocation()
  const navigate = useNavigate()
  const currentPage: BossPage = location.pathname === '/home' ? 'home' : location.pathname === '/companions' ? 'companions' : location.pathname === '/companion-picker' ? 'companion-picker' : location.pathname === '/companion-detail' ? 'companion-detail' : location.pathname === '/catalog' ? 'catalog' : location.pathname === '/service-detail' ? 'service-detail' : location.pathname === '/checkout' ? 'checkout' : location.pathname === '/orders' ? 'orders' : location.pathname === '/order-detail' ? 'order-detail' : location.pathname === '/wallet' ? 'wallet' : location.pathname === '/gifts' ? 'gifts' : location.pathname === '/mine' ? 'mine' : location.pathname === '/messages' ? 'messages' : location.pathname === '/chat' ? 'chat' : location.pathname === '/support' ? 'support' : location.pathname === '/security' ? 'security' : location.pathname === '/profile' ? 'profile' : location.pathname === '/reservations' ? 'reservations' : location.pathname === '/reservation-detail' ? 'reservation-detail' : location.pathname === '/reservation-create' ? 'reservation-create' : location.pathname === '/coupons' ? 'coupons' : location.pathname === '/membership' ? 'membership' : location.pathname === '/points' ? 'points' : location.pathname === '/lottery' ? 'lottery' : location.pathname === '/reward-claim' ? 'reward-claim' : location.pathname === '/favorites' ? 'favorites' : location.pathname === '/checkin' ? 'checkin' : location.pathname === '/achievements' ? 'achievements' : location.pathname === '/rankings' ? 'rankings' : location.pathname === '/announcements' ? 'announcements' : location.pathname === '/announcement-detail' ? 'announcement-detail' : location.pathname === '/club-content' ? 'club-content' : location.pathname === '/club-content-detail' ? 'club-content-detail' : location.pathname === '/activities' ? 'activities' : location.pathname === '/activity-detail' ? 'activity-detail' : location.pathname === '/rights' ? 'rights' : location.pathname === '/rights-detail' ? 'rights-detail' : location.pathname === '/point-rules' ? 'point-rules' : location.pathname === '/point-rules-detail' ? 'point-rules-detail' : location.pathname === '/message-detail' ? 'message-detail' : location.pathname === '/audition' ? 'audition' : 'login'
  const scenario = currentPage === 'home' ? homeScenario : currentPage === 'companions' ? companionScenario : currentPage === 'companion-picker' ? companionPickerScenario : currentPage === 'companion-detail' ? companionDetailScenario : currentPage === 'catalog' ? catalogScenario : currentPage === 'service-detail' ? serviceDetailScenario : currentPage === 'checkout' ? checkoutScenario : currentPage === 'orders' ? ordersScenario : currentPage === 'order-detail' ? orderDetailScenario : currentPage === 'wallet' ? walletScenario : currentPage === 'gifts' ? giftsScenario : currentPage === 'mine' ? mineScenario : currentPage === 'messages' ? messagesScenario : currentPage === 'chat' ? chatScenario : currentPage === 'support' ? supportScenario : currentPage === 'security' ? securityScenario : currentPage === 'profile' ? profileScenario : currentPage === 'reservations' ? reservationsScenario : currentPage === 'reservation-detail' ? reservationDetailScenario : currentPage === 'reservation-create' ? reservationCreateScenario : currentPage === 'coupons' ? couponsScenario : currentPage === 'membership' ? membershipScenario : currentPage === 'points' ? pointsScenario : currentPage === 'lottery' ? lotteryScenario : currentPage === 'reward-claim' ? rewardClaimScenario : currentPage === 'favorites' ? favoritesScenario : currentPage === 'checkin' ? checkinScenario : currentPage === 'achievements' ? achievementsScenario : currentPage === 'rankings' ? rankingsScenario : currentPage === 'announcements' ? announcementsScenario : currentPage === 'announcement-detail' ? announcementDetailScenario : currentPage === 'club-content' ? clubContentScenario : currentPage === 'club-content-detail' ? clubContentDetailScenario : currentPage === 'activities' ? activitiesScenario : currentPage === 'activity-detail' ? activityDetailScenario : currentPage === 'rights' ? rightsScenario : currentPage === 'rights-detail' ? rightsDetailScenario : currentPage === 'point-rules' ? pointRulesScenario : currentPage === 'point-rules-detail' ? pointRulesDetailScenario : currentPage === 'message-detail' ? messageDetailScenario : currentPage === 'audition' ? auditionScenario : loginScenario
  const scenarioOptions = currentPage === 'home' ? homeScenarioOptions : currentPage === 'companions' ? companionScenarioOptions : currentPage === 'companion-picker' ? companionPickerScenarioOptions : currentPage === 'companion-detail' ? companionDetailScenarioOptions : currentPage === 'catalog' ? catalogScenarioOptions : currentPage === 'service-detail' ? serviceDetailScenarioOptions : currentPage === 'checkout' ? checkoutScenarioOptions : currentPage === 'orders' ? ordersScenarioOptions : currentPage === 'order-detail' ? orderDetailScenarioOptions : currentPage === 'wallet' ? walletScenarioOptions : currentPage === 'gifts' ? giftsScenarioOptions : currentPage === 'mine' ? mineScenarioOptions : currentPage === 'messages' ? messagesScenarioOptions : currentPage === 'chat' ? chatScenarioOptions : currentPage === 'support' ? supportScenarioOptions : currentPage === 'security' ? securityScenarioOptions : currentPage === 'profile' ? profileScenarioOptions : currentPage === 'reservations' ? reservationsScenarioOptions : currentPage === 'reservation-detail' ? reservationDetailScenarioOptions : currentPage === 'reservation-create' ? reservationCreateScenarioOptions : currentPage === 'coupons' ? couponsScenarioOptions : currentPage === 'membership' ? membershipScenarioOptions : currentPage === 'points' ? pointsScenarioOptions : currentPage === 'lottery' ? lotteryScenarioOptions : currentPage === 'reward-claim' ? rewardClaimScenarioOptions : currentPage === 'favorites' ? favoritesScenarioOptions : currentPage === 'checkin' ? checkinScenarioOptions : currentPage === 'achievements' ? achievementsScenarioOptions : currentPage === 'rankings' ? rankingsScenarioOptions : currentPage === 'announcements' ? announcementsScenarioOptions : currentPage === 'announcement-detail' ? announcementDetailScenarioOptions : currentPage === 'club-content' ? clubContentScenarioOptions : currentPage === 'club-content-detail' ? clubContentDetailScenarioOptions : currentPage === 'activities' ? activitiesScenarioOptions : currentPage === 'activity-detail' ? activityDetailScenarioOptions : currentPage === 'rights' || currentPage === 'point-rules' ? guideContentScenarioOptions : currentPage === 'rights-detail' || currentPage === 'point-rules-detail' ? guideContentDetailScenarioOptions : currentPage === 'message-detail' ? messageDetailScenarioOptions : currentPage === 'audition' ? auditionScenarioOptions : loginScenarioOptions

  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
    document.documentElement.style.colorScheme = dark ? 'dark' : 'light'
  }, [dark])

  const ordersFromDock = location.pathname === '/orders' && Boolean((location.state as { fromDock?: boolean } | null)?.fromDock)

  function navigateAdmin(page: AdminNavigablePage) {
    if (page === 'recharges') setAdminRechargeWalletScope(undefined)
    if (page === 'orders') setAdminOrderSearchKeyword('')
    if (page === 'services') setAdminServicesGameplayScope(undefined)
    navigate(adminPathFor(page))
  }

  function openAdminTransactions(walletId?: number) {
    setAdminTransactionWalletScope(walletId)
    setAdminTransactionsScenario('default')
    navigate('/admin-transactions')
  }

  function openAdminRecharges(walletId?: number) {
    setAdminRechargeWalletScope(walletId)
    setAdminRechargesScenario('default')
    navigate('/admin-recharges')
  }

  function openAdminOrders(queue: 'all' | 'staff' | 'aftersale' = 'all') {
    setAdminOrderSearchKeyword('')
    setAdminOrdersScenario(queue === 'staff' ? 'staff' : queue === 'aftersale' ? 'aftersale' : 'default')
    navigate('/admin-orders')
  }

  function openAdminOrderSearch(orderNo: string) {
    setAdminOrderSearchKeyword(orderNo)
    setAdminOrdersScenario('default')
    navigate('/admin-orders')
  }

  function openAdminOrder(order: AdminOrder, nextScenario?: AdminOrderDetailScenario) {
    const scenario = nextScenario || (order.aftersale && order.status !== 'CANCELLED' ? 'aftersale' : order.paymentStatus === 'REFUNDED' || order.paymentStatus === 'PARTIALLY_REFUNDED' ? 'refunded' : order.status === 'PENDING' ? 'pending' : order.status === 'IN_SERVICE' ? 'in-service' : 'default')
    setSelectedAdminOrder(order)
    setAdminOrderDetailScenario(scenario)
    navigate('/admin-order-detail')
  }

  function openAdminProvider(provider: AdminProvider, nextScenario?: AdminProviderDetailScenario) {
    setSelectedAdminProvider(provider)
    setAdminProviderDetailScenario(nextScenario || (!provider.verified ? 'unverified' : provider.status === 'BUSY' ? 'busy' : 'default'))
    navigate('/admin-provider-detail')
  }

  function openAdminRegistration(registration: AdminRegistration, nextScenario?: AdminRegistrationDetailScenario) {
    setSelectedAdminRegistration(registration)
    setAdminRegistrationDetailScenario(nextScenario || (registration.status === 'APPROVED' ? 'approved' : registration.status === 'REJECTED' ? 'rejected' : registration.depositMode === 'EARNINGS' ? 'earnings' : 'default'))
    navigate('/admin-registration-detail')
  }

  function changeAdminRegistrationDetailScenario(next: AdminRegistrationDetailScenario) {
    if (next === 'default') setSelectedAdminRegistration(adminRegistrationItems.find(item => item.status === 'PENDING' && item.depositMode === 'CASH') || adminRegistrationItems[0])
    else if (next === 'earnings') setSelectedAdminRegistration(adminRegistrationItems.find(item => item.status === 'PENDING' && item.depositMode === 'EARNINGS') || adminRegistrationItems[0])
    else if (next === 'approved') setSelectedAdminRegistration(adminRegistrationItems.find(item => item.status === 'APPROVED') || adminRegistrationItems[0])
    else if (next === 'rejected') setSelectedAdminRegistration(adminRegistrationItems.find(item => item.status === 'REJECTED') || adminRegistrationItems[0])
    setAdminRegistrationDetailScenario(next)
  }

  function completeAdminRegistrationReview(registration: AdminRegistration) {
    setAdminRegistrationItems(items => items.map(item => item.id === registration.id ? registration : item))
    setSelectedAdminRegistration(registration)
  }

  if (location.pathname.startsWith('/provider')) {
    return <ProviderArea dark={dark} onThemeChange={setDark} />
  }

  if (location.pathname.startsWith('/admin')) {
    const changeDashboardScenario = (next: AdminDashboardScenario) => {
      setAdminDashboardScenario(next)
      if (next === 'refreshing') window.setTimeout(() => setAdminDashboardScenario('default'), 900)
    }
    const preview = <T extends string,>(value: T, options: readonly { value: string; label: string; description: string }[], onChange: (value: T) => void) => ({
      scenario: value,
      scenarioOptions: options,
      onScenarioChange: (next: string) => onChange(next as T),
    })
    const adminPreviews = {
      '/admin-login': preview(adminLoginScenario, adminLoginScenarioOptions, setAdminLoginScenario),
      '/admin-stores': preview('default', [{ value: 'default', label: '平台门店总览', description: '只展示租户元数据、独立入口与生命周期，不展示店内业务明细。' }], () => undefined),
      '/admin-dashboard': preview(adminDashboardScenario, adminDashboardScenarioOptions, changeDashboardScenario),
      '/admin-orders': preview(adminOrdersScenario, adminOrdersScenarioOptions, setAdminOrdersScenario),
      '/admin-order-detail': preview(adminOrderDetailScenario, adminOrderDetailScenarioOptions, setAdminOrderDetailScenario),
      '/admin-reservations': preview(adminReservationsScenario, adminReservationsScenarioOptions, setAdminReservationsScenario),
      '/admin-chat': preview(adminChatScenario, adminChatScenarioOptions, setAdminChatScenario),
      '/admin-reports': preview(adminReportsScenario, adminReportsScenarioOptions, setAdminReportsScenario),
      '/admin-commission': preview(adminCommissionScenario, adminCommissionScenarioOptions, setAdminCommissionScenario),
      '/admin-services': preview(adminServicesScenario, adminServicesScenarioOptions, setAdminServicesScenario),
      '/admin-gameplays': preview(adminGameplaysScenario, adminGameplaysScenarioOptions, setAdminGameplaysScenario),
      '/admin-banners': preview(adminBannersScenario, adminBannersScenarioOptions, setAdminBannersScenario),
      '/admin-announcements': preview(adminAnnouncementsScenario, adminAnnouncementsScenarioOptions, setAdminAnnouncementsScenario),
      '/admin-contents': preview(adminContentsScenario, adminContentsScenarioOptions, setAdminContentsScenario),
      '/admin-broadcast-rules': preview(adminBroadcastRulesScenario, adminBroadcastRulesScenarioOptions, setAdminBroadcastRulesScenario),
      '/admin-broadcast-records': preview(adminBroadcastRecordsScenario, adminBroadcastRecordsScenarioOptions, setAdminBroadcastRecordsScenario),
      '/admin-rewards': preview(adminRewardsScenario, adminRewardsScenarioOptions, setAdminRewardsScenario),
      '/admin-lotteries': preview(adminLotteriesScenario, adminLotteriesScenarioOptions, setAdminLotteriesScenario),
      '/admin-claims': preview(adminClaimsScenario, adminClaimsScenarioOptions, setAdminClaimsScenario),
      '/admin-operation-config': preview(adminOperationConfigScenario, adminOperationConfigScenarioOptions, setAdminOperationConfigScenario),
      '/admin-bosses': preview(adminBossesScenario, adminBossesScenarioOptions, setAdminBossesScenario),
      '/admin-providers': preview(adminProvidersScenario, adminProvidersScenarioOptions, setAdminProvidersScenario),
      '/admin-provider-detail': preview(adminProviderDetailScenario, adminProviderDetailScenarioOptions, setAdminProviderDetailScenario),
      '/admin-registrations': preview(adminRegistrationsScenario, adminRegistrationsScenarioOptions, setAdminRegistrationsScenario),
      '/admin-registration-detail': preview(adminRegistrationDetailScenario, adminRegistrationDetailScenarioOptions, changeAdminRegistrationDetailScenario),
      '/admin-levels': preview(adminLevelsScenario, adminLevelsScenarioOptions, setAdminLevelsScenario),
      '/admin-passes': preview(adminPassesScenario, adminPassesScenarioOptions, setAdminPassesScenario),
      '/admin-disposes': preview(adminDisposesScenario, adminDisposesScenarioOptions, setAdminDisposesScenario),
      '/admin-auditions': preview(adminAuditionLinksScenario, adminAuditionLinksScenarioOptions, setAdminAuditionLinksScenario),
      '/admin-audition-signups': preview(adminAuditionSignupsScenario, adminAuditionSignupsScenarioOptions, setAdminAuditionSignupsScenario),
      '/admin-withdraw': preview(adminWithdrawScenario, adminWithdrawScenarioOptions, setAdminWithdrawScenario),
      '/admin-wallets': preview(adminWalletsScenario, adminWalletsScenarioOptions, setAdminWalletsScenario),
      '/admin-provider-wallets': preview(adminProviderWalletsScenario, adminWalletsScenarioOptions, setAdminProviderWalletsScenario),
      '/admin-transactions': preview(adminTransactionsScenario, adminTransactionsScenarioOptions, setAdminTransactionsScenario),
      '/admin-recharges': preview(adminRechargesScenario, adminRechargesScenarioOptions, setAdminRechargesScenario),
      '/admin-payment-fee': preview(adminPaymentFeeScenario, adminPaymentFeeScenarioOptions, setAdminPaymentFeeScenario),
    }
    const adminPreview = adminPreviews[location.pathname as keyof typeof adminPreviews] || adminPreviews['/admin-dashboard']
    return (
      <AdminDesignShell dark={dark} onThemeChange={setDark} {...adminPreview}>
      <Routes>
        <Route path="/admin-login" element={<AdminLoginPage key={adminLoginScenario} scenario={adminLoginScenario} dark={dark} onScenarioChange={setAdminLoginScenario} onThemeChange={setDark} onBoss={() => navigate('/login')} onSuccess={() => { setAdminDashboardScenario('default'); navigate('/admin-dashboard') }} />} />
        <Route path="/admin-stores" element={<AdminPrototypeShell dark={dark} page="stores" scenario="default" scenarioOptions={[{ value: 'default', label: '平台门店总览', description: '只展示租户元数据、独立入口与生命周期，不展示店内业务明细。' }]} onThemeChange={setDark} onScenarioChange={() => undefined} onNavigate={navigateAdmin} onBoss={() => navigate('/login')} onLogout={() => { setAdminLoginScenario('default'); navigate('/admin-login') }}><AdminStoresPage /></AdminPrototypeShell>} />
        <Route path="/admin-dashboard" element={<AdminPrototypeShell dark={dark} scenario={adminDashboardScenario} scenarioOptions={adminDashboardScenarioOptions} onThemeChange={setDark} onScenarioChange={changeDashboardScenario} onNavigate={navigateAdmin} onBoss={() => navigate('/login')} onLogout={() => { setAdminLoginScenario('default'); navigate('/admin-login') }}><AdminDashboardPage scenario={adminDashboardScenario} onScenarioChange={changeDashboardScenario} onOrders={openAdminOrders} onOpenOrder={orderNo => { const order = adminOrders.find(item => item.orderNo === orderNo); if (order) openAdminOrder(order) }} /></AdminPrototypeShell>} />
        <Route path="/admin-orders" element={<AdminPrototypeShell dark={dark} page="orders" scenario={adminOrdersScenario} scenarioOptions={adminOrdersScenarioOptions} onThemeChange={setDark} onScenarioChange={setAdminOrdersScenario} onNavigate={navigateAdmin} onBoss={() => navigate('/login')} onLogout={() => { setAdminLoginScenario('default'); navigate('/admin-login') }}><AdminOrdersPage key={`${adminOrdersScenario}-${adminOrderSearchKeyword}`} scenario={adminOrdersScenario} initialOrderKeyword={adminOrderSearchKeyword} onScenarioChange={setAdminOrdersScenario} onOpen={openAdminOrder} /></AdminPrototypeShell>} />
        <Route path="/admin-chat" element={<AdminPrototypeShell dark={dark} page="chat" scenario={adminChatScenario} scenarioOptions={adminChatScenarioOptions} onThemeChange={setDark} onScenarioChange={setAdminChatScenario} onNavigate={navigateAdmin} onBoss={() => navigate('/login')} onLogout={() => { setAdminLoginScenario('default'); navigate('/admin-login') }}><AdminChatPage key={adminChatScenario} scenario={adminChatScenario} onScenarioChange={setAdminChatScenario} onOpenOrder={openAdminOrderSearch} /></AdminPrototypeShell>} />
        <Route path="/admin-reports" element={<AdminPrototypeShell dark={dark} page="reports" scenario={adminReportsScenario} scenarioOptions={adminReportsScenarioOptions} onThemeChange={setDark} onScenarioChange={setAdminReportsScenario} onNavigate={navigateAdmin} onBoss={() => navigate('/login')} onLogout={() => { setAdminLoginScenario('default'); navigate('/admin-login') }}><AdminReportsPage key={adminReportsScenario} scenario={adminReportsScenario} onScenarioChange={setAdminReportsScenario} onOpenOrder={openAdminOrderSearch} onOpenCommission={() => navigate('/admin-commission')} /></AdminPrototypeShell>} />
        <Route path="/admin-commission" element={<AdminPrototypeShell dark={dark} page="commission" scenario={adminCommissionScenario} scenarioOptions={adminCommissionScenarioOptions} onThemeChange={setDark} onScenarioChange={setAdminCommissionScenario} onNavigate={navigateAdmin} onBoss={() => navigate('/login')} onLogout={() => { setAdminLoginScenario('default'); navigate('/admin-login') }}><AdminCommissionPage key={adminCommissionScenario} scenario={adminCommissionScenario} onScenarioChange={setAdminCommissionScenario} /></AdminPrototypeShell>} />
        <Route path="/admin-services" element={<AdminPrototypeShell dark={dark} page="services" scenario={adminServicesScenario} scenarioOptions={adminServicesScenarioOptions} onThemeChange={setDark} onScenarioChange={setAdminServicesScenario} onNavigate={navigateAdmin} onBoss={() => navigate('/login')} onLogout={() => { setAdminLoginScenario('default'); navigate('/admin-login') }}><AdminServicesPage key={`${adminServicesScenario}-${adminServicesGameplayScope ?? 'all'}`} scenario={adminServicesScenario} initialGameplay={adminServicesGameplayScope} onScenarioChange={setAdminServicesScenario} /></AdminPrototypeShell>} />
        <Route path="/admin-gameplays" element={<AdminPrototypeShell dark={dark} page="gameplays" scenario={adminGameplaysScenario} scenarioOptions={adminGameplaysScenarioOptions} onThemeChange={setDark} onScenarioChange={setAdminGameplaysScenario} onNavigate={navigateAdmin} onBoss={() => navigate('/login')} onLogout={() => { setAdminLoginScenario('default'); navigate('/admin-login') }}><AdminGameplaysPage key={adminGameplaysScenario} scenario={adminGameplaysScenario} onScenarioChange={setAdminGameplaysScenario} onConfigure={gameplay => { setAdminServicesGameplayScope(gameplay.name); setAdminServicesScenario('default'); navigate('/admin-services') }} /></AdminPrototypeShell>} />
        <Route path="/admin-banners" element={<AdminPrototypeShell dark={dark} page="banners" scenario={adminBannersScenario} scenarioOptions={adminBannersScenarioOptions} onThemeChange={setDark} onScenarioChange={setAdminBannersScenario} onNavigate={navigateAdmin} onBoss={() => navigate('/login')} onLogout={() => { setAdminLoginScenario('default'); navigate('/admin-login') }}><AdminBannersPage key={adminBannersScenario} scenario={adminBannersScenario} onScenarioChange={setAdminBannersScenario} /></AdminPrototypeShell>} />
        <Route path="/admin-announcements" element={<AdminPrototypeShell dark={dark} page="announcements" scenario={adminAnnouncementsScenario} scenarioOptions={adminAnnouncementsScenarioOptions} onThemeChange={setDark} onScenarioChange={setAdminAnnouncementsScenario} onNavigate={navigateAdmin} onBoss={() => navigate('/login')} onLogout={() => { setAdminLoginScenario('default'); navigate('/admin-login') }}><AdminAnnouncementsPage key={adminAnnouncementsScenario} scenario={adminAnnouncementsScenario} onScenarioChange={setAdminAnnouncementsScenario} /></AdminPrototypeShell>} />
        <Route path="/admin-contents" element={<AdminPrototypeShell dark={dark} page="contents" scenario={adminContentsScenario} scenarioOptions={adminContentsScenarioOptions} onThemeChange={setDark} onScenarioChange={setAdminContentsScenario} onNavigate={navigateAdmin} onBoss={() => navigate('/login')} onLogout={() => { setAdminLoginScenario('default'); navigate('/admin-login') }}><AdminContentsPage key={adminContentsScenario} scenario={adminContentsScenario} onScenarioChange={setAdminContentsScenario} /></AdminPrototypeShell>} />
        <Route path="/admin-broadcast-rules" element={<AdminPrototypeShell dark={dark} page="broadcast-rules" scenario={adminBroadcastRulesScenario} scenarioOptions={adminBroadcastRulesScenarioOptions} onThemeChange={setDark} onScenarioChange={setAdminBroadcastRulesScenario} onNavigate={navigateAdmin} onBoss={() => navigate('/login')} onLogout={() => { setAdminLoginScenario('default'); navigate('/admin-login') }}><AdminBroadcastRulesPage key={adminBroadcastRulesScenario} scenario={adminBroadcastRulesScenario} onScenarioChange={setAdminBroadcastRulesScenario} /></AdminPrototypeShell>} />
        <Route path="/admin-broadcast-records" element={<AdminPrototypeShell dark={dark} page="broadcast-records" scenario={adminBroadcastRecordsScenario} scenarioOptions={adminBroadcastRecordsScenarioOptions} onThemeChange={setDark} onScenarioChange={setAdminBroadcastRecordsScenario} onNavigate={navigateAdmin} onBoss={() => navigate('/login')} onLogout={() => { setAdminLoginScenario('default'); navigate('/admin-login') }}><AdminBroadcastRecordsPage key={adminBroadcastRecordsScenario} scenario={adminBroadcastRecordsScenario} onScenarioChange={setAdminBroadcastRecordsScenario} /></AdminPrototypeShell>} />
        <Route path="/admin-rewards" element={<AdminPrototypeShell dark={dark} page="rewards" scenario={adminRewardsScenario} scenarioOptions={adminRewardsScenarioOptions} onThemeChange={setDark} onScenarioChange={setAdminRewardsScenario} onNavigate={navigateAdmin} onBoss={() => navigate('/login')} onLogout={() => { setAdminLoginScenario('default'); navigate('/admin-login') }}><AdminRewardsPage key={adminRewardsScenario} scenario={adminRewardsScenario} onScenarioChange={setAdminRewardsScenario} /></AdminPrototypeShell>} />
        <Route path="/admin-lotteries" element={<AdminPrototypeShell dark={dark} page="lotteries" scenario={adminLotteriesScenario} scenarioOptions={adminLotteriesScenarioOptions} onThemeChange={setDark} onScenarioChange={setAdminLotteriesScenario} onNavigate={navigateAdmin} onBoss={() => navigate('/login')} onLogout={() => { setAdminLoginScenario('default'); navigate('/admin-login') }}><AdminLotteriesPage key={adminLotteriesScenario} scenario={adminLotteriesScenario} onScenarioChange={setAdminLotteriesScenario} /></AdminPrototypeShell>} />
        <Route path="/admin-claims" element={<AdminPrototypeShell dark={dark} page="claims" scenario={adminClaimsScenario} scenarioOptions={adminClaimsScenarioOptions} onThemeChange={setDark} onScenarioChange={setAdminClaimsScenario} onNavigate={navigateAdmin} onBoss={() => navigate('/login')} onLogout={() => { setAdminLoginScenario('default'); navigate('/admin-login') }}><AdminClaimsPage key={adminClaimsScenario} scenario={adminClaimsScenario} onScenarioChange={setAdminClaimsScenario} /></AdminPrototypeShell>} />
        <Route path="/admin-operation-config" element={<AdminPrototypeShell dark={dark} page="operation-config" scenario={adminOperationConfigScenario} scenarioOptions={adminOperationConfigScenarioOptions} onThemeChange={setDark} onScenarioChange={setAdminOperationConfigScenario} onNavigate={navigateAdmin} onBoss={() => navigate('/login')} onLogout={() => { setAdminLoginScenario('default'); navigate('/admin-login') }}><AdminOperationConfigPage key={adminOperationConfigScenario} scenario={adminOperationConfigScenario} onScenarioChange={setAdminOperationConfigScenario} /></AdminPrototypeShell>} />
        <Route path="/admin-reservations" element={<AdminPrototypeShell dark={dark} page="reservations" scenario={adminReservationsScenario} scenarioOptions={adminReservationsScenarioOptions} onThemeChange={setDark} onScenarioChange={setAdminReservationsScenario} onNavigate={navigateAdmin} onBoss={() => navigate('/login')} onLogout={() => { setAdminLoginScenario('default'); navigate('/admin-login') }}><AdminReservationsPage key={adminReservationsScenario} scenario={adminReservationsScenario} onScenarioChange={setAdminReservationsScenario} onOpenOrder={orderNo => { const order = adminOrders.find(item => item.orderNo === orderNo); if (order) openAdminOrder(order) }} /></AdminPrototypeShell>} />
        <Route path="/admin-order-detail" element={<AdminPrototypeShell dark={dark} page="order-detail" scenario={adminOrderDetailScenario} scenarioOptions={adminOrderDetailScenarioOptions} onThemeChange={setDark} onScenarioChange={setAdminOrderDetailScenario} onNavigate={navigateAdmin} onBoss={() => navigate('/login')} onLogout={() => { setAdminLoginScenario('default'); navigate('/admin-login') }}><AdminOrderDetailPage key={`${selectedAdminOrder.id}-${adminOrderDetailScenario}`} order={selectedAdminOrder} scenario={adminOrderDetailScenario} onScenarioChange={setAdminOrderDetailScenario} onBack={() => navigate('/admin-orders')} /></AdminPrototypeShell>} />
        <Route path="/admin-bosses" element={<AdminPrototypeShell dark={dark} page="bosses" scenario={adminBossesScenario} scenarioOptions={adminBossesScenarioOptions} onThemeChange={setDark} onScenarioChange={setAdminBossesScenario} onNavigate={navigateAdmin} onBoss={() => navigate('/login')} onLogout={() => { setAdminLoginScenario('default'); navigate('/admin-login') }}><AdminBossesPage key={`${adminBossesScenario}`} scenario={adminBossesScenario} onScenarioChange={setAdminBossesScenario} /></AdminPrototypeShell>} />
        <Route path="/admin-providers" element={<AdminPrototypeShell dark={dark} page="providers" scenario={adminProvidersScenario} scenarioOptions={adminProvidersScenarioOptions} onThemeChange={setDark} onScenarioChange={setAdminProvidersScenario} onNavigate={navigateAdmin} onBoss={() => navigate('/login')} onLogout={() => { setAdminLoginScenario('default'); navigate('/admin-login') }}><AdminProvidersPage key={adminProvidersScenario} scenario={adminProvidersScenario} createdProviders={createdAdminProviders} onScenarioChange={setAdminProvidersScenario} onProviderCreated={provider => setCreatedAdminProviders(items => [provider, ...items])} onOpen={openAdminProvider} /></AdminPrototypeShell>} />
        <Route path="/admin-provider-detail" element={<AdminPrototypeShell dark={dark} page="provider-detail" scenario={adminProviderDetailScenario} scenarioOptions={adminProviderDetailScenarioOptions} onThemeChange={setDark} onScenarioChange={setAdminProviderDetailScenario} onNavigate={navigateAdmin} onBoss={() => navigate('/login')} onLogout={() => { setAdminLoginScenario('default'); navigate('/admin-login') }}><AdminProviderDetailPage key={`${selectedAdminProvider.id}-${adminProviderDetailScenario}`} provider={selectedAdminProvider} scenario={adminProviderDetailScenario} onScenarioChange={setAdminProviderDetailScenario} onBack={() => navigate('/admin-providers')} /></AdminPrototypeShell>} />
        <Route path="/admin-registrations" element={<AdminPrototypeShell dark={dark} page="registrations" scenario={adminRegistrationsScenario} scenarioOptions={adminRegistrationsScenarioOptions} onThemeChange={setDark} onScenarioChange={setAdminRegistrationsScenario} onNavigate={navigateAdmin} onBoss={() => navigate('/login')} onLogout={() => { setAdminLoginScenario('default'); navigate('/admin-login') }}><AdminRegistrationsPage key={adminRegistrationsScenario} scenario={adminRegistrationsScenario} registrations={adminRegistrationItems} onScenarioChange={setAdminRegistrationsScenario} onOpen={openAdminRegistration} /></AdminPrototypeShell>} />
        <Route path="/admin-registration-detail" element={<AdminPrototypeShell dark={dark} page="registration-detail" scenario={adminRegistrationDetailScenario} scenarioOptions={adminRegistrationDetailScenarioOptions} onThemeChange={setDark} onScenarioChange={changeAdminRegistrationDetailScenario} onNavigate={navigateAdmin} onBoss={() => navigate('/login')} onLogout={() => { setAdminLoginScenario('default'); navigate('/admin-login') }}><AdminRegistrationDetailPage key={`${selectedAdminRegistration.id}-${adminRegistrationDetailScenario}`} registration={selectedAdminRegistration} scenario={adminRegistrationDetailScenario} onScenarioChange={changeAdminRegistrationDetailScenario} onReviewed={completeAdminRegistrationReview} onBack={() => navigate('/admin-registrations')} /></AdminPrototypeShell>} />
        <Route path="/admin-levels" element={<AdminPrototypeShell dark={dark} page="levels" scenario={adminLevelsScenario} scenarioOptions={adminLevelsScenarioOptions} onThemeChange={setDark} onScenarioChange={setAdminLevelsScenario} onNavigate={navigateAdmin} onBoss={() => navigate('/login')} onLogout={() => { setAdminLoginScenario('default'); navigate('/admin-login') }}><AdminLevelsPage key={adminLevelsScenario} scenario={adminLevelsScenario} onScenarioChange={setAdminLevelsScenario} /></AdminPrototypeShell>} />
        <Route path="/admin-passes" element={<AdminPrototypeShell dark={dark} page="passes" scenario={adminPassesScenario} scenarioOptions={adminPassesScenarioOptions} onThemeChange={setDark} onScenarioChange={setAdminPassesScenario} onNavigate={navigateAdmin} onBoss={() => navigate('/login')} onLogout={() => { setAdminLoginScenario('default'); navigate('/admin-login') }}><AdminPassesPage key={adminPassesScenario} scenario={adminPassesScenario} onScenarioChange={setAdminPassesScenario} /></AdminPrototypeShell>} />
        <Route path="/admin-disposes" element={<AdminPrototypeShell dark={dark} page="disposes" scenario={adminDisposesScenario} scenarioOptions={adminDisposesScenarioOptions} onThemeChange={setDark} onScenarioChange={setAdminDisposesScenario} onNavigate={navigateAdmin} onBoss={() => navigate('/login')} onLogout={() => { setAdminLoginScenario('default'); navigate('/admin-login') }}><AdminDisposesPage key={adminDisposesScenario} scenario={adminDisposesScenario} onScenarioChange={setAdminDisposesScenario} /></AdminPrototypeShell>} />
        <Route path="/admin-auditions" element={<AdminPrototypeShell dark={dark} page="auditions" scenario={adminAuditionLinksScenario} scenarioOptions={adminAuditionLinksScenarioOptions} onThemeChange={setDark} onScenarioChange={setAdminAuditionLinksScenario} onNavigate={navigateAdmin} onBoss={() => navigate('/login')} onLogout={() => { setAdminLoginScenario('default'); navigate('/admin-login') }}><AdminAuditionLinksPage key={adminAuditionLinksScenario} scenario={adminAuditionLinksScenario} onScenarioChange={setAdminAuditionLinksScenario} /></AdminPrototypeShell>} />
        <Route path="/admin-audition-signups" element={<AdminPrototypeShell dark={dark} page="audition-signups" scenario={adminAuditionSignupsScenario} scenarioOptions={adminAuditionSignupsScenarioOptions} onThemeChange={setDark} onScenarioChange={setAdminAuditionSignupsScenario} onNavigate={navigateAdmin} onBoss={() => navigate('/login')} onLogout={() => { setAdminLoginScenario('default'); navigate('/admin-login') }}><AdminAuditionSignupsPage key={adminAuditionSignupsScenario} scenario={adminAuditionSignupsScenario} onScenarioChange={setAdminAuditionSignupsScenario} /></AdminPrototypeShell>} />
        <Route path="/admin-withdraw" element={<AdminPrototypeShell dark={dark} page="withdraw" scenario={adminWithdrawScenario} scenarioOptions={adminWithdrawScenarioOptions} onThemeChange={setDark} onScenarioChange={setAdminWithdrawScenario} onNavigate={navigateAdmin} onBoss={() => navigate('/login')} onLogout={() => { setAdminLoginScenario('default'); navigate('/admin-login') }}><AdminWithdrawPage key={adminWithdrawScenario} scenario={adminWithdrawScenario} onScenarioChange={setAdminWithdrawScenario} /></AdminPrototypeShell>} />
        <Route path="/admin-wallets" element={<AdminPrototypeShell dark={dark} page="wallets" scenario={adminWalletsScenario} scenarioOptions={adminWalletsScenarioOptions} onThemeChange={setDark} onScenarioChange={setAdminWalletsScenario} onNavigate={navigateAdmin} onBoss={() => navigate('/login')} onLogout={() => { setAdminLoginScenario('default'); navigate('/admin-login') }}><AdminWalletPage key={`boss-${adminWalletsScenario}`} kind="BOSS" scenario={adminWalletsScenario} onScenarioChange={setAdminWalletsScenario} onOpenTransactions={openAdminTransactions} onOpenRecharges={openAdminRecharges} /></AdminPrototypeShell>} />
        <Route path="/admin-provider-wallets" element={<AdminPrototypeShell dark={dark} page="provider-wallets" scenario={adminProviderWalletsScenario} scenarioOptions={adminWalletsScenarioOptions} onThemeChange={setDark} onScenarioChange={setAdminProviderWalletsScenario} onNavigate={navigateAdmin} onBoss={() => navigate('/login')} onLogout={() => { setAdminLoginScenario('default'); navigate('/admin-login') }}><AdminWalletPage key={`provider-${adminProviderWalletsScenario}`} kind="PROVIDER" scenario={adminProviderWalletsScenario} onScenarioChange={setAdminProviderWalletsScenario} onOpenTransactions={openAdminTransactions} onOpenRecharges={openAdminRecharges} /></AdminPrototypeShell>} />
        <Route path="/admin-transactions" element={<AdminPrototypeShell dark={dark} page="transactions" scenario={adminTransactionsScenario} scenarioOptions={adminTransactionsScenarioOptions} onThemeChange={setDark} onScenarioChange={setAdminTransactionsScenario} onNavigate={navigateAdmin} onBoss={() => navigate('/login')} onLogout={() => { setAdminLoginScenario('default'); navigate('/admin-login') }}><AdminTransactionsPage key={`${adminTransactionsScenario}-${adminTransactionWalletScope ?? 'all'}`} scenario={adminTransactionsScenario} initialWalletId={adminTransactionWalletScope} onScenarioChange={next => { if (next === 'default') setAdminTransactionWalletScope(undefined); setAdminTransactionsScenario(next) }} onOpenOrder={orderNo => { const order = adminOrders.find(item => item.orderNo === orderNo); if (order) openAdminOrder(order) }} /></AdminPrototypeShell>} />
        <Route path="/admin-recharges" element={<AdminPrototypeShell dark={dark} page="recharges" scenario={adminRechargesScenario} scenarioOptions={adminRechargesScenarioOptions} onThemeChange={setDark} onScenarioChange={setAdminRechargesScenario} onNavigate={navigateAdmin} onBoss={() => navigate('/login')} onLogout={() => { setAdminLoginScenario('default'); navigate('/admin-login') }}><AdminRechargesPage key={`${adminRechargesScenario}-${adminRechargeWalletScope ?? 'all'}`} scenario={adminRechargesScenario} initialWalletId={adminRechargeWalletScope} onScenarioChange={next => { if (next === 'default') setAdminRechargeWalletScope(undefined); setAdminRechargesScenario(next) }} onOpenTransactions={openAdminTransactions} /></AdminPrototypeShell>} />
        <Route path="/admin-payment-fee" element={<AdminPrototypeShell dark={dark} page="payment-fee" scenario={adminPaymentFeeScenario} scenarioOptions={adminPaymentFeeScenarioOptions} onThemeChange={setDark} onScenarioChange={setAdminPaymentFeeScenario} onNavigate={navigateAdmin} onBoss={() => navigate('/login')} onLogout={() => { setAdminLoginScenario('default'); navigate('/admin-login') }}><AdminPaymentFeePage key={adminPaymentFeeScenario} scenario={adminPaymentFeeScenario} onScenarioChange={setAdminPaymentFeeScenario} /></AdminPrototypeShell>} />
        <Route path="*" element={<Navigate to="/admin-login" replace />} />
      </Routes>
      </AdminDesignShell>
    )
  }

  function changePage(page: BossPage) {
    navigate(page === 'home' ? '/home' : page === 'companions' ? '/companions' : page === 'companion-picker' ? '/companion-picker' : page === 'companion-detail' ? '/companion-detail' : page === 'catalog' ? '/catalog' : page === 'service-detail' ? '/service-detail' : page === 'checkout' ? '/checkout' : page === 'orders' ? '/orders' : page === 'order-detail' ? '/order-detail' : page === 'wallet' ? '/wallet' : page === 'gifts' ? '/gifts' : page === 'mine' ? '/mine' : page === 'messages' ? '/messages' : page === 'chat' ? '/chat' : page === 'support' ? '/support' : page === 'security' ? '/security' : page === 'profile' ? '/profile' : page === 'reservations' ? '/reservations' : page === 'reservation-detail' ? '/reservation-detail' : page === 'reservation-create' ? '/reservation-create' : page === 'coupons' ? '/coupons' : page === 'membership' ? '/membership' : page === 'points' ? '/points' : page === 'lottery' ? '/lottery' : page === 'reward-claim' ? '/reward-claim' : page === 'favorites' ? '/favorites' : page === 'checkin' ? '/checkin' : page === 'achievements' ? '/achievements' : page === 'rankings' ? '/rankings' : page === 'announcements' ? '/announcements' : page === 'announcement-detail' ? '/announcement-detail' : page === 'club-content' ? '/club-content' : page === 'club-content-detail' ? '/club-content-detail' : page === 'activities' ? '/activities' : page === 'activity-detail' ? '/activity-detail' : page === 'rights' ? '/rights' : page === 'rights-detail' ? '/rights-detail' : page === 'point-rules' ? '/point-rules' : page === 'point-rules-detail' ? '/point-rules-detail' : page === 'message-detail' ? '/message-detail' : page === 'audition' ? '/audition' : '/login')
  }

  function changeScenario(value: string) {
    if (currentPage === 'home') setHomeScenario(value as HomeScenario)
    else if (currentPage === 'companions') setCompanionScenario(value as CompanionScenario)
    else if (currentPage === 'companion-picker') setCompanionPickerScenario(value as CompanionPickerScenario)
    else if (currentPage === 'companion-detail') setCompanionDetailScenario(value as CompanionDetailScenario)
    else if (currentPage === 'catalog') setCatalogScenario(value as CatalogScenario)
    else if (currentPage === 'service-detail') setServiceDetailScenario(value as ServiceDetailScenario)
    else if (currentPage === 'checkout') setCheckoutScenario(value as CheckoutScenario)
    else if (currentPage === 'orders') setOrdersScenario(value as OrdersScenario)
    else if (currentPage === 'order-detail') setOrderDetailScenario(value as OrderDetailScenario)
    else if (currentPage === 'wallet') setWalletScenario(value as WalletScenario)
    else if (currentPage === 'gifts') setGiftsScenario(value as GiftsScenario)
    else if (currentPage === 'mine') setMineScenario(value as MineScenario)
    else if (currentPage === 'messages') setMessagesScenario(value as MessagesScenario)
    else if (currentPage === 'message-detail') {
      const nextScenario = value as MessageDetailScenario
      setMessageDetailScenario(nextScenario)
      if (nextScenario === 'wallet') setSelectedNotice(initialNotices[5])
      else if (nextScenario === 'claims') setSelectedNotice({ ...initialNotices[3], target: 'claims' })
      else if (nextScenario === 'default' || nextScenario === 'empty-content') setSelectedNotice(initialNotices[0])
    }
    else if (currentPage === 'audition') setAuditionScenario(value as AuditionScenario)
    else if (currentPage === 'chat') setChatScenario(value as ChatScenario)
    else if (currentPage === 'support') setSupportScenario(value as SupportScenario)
    else if (currentPage === 'security') setSecurityScenario(value as SecurityScenario)
    else if (currentPage === 'profile') setProfileScenario(value as ProfileScenario)
    else if (currentPage === 'reservations') setReservationsScenario(value as ReservationsScenario)
    else if (currentPage === 'reservation-detail') setReservationDetailScenario(value as ReservationDetailScenario)
    else if (currentPage === 'reservation-create') setReservationCreateScenario(value as ReservationCreateScenario)
    else if (currentPage === 'coupons') setCouponsScenario(value as CouponsScenario)
    else if (currentPage === 'membership') setMembershipScenario(value as MembershipScenario)
    else if (currentPage === 'points') setPointsScenario(value as PointsScenario)
    else if (currentPage === 'lottery') setLotteryScenario(value as LotteryScenario)
    else if (currentPage === 'reward-claim') setRewardClaimScenario(value as RewardClaimScenario)
    else if (currentPage === 'favorites') setFavoritesScenario(value as FavoritesScenario)
    else if (currentPage === 'checkin') setCheckinScenario(value as CheckinScenario)
    else if (currentPage === 'achievements') setAchievementsScenario(value as AchievementsScenario)
    else if (currentPage === 'rankings') setRankingsScenario(value as RankingsScenario)
    else if (currentPage === 'announcements') setAnnouncementsScenario(value as AnnouncementsScenario)
    else if (currentPage === 'announcement-detail') setAnnouncementDetailScenario(value as AnnouncementDetailScenario)
    else if (currentPage === 'club-content') setClubContentScenario(value as ClubContentScenario)
    else if (currentPage === 'club-content-detail') setClubContentDetailScenario(value as ClubContentDetailScenario)
    else if (currentPage === 'activities') setActivitiesScenario(value as ActivitiesScenario)
    else if (currentPage === 'activity-detail') setActivityDetailScenario(value as ActivityDetailScenario)
    else if (currentPage === 'rights') setRightsScenario(value as GuideContentScenario)
    else if (currentPage === 'rights-detail') setRightsDetailScenario(value as GuideContentDetailScenario)
    else if (currentPage === 'point-rules') setPointRulesScenario(value as GuideContentScenario)
    else if (currentPage === 'point-rules-detail') setPointRulesDetailScenario(value as GuideContentDetailScenario)
    else setLoginScenario(value as LoginScenario)
  }

  function openSupport(nextScenario: SupportScenario = 'default', origin: BossPage = 'mine', orderNo?: string) {
    setSupportScenario(nextScenario)
    setSupportOrigin(origin)
    if (nextScenario === 'order') setSupportOrderNo(orderNo || bossOrders[0].orderNo)
    navigate('/support')
  }

  function openSecurity(nextScenario: SecurityScenario = 'default', origin: BossPage = 'mine') {
    setSecurityScenario(nextScenario)
    setSecurityOrigin(origin)
    navigate('/security')
  }

  function openService(service: CatalogService) {
    setSelectedService(service)
    setServiceDetailScenario(service.consult ? 'consult' : catalogScenario === 'provider' ? 'provider' : 'default')
    navigate('/service-detail')
  }

  function openCheckout(price: number, variantName: string) {
    setCheckoutUnitPrice(price)
    setCheckoutVariantName(variantName)
    setCheckoutProviderIds(undefined)
    setCheckoutScenario(serviceDetailScenario === 'provider' ? 'provider' : 'default')
    navigate('/checkout')
  }

  function openOrders(status?: 'pending' | 'grabbed' | 'in_progress') {
    setOrdersScenario(status === 'pending' ? 'pending' : status === 'grabbed' ? 'grabbed' : status === 'in_progress' ? 'in-progress' : 'default')
    navigate('/orders')
  }

  function openOrder(order: BossOrder) {
    setSelectedOrder(order)
    setOrderDetailScenario(order.paymentUnconfirmed ? 'payment-pending' : order.aftersale ? 'aftersale' : order.gift ? 'gift' : order.evaluate ? 'review' : order.status === 'PENDING' ? 'pending' : order.status === 'GRABBED' ? 'grabbed' : order.status === 'IN_SERVICE' ? 'in-service' : order.status === 'CANCELLED' ? 'refund' : 'default')
    navigate('/order-detail')
  }

  function openReservation(reservation: Reservation) {
    setSelectedReservation(reservation)
    setReservationDetailScenario(reservation.status === 'PENDING' ? 'pending' : reservation.status === 'CONVERTED' ? 'converted' : reservation.status === 'CANCELLED' ? 'cancelled' : reservation.status === 'REJECTED' ? 'rejected' : reservation.status === 'EXPIRED' ? 'expired' : 'default')
    navigate('/reservation-detail')
  }

  return (
    <PrototypeShell
      dark={dark}
      onThemeChange={setDark}
      currentPage={currentPage}
      onPageChange={changePage}
      scenario={scenario}
      onScenarioChange={changeScenario}
      scenarioOptions={scenarioOptions}
    >
      <Routes>
        <Route path="/" element={<Navigate to="/login" replace />} />
        <Route path="/login" element={<LoginPage key={loginScenario} scenario={loginScenario} />} />
        <Route path="/home" element={<HomePage key={homeScenario} scenario={homeScenario} onLogin={() => navigate('/login')} onCompanions={() => navigate('/companions')} onCatalog={() => { setCatalogScenario('default'); navigate('/catalog') }} onOrders={openOrders} onGifts={() => { setGiftRecipient(''); setGiftsScenario('default'); navigate('/gifts') }} onCoupons={() => { setCouponsScenario('default'); navigate('/coupons') }} onPoints={() => { setPointsScenario('default'); navigate('/points') }} onCheckin={() => { setCheckinScenario('default'); navigate('/checkin') }} onRankings={() => { setRankingsScenario('default'); navigate('/rankings') }} onAnnouncement={() => { setSelectedAnnouncement(bossAnnouncements[0]); setAnnouncementOrigin('home'); setAnnouncementDetailScenario('default'); navigate('/announcement-detail') }} onActivities={() => { setActivitiesScenario('default'); navigate('/activities') }} onActivity={() => { setSelectedActivity(clubActivities[0]); setActivityOrigin('home'); setActivityDetailScenario('default'); navigate('/activity-detail') }} onRights={() => { setRightsOrigin('home'); setRightsScenario('default'); navigate('/rights') }} onSupport={() => openSupport('default', 'home')} onRetry={() => setHomeScenario('default')} />} />
        <Route path="/companions" element={<CompanionsPage key={companionScenario} scenario={companionScenario} onLogin={() => navigate('/login')} onHome={() => navigate('/home')} onDetail={() => navigate('/companion-detail')} onPlatform={() => { setCheckoutUnitPrice(68); setCheckoutVariantName('1 小时'); setCheckoutProviderIds([]); setCheckoutScenario('default'); navigate('/checkout') }} onRetry={() => setCompanionScenario('default')} />} />
        <Route path="/companion-picker" element={<CompanionPickerPage key={companionPickerScenario} scenario={companionPickerScenario} service={selectedService} fromCheckout={companionPickerOrigin === 'checkout'} onLogin={() => navigate('/login')} onBack={() => navigate(companionPickerOrigin === 'checkout' ? '/checkout' : '/service-detail')} onHome={() => navigate('/home')} onCompanions={() => { setCompanionScenario('default'); navigate('/companions') }} onOrders={() => openOrders()} onMine={() => navigate('/mine')} onDetail={() => { setCompanionDetailScenario('picker'); navigate('/companion-detail') }} onConfirm={ids => { setCheckoutProviderIds(ids); setCheckoutScenario(ids.length === 2 ? 'double' : 'provider'); navigate('/checkout') }} onPlatform={() => { setCheckoutProviderIds([]); setCheckoutScenario('default'); navigate('/checkout') }} onRetry={() => setCompanionPickerScenario('default')} />} />
        <Route path="/companion-detail" element={<CompanionDetailPage key={companionDetailScenario} scenario={companionDetailScenario} onLogin={() => navigate('/login')} onBack={() => navigate(companionDetailScenario === 'picker' ? '/companion-picker' : '/companions')} onHome={() => navigate('/home')} onCatalog={() => { setCatalogScenario('provider'); navigate('/catalog') }} onGift={() => { setGiftRecipient('小澈'); setGiftsScenario('default'); navigate('/gifts') }} onOrder={() => { setCheckoutUnitPrice(68); setCheckoutVariantName('1 小时'); setCheckoutProviderIds([1]); setCheckoutScenario('provider'); navigate('/checkout') }} onReserve={() => { setReservationCreateScenario('default'); navigate('/reservation-create') }} onRetry={() => setCompanionDetailScenario('default')} />} />
        <Route path="/catalog" element={<ServiceCatalogPage key={catalogScenario} scenario={catalogScenario} onLogin={() => navigate('/login')} onHome={() => navigate('/home')} onCompanions={() => navigate('/companions')} onOpen={openService} onRetry={() => setCatalogScenario('default')} />} />
        <Route path="/service-detail" element={<ServiceDetailPage key={`${selectedService.id}-${serviceDetailScenario}`} scenario={serviceDetailScenario} service={selectedService} onLogin={() => navigate('/login')} onBack={() => navigate('/catalog')} onHome={() => navigate('/home')} onCompanions={() => { setCompanionPickerOrigin('service'); setCompanionPickerScenario('default'); navigate('/companion-picker') }} onCheckout={openCheckout} onSupport={() => openSupport('default', 'service-detail')} onRetry={() => setServiceDetailScenario('default')} />} />
        <Route path="/checkout" element={<CheckoutPage key={`${selectedService.id}-${checkoutScenario}-${checkoutProviderIds?.join('-') ?? 'context'}`} scenario={checkoutScenario} service={selectedService} unitPrice={checkoutUnitPrice} variantName={checkoutVariantName} initialProviderIds={checkoutProviderIds} onLogin={() => navigate('/login')} onBack={() => navigate('/service-detail')} onHome={() => navigate('/home')} onCompanions={() => { setCompanionPickerOrigin('checkout'); setCompanionPickerScenario(checkoutScenario === 'double' ? 'double' : 'checkout'); navigate('/companion-picker') }} onOrders={openOrders} onSupport={recharge => openSupport(recharge ? 'recharge' : 'default', 'checkout')} onRetry={() => setCheckoutScenario('default')} />} />
        <Route path="/orders" element={<OrdersPage key={`${ordersFromDock ? 'dock' : 'context'}-${ordersScenario}`} scenario={ordersFromDock ? 'default' : ordersScenario} onLogin={() => navigate('/login')} onHome={() => navigate('/home')} onCompanions={() => navigate('/companions')} onCatalog={() => navigate('/catalog')} onGifts={() => { setGiftRecipient(''); setGiftsScenario('default'); navigate('/gifts') }} onOpen={openOrder} onRetry={() => setOrdersScenario('default')} />} />
        <Route path="/order-detail" element={<OrderDetailPage key={`${selectedOrder.id}-${orderDetailScenario}`} scenario={orderDetailScenario} order={selectedOrder} onLogin={() => navigate('/login')} onBack={openOrders} onHome={() => navigate('/home')} onCompanions={() => navigate('/companions')} onWallet={() => { setWalletScenario('refund'); navigate('/wallet') }} onGift={() => { setGiftRecipient('小澈'); setGiftsScenario('order'); navigate('/gifts') }} onSupport={orderNo => openSupport('order', 'order-detail', orderNo)} onRetry={() => setOrderDetailScenario('default')} />} />
        <Route path="/wallet" element={<WalletPage key={walletScenario} scenario={walletScenario} onLogin={() => navigate('/login')} onHome={() => navigate('/home')} onCompanions={() => navigate('/companions')} onOrders={openOrders} onSupport={() => openSupport('recharge', 'wallet')} onRetry={() => setWalletScenario('default')} />} />
        <Route path="/gifts" element={<GiftsPage key={`${giftsScenario}-${giftRecipient}`} scenario={giftsScenario} initialRecipient={giftRecipient} onLogin={() => navigate('/login')} onHome={() => navigate('/home')} onCompanions={() => navigate('/companions')} onOrders={openOrders} onGiftOrders={() => { setOrdersScenario('gifts'); navigate('/orders') }} onClaims={() => { setPointsScenario('claims'); navigate('/points') }} onSupport={recharge => openSupport(recharge ? 'recharge' : 'default', 'gifts')} onBack={() => navigate('/order-detail')} onRetry={() => setGiftsScenario('default')} />} />
        <Route path="/mine" element={<MinePage key={mineScenario} scenario={mineScenario} onLogin={() => navigate('/login')} onHome={() => navigate('/home')} onCompanions={() => navigate('/companions')} onOrders={openOrders} onWallet={() => { setWalletScenario('default'); navigate('/wallet') }} onGifts={() => { setGiftRecipient(''); setGiftsScenario('default'); navigate('/gifts') }} onCoupons={() => { setCouponsScenario('default'); navigate('/coupons') }} onMembership={() => { setMembershipScenario('default'); navigate('/membership') }} onPoints={() => { setPointsScenario('default'); navigate('/points') }} onFavorites={() => { setFavoritesScenario('default'); navigate('/favorites') }} onCheckin={() => { setCheckinScenario('default'); navigate('/checkin') }} onAchievements={() => { setAchievementsScenario('default'); navigate('/achievements') }} onAnnouncements={() => { setAnnouncementsScenario('default'); navigate('/announcements') }} onClubContent={() => { setClubContentScenario('default'); navigate('/club-content') }} onRights={() => { setRightsOrigin('mine'); setRightsScenario('default'); navigate('/rights') }} onProfile={() => { setProfileScenario('default'); navigate('/profile') }} onReservations={() => { setReservationsScenario('default'); navigate('/reservations') }} onSecurity={() => openSecurity(mineScenario === 'unbound' ? 'wechat-unbound' : 'default', 'mine')} onSupport={() => openSupport('default', 'mine')} onRetry={() => setMineScenario('default')} />} />
        <Route path="/messages" element={<MessagesPage key={messagesScenario} scenario={messagesScenario} onLogin={() => navigate('/login')} onHome={() => navigate('/home')} onCompanions={() => navigate('/companions')} onOrders={openOrders} readNoticeIds={readNoticeIds} onMarkAllRead={() => setReadNoticeIds(initialNotices.map(item => item.id))} onOpenNotice={item => { setSelectedNotice(item); setReadNoticeIds(ids => ids.includes(item.id) ? ids : [...ids, item.id]); setMessageDetailScenario('default'); navigate('/message-detail') }} onChat={() => { setChatScenario('default'); navigate('/chat') }} onSupport={() => openSupport('default', 'messages')} onAnnouncements={() => { setAnnouncementsScenario('default'); navigate('/announcements') }} onRetry={() => setMessagesScenario('default')} />} />
        <Route path="/message-detail" element={<MessageDetailPage key={`${selectedNotice.id}-${messageDetailScenario}`} scenario={messageDetailScenario} item={selectedNotice} onLogin={() => navigate('/login')} onBack={() => navigate('/messages')} onHome={() => navigate('/home')} onCompanions={() => navigate('/companions')} onOrders={() => openOrders()} onMine={() => navigate('/mine')} onOrder={() => openOrder(bossOrders[0])} onWallet={() => { setWalletScenario('default'); navigate('/wallet') }} onClaims={() => { setPointsScenario('claims'); navigate('/points') }} onRetry={() => setMessageDetailScenario('default')} />} />
        <Route path="/audition" element={<AuditionPage key={auditionScenario} scenario={auditionScenario} onHome={() => navigate('/home')} onCompanions={() => navigate('/companions')} onOrders={() => openOrders()} onMine={() => navigate('/mine')} onEntered={() => { setMineScenario('default'); navigate('/mine') }} onSupport={() => openSupport('default', 'audition')} onRetry={() => setAuditionScenario('default')} />} />
        <Route path="/chat" element={<ChatPage key={`${chatScenario}-${chatOrderNo}`} scenario={chatScenario} orderNo={chatOrderNo} onLogin={() => navigate('/login')} onBack={() => navigate('/messages')} onHome={() => navigate('/home')} onCompanions={() => navigate('/companions')} onOrders={openOrders} onRetry={() => setChatScenario('default')} />} />
        <Route path="/support" element={<SupportPage key={`${supportScenario}-${supportOrderNo}`} scenario={supportScenario} orderNo={supportOrderNo} onLogin={() => navigate('/login')} onBack={() => changePage(supportOrigin)} onHome={() => navigate('/home')} onCompanions={() => navigate('/companions')} onOrders={openOrders} onChat={withOrder => { setChatOrderNo(supportOrderNo); setChatScenario(withOrder ? 'order' : 'default'); navigate('/chat') }} onRetry={() => setSupportScenario('default')} />} />
        <Route path="/security" element={<SecurityPage key={securityScenario} scenario={securityScenario} onLogin={() => navigate('/login')} onBack={() => changePage(securityOrigin)} onHome={() => navigate('/home')} onCompanions={() => navigate('/companions')} onOrders={openOrders} onSupport={() => openSupport('default', 'security')} onLogout={() => { setMineScenario('guest'); navigate('/mine') }} onRetry={() => setSecurityScenario('default')} />} />
        <Route path="/profile" element={<ProfilePage key={profileScenario} scenario={profileScenario} onLogin={() => navigate('/login')} onBack={() => navigate('/mine')} onHome={() => navigate('/home')} onCompanions={() => navigate('/companions')} onOrders={openOrders} onSecurity={() => openSecurity('default', 'profile')} onRetry={() => setProfileScenario('default')} />} />
        <Route path="/reservations" element={<ReservationsPage key={reservationsScenario} scenario={reservationsScenario} onLogin={() => navigate('/login')} onBack={() => navigate('/mine')} onHome={() => navigate('/home')} onCompanions={() => navigate('/companions')} onOrders={openOrders} onBrowse={() => navigate('/catalog')} onOpen={openReservation} onRetry={() => setReservationsScenario('default')} />} />
        <Route path="/reservation-detail" element={<ReservationDetailPage key={`${selectedReservation.id}-${reservationDetailScenario}`} scenario={reservationDetailScenario} reservation={selectedReservation} onLogin={() => navigate('/login')} onBack={() => navigate('/reservations')} onHome={() => navigate('/home')} onCompanions={() => navigate('/companions')} onOrders={openOrders} onOrder={() => openOrder(bossOrders[2])} onWallet={() => { setWalletScenario('default'); setReservationDetailScenario('default'); navigate('/wallet') }} onRebook={() => { setReservationCreateScenario('default'); navigate('/reservation-create') }} onRetry={() => setReservationDetailScenario('default')} />} />
        <Route path="/reservation-create" element={<ReservationCreatePage key={reservationCreateScenario} scenario={reservationCreateScenario} service={selectedService} onLogin={() => navigate('/login')} onBack={() => { setCompanionDetailScenario('service'); navigate('/companion-detail') }} onHome={() => navigate('/home')} onCompanions={() => navigate('/companions')} onOrders={openOrders} onList={() => navigate('/reservations')} onBrowse={() => navigate('/catalog')} onCreated={() => { setSelectedReservation(bossReservations[1]); setReservationDetailScenario('pending'); navigate('/reservation-detail') }} onRetry={() => setReservationCreateScenario('default')} />} />
        <Route path="/coupons" element={<CouponsPage key={couponsScenario} scenario={couponsScenario} onLogin={() => navigate('/login')} onBack={() => navigate('/mine')} onHome={() => navigate('/home')} onCompanions={() => navigate('/companions')} onOrders={openOrders} onUse={() => { setCatalogScenario('coupon'); navigate('/catalog') }} onRetry={() => setCouponsScenario('default')} />} />
        <Route path="/membership" element={<MembershipPage key={membershipScenario} scenario={membershipScenario} onLogin={() => navigate('/login')} onBack={() => navigate('/mine')} onHome={() => navigate('/home')} onCompanions={() => navigate('/companions')} onOrders={openOrders} onCoupons={() => { setCouponsScenario('default'); navigate('/coupons') }} onRights={() => { setRightsOrigin('membership'); setRightsScenario('default'); navigate('/rights') }} onRetry={() => setMembershipScenario('default')} />} />
        <Route path="/points" element={<PointsPage key={pointsScenario} scenario={pointsScenario} onLogin={() => navigate('/login')} onBack={() => navigate('/mine')} onHome={() => navigate('/home')} onCompanions={() => navigate('/companions')} onOrders={() => openOrders()} onLottery={() => { setLotteryScenario('default'); navigate('/lottery') }} onClaim={() => { setRewardClaimScenario('default'); navigate('/reward-claim') }} onRules={() => { setPointRulesScenario('default'); navigate('/point-rules') }} onRetry={() => setPointsScenario('default')} />} />
        <Route path="/lottery" element={<LotteryPage key={lotteryScenario} scenario={lotteryScenario} onBack={() => navigate('/points')} onHome={() => navigate('/home')} onCompanions={() => navigate('/companions')} onOrders={() => openOrders()} onRecords={() => { setPointsScenario('claims'); navigate('/points') }} onClaim={() => { setRewardClaimScenario('default'); navigate('/reward-claim') }} onRetry={() => setLotteryScenario('default')} />} />
        <Route path="/reward-claim" element={<RewardClaimPage key={rewardClaimScenario} scenario={rewardClaimScenario} onLogin={() => navigate('/login')} onBack={() => { setPointsScenario('claims'); navigate('/points') }} onHome={() => navigate('/home')} onCompanions={() => navigate('/companions')} onOrders={() => openOrders()} onMembership={() => { setMembershipScenario('default'); navigate('/membership') }} onSupport={() => openSupport('default', 'reward-claim')} onRetry={() => setRewardClaimScenario('default')} />} />
        <Route path="/favorites" element={<FavoritesPage key={favoritesScenario} scenario={favoritesScenario} onLogin={() => navigate('/login')} onBack={() => navigate('/mine')} onHome={() => navigate('/home')} onCompanions={() => navigate('/companions')} onOrders={() => openOrders()} onOpen={openService} onBrowse={() => { setCatalogScenario('default'); navigate('/catalog') }} onRetry={() => setFavoritesScenario('default')} />} />
        <Route path="/checkin" element={<CheckinPage key={checkinScenario} scenario={checkinScenario} onLogin={() => navigate('/login')} onBack={() => navigate('/mine')} onHome={() => navigate('/home')} onCompanions={() => navigate('/companions')} onOrders={() => openOrders()} onWallet={() => { setWalletScenario('default'); navigate('/wallet') }} onRetry={() => setCheckinScenario('default')} />} />
        <Route path="/achievements" element={<AchievementsPage key={achievementsScenario} scenario={achievementsScenario} onLogin={() => navigate('/login')} onBack={() => navigate('/mine')} onHome={() => navigate('/home')} onCompanions={() => navigate('/companions')} onOrders={() => openOrders()} onMembership={() => { setMembershipScenario('default'); navigate('/membership') }} onRetry={() => setAchievementsScenario('default')} />} />
        <Route path="/rankings" element={<RankingsPage key={rankingsScenario} scenario={rankingsScenario} onLogin={() => navigate('/login')} onBack={() => navigate('/home')} onHome={() => navigate('/home')} onCompanions={() => navigate('/companions')} onOrders={() => openOrders()} onMine={() => navigate('/mine')} onProvider={() => { setCompanionDetailScenario('default'); navigate('/companion-detail') }} onRetry={() => setRankingsScenario('default')} />} />
        <Route path="/announcements" element={<AnnouncementsPage key={announcementsScenario} scenario={announcementsScenario} onLogin={() => navigate('/login')} onBack={() => navigate('/mine')} onHome={() => navigate('/home')} onCompanions={() => navigate('/companions')} onOrders={() => openOrders()} onMine={() => navigate('/mine')} onOpen={item => { setSelectedAnnouncement(item); setAnnouncementOrigin('announcements'); setAnnouncementDetailScenario('default'); navigate('/announcement-detail') }} onRetry={() => setAnnouncementsScenario('default')} />} />
        <Route path="/announcement-detail" element={<AnnouncementDetailPage key={`${selectedAnnouncement.id}-${announcementDetailScenario}`} scenario={announcementDetailScenario} item={selectedAnnouncement} onLogin={() => navigate('/login')} onBack={() => navigate(announcementOrigin === 'home' ? '/home' : announcementOrigin === 'mine' ? '/mine' : '/announcements')} onHome={() => navigate('/home')} onCompanions={() => navigate('/companions')} onOrders={() => openOrders()} onMine={() => navigate('/mine')} onRetry={() => setAnnouncementDetailScenario('default')} />} />
        <Route path="/club-content" element={<ClubContentPage key={clubContentScenario} scenario={clubContentScenario} onBack={() => navigate('/mine')} onHome={() => navigate('/home')} onCompanions={() => navigate('/companions')} onOrders={() => openOrders()} onMine={() => navigate('/mine')} onOpen={item => { setSelectedClubContent(item); setClubContentDetailScenario('default'); navigate('/club-content-detail') }} onRetry={() => setClubContentScenario('default')} />} />
        <Route path="/club-content-detail" element={<ClubContentDetailPage key={`${selectedClubContent.id}-${clubContentDetailScenario}`} scenario={clubContentDetailScenario} item={selectedClubContent} onBack={() => navigate('/club-content')} onHome={() => navigate('/home')} onCompanions={() => navigate('/companions')} onOrders={() => openOrders()} onMine={() => navigate('/mine')} onRetry={() => setClubContentDetailScenario('default')} />} />
        <Route path="/activities" element={<ActivitiesPage key={activitiesScenario} scenario={activitiesScenario} onBack={() => navigate('/home')} onHome={() => navigate('/home')} onCompanions={() => navigate('/companions')} onOrders={() => openOrders()} onMine={() => navigate('/mine')} onOpen={item => { setSelectedActivity(item); setActivityOrigin('activities'); setActivityDetailScenario('default'); navigate('/activity-detail') }} onRetry={() => setActivitiesScenario('default')} />} />
        <Route path="/activity-detail" element={<ActivityDetailPage key={`${selectedActivity.id}-${activityDetailScenario}`} scenario={activityDetailScenario} item={selectedActivity} onBack={() => navigate(activityOrigin === 'home' ? '/home' : '/activities')} onHome={() => navigate('/home')} onCompanions={() => navigate('/companions')} onOrders={() => openOrders()} onMine={() => navigate('/mine')} onRetry={() => setActivityDetailScenario('default')} />} />
        <Route path="/rights" element={<GuideContentPage key={rightsScenario} kind="RIGHTS" scenario={rightsScenario} backLabel={rightsOrigin === 'home' ? '首页' : rightsOrigin === 'membership' ? '会员' : '我的'} onBack={() => navigate(rightsOrigin === 'home' ? '/home' : rightsOrigin === 'membership' ? '/membership' : '/mine')} onHome={() => navigate('/home')} onCompanions={() => navigate('/companions')} onOrders={() => openOrders()} onMine={() => navigate('/mine')} onOpen={item => { setSelectedRightsContent(item); setRightsDetailScenario('default'); navigate('/rights-detail') }} onRetry={() => setRightsScenario('default')} />} />
        <Route path="/rights-detail" element={<GuideContentDetailPage key={`${selectedRightsContent.id}-${rightsDetailScenario}`} kind="RIGHTS" scenario={rightsDetailScenario} item={selectedRightsContent} onBack={() => navigate('/rights')} onHome={() => navigate('/home')} onCompanions={() => navigate('/companions')} onOrders={() => openOrders()} onMine={() => navigate('/mine')} onRetry={() => setRightsDetailScenario('default')} />} />
        <Route path="/point-rules" element={<GuideContentPage key={pointRulesScenario} kind="POINT_RULES" scenario={pointRulesScenario} backLabel="积分" onBack={() => navigate('/points')} onHome={() => navigate('/home')} onCompanions={() => navigate('/companions')} onOrders={() => openOrders()} onMine={() => navigate('/mine')} onOpen={item => { setSelectedPointRuleContent(item); setPointRulesDetailScenario('default'); navigate('/point-rules-detail') }} onRetry={() => setPointRulesScenario('default')} />} />
        <Route path="/point-rules-detail" element={<GuideContentDetailPage key={`${selectedPointRuleContent.id}-${pointRulesDetailScenario}`} kind="POINT_RULES" scenario={pointRulesDetailScenario} item={selectedPointRuleContent} onBack={() => navigate('/point-rules')} onHome={() => navigate('/home')} onCompanions={() => navigate('/companions')} onOrders={() => openOrders()} onMine={() => navigate('/mine')} onRetry={() => setPointRulesDetailScenario('default')} />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    </PrototypeShell>
  )
}
