from django.urls import path

from .reservation_views import (
    ReservationAccountFieldsView,
    ReservationCancelView,
    ReservationConfirmView,
    ReservationConvertView,
    ReservationQuoteView,
    ReservationDetailView,
    ReservationListCreateView,
    ReservationRejectView,
    ReservationReplaceProviderView,
    ReservationReplacementCandidatesView,
)
from .views import (
    AssignOrderView,
    CancelOrderView,
    CompleteOrderView,
    CreateOrderView,
    ResolveOrderRequestView,
    EvaluateOrderView,
    FavoriteListView,
    FavoriteRemoveView,
    FavoriteToggleView,
    GameCategoryListView,
    GiftContextView,
    GrabOrderView,
    MyEvaluationListView,
    OrderListView,
    OrderDetailView,
    OrderQuoteView,
    OrderDiscountOptionsPreviewView,
    OrderStatsView,
    RankingView,
    RefundOrderView,
    RejectOrderView,
    ReplyEvaluationView,
    ServiceDetailView,
    ServiceEvaluationListView,
    ServiceListView,
    StartOrderServiceView,
    TipOrderView,
)

from .gameplay_views import GameplayListView
from .gift_receipts import GiftReceiptView
from .continuations import ContinuationClaimView

from .applications import ApplicationRecordingSubmitView, ApplicationActionView, MyApplicationDetailView, MyApplicationListView, ProviderApplicationHistoryView, SelectApplicationView, TeammateListView

from .traffic_views import TrafficStatusView, NetworkReportView

from .aftersale import AfterSaleView
from .negotiation_proposals import UserNegotiationProposalView
from .participation_entry import TransferParticipationStartView, TransferParticipationCompletionEvidenceView
from .segment_reports import SegmentReportActionView
from .rooms import OrderRoomView
from .room_objections import RoomObjectionView
from .room_messages import OrderRoomMessageView
from .room_message_images import OrderRoomMessageImageView
from .settlement_breakdown import SettlementBreakdownView
from .room_completion_review import RoomCompletionRequestView
from .room_boss_confirmation import RoomBossConfirmView
from .addon_offers import OrderAddonOffersView, OrderAddonQuoteView
from .addon_intents import OrderAddonIntentView, OrderAddonPaymentView
from .addon_refund import (
    UserAddonRefundProposalListView,
    UserAddonRefundProposalView,
    UserOrderAddonRefundProposalListView,
)
from .addon_handover import UserAddonHandoverListView, UserAddonHandoverDecisionView
from .addon_completion import UserAddonCompletionView
from .grab_team_flow import TeamInviteView, TeamInvitationsView, TeamActionView

urlpatterns = [
    path('orders/<int:order_id>/continuation/', ContinuationClaimView.as_view()),
    path('orders/<int:order_id>/grab-team/', TeamInviteView.as_view()),
    path('grab-team/invitations/', TeamInvitationsView.as_view()),
    path('grab-team/invitations/<int:team_id>/<str:action>/', TeamActionView.as_view()),
    path('orders/<int:order_id>/addons/', OrderAddonOffersView.as_view()),
    path('orders/<int:order_id>/addons/quote/', OrderAddonQuoteView.as_view()),
    path('orders/<int:order_id>/addons/intents/', OrderAddonIntentView.as_view()),
    path('orders/<int:order_id>/addons/intents/<int:intent_id>/pay/', OrderAddonPaymentView.as_view()),
    path('orders/<int:order_id>/addons/refund-proposals/', UserOrderAddonRefundProposalListView.as_view()),
    path('orders/<int:order_id>/addons/payments/<int:payment_id>/refund-proposals/', UserAddonRefundProposalListView.as_view()),
    path('orders/<int:order_id>/addons/payments/<int:payment_id>/refund-proposals/<int:proposal_id>/', UserAddonRefundProposalView.as_view()),
    path('orders/<int:order_id>/addons/payments/<int:payment_id>/handovers/', UserAddonHandoverListView.as_view()),
    path('orders/<int:order_id>/addons/payments/<int:payment_id>/completion/', UserAddonCompletionView.as_view()),
    path('orders/<int:order_id>/addons/payments/<int:payment_id>/completion/<int:completion_id>/decision/', UserAddonCompletionView.as_view()),
    path('orders/<int:order_id>/addons/payments/<int:payment_id>/handovers/<int:handover_id>/decision/', UserAddonHandoverDecisionView.as_view()),
    path('orders/<int:order_id>/aftersale/', AfterSaleView.as_view()),
    path('orders/<int:order_id>/room/', OrderRoomView.as_view()),
    path('orders/<int:order_id>/room/objections/', RoomObjectionView.as_view()),
    path('orders/<int:order_id>/room/messages/', OrderRoomMessageView.as_view()),
    path('orders/<int:order_id>/room/messages/image/', OrderRoomMessageImageView.as_view()),
    path('orders/<int:order_id>/room/completion-requests/', RoomCompletionRequestView.as_view()),
    path('orders/<int:order_id>/room/completion-requests/<int:completion_id>/confirm/', RoomBossConfirmView.as_view()),
    path('orders/<int:order_id>/negotiation-proposal/', UserNegotiationProposalView.as_view()),
    path('traffic-status/', TrafficStatusView.as_view()),
    path('network-report/', NetworkReportView.as_view()),
    path('applications/mine/', ProviderApplicationHistoryView.as_view()),
    path('orders/<int:order_id>/applications/recording/', ApplicationRecordingSubmitView.as_view()),
    path('orders/<int:order_id>/applications/<int:application_id>/recording/', ApplicationRecordingSubmitView.as_view()),
    path('orders/<int:order_id>/applications/<int:application_id>/action/', ApplicationActionView.as_view()),
    path('orders/<int:order_id>/applications/<int:application_id>/select/', SelectApplicationView.as_view()),
    path('orders/<int:order_id>/applications/<int:application_id>/mine/', MyApplicationDetailView.as_view()),
    path('orders/<int:order_id>/applications/mine/', MyApplicationListView.as_view()),
    path('orders/<int:order_id>/teammates/', TeammateListView.as_view()),
    path('gameplays/', GameplayListView.as_view()),
    path('game-categories/', GameCategoryListView.as_view()),
    path('services/', ServiceListView.as_view()),
    path('services/<int:service_id>/', ServiceDetailView.as_view()),
    path('services/<int:service_id>/evaluations/', ServiceEvaluationListView.as_view()),
    path('favorites/', FavoriteListView.as_view()),
    path('favorites/toggle/', FavoriteToggleView.as_view()),
    path('favorites/<int:service_id>/', FavoriteRemoveView.as_view()),
    path('rankings/', RankingView.as_view()),
    path('create/', CreateOrderView.as_view()),
    path('create/resolve/', ResolveOrderRequestView.as_view()),
    path('quote/', OrderQuoteView.as_view()),
    path('discount-options-preview/', OrderDiscountOptionsPreviewView.as_view()),
    path('gifts/context/', GiftContextView.as_view()),
    path('gifts/receipt/', GiftReceiptView.as_view()),
    path('gifts/send/', TipOrderView.as_view()),
    path('orders/', OrderListView.as_view(), name='order-list'),
    path('orders/<int:order_id>/settlement-breakdown/', SettlementBreakdownView.as_view(),
         name='order-settlement-breakdown'),
    path('orders/<int:order_id>/', OrderDetailView.as_view(), name='order-detail'),
    path('orders/<int:order_id>/grab/', GrabOrderView.as_view()),
    path('orders/<int:order_id>/start/', StartOrderServiceView.as_view()),
    path('orders/<int:order_id>/participation-start/', TransferParticipationStartView.as_view()),
    path('orders/<int:order_id>/participation-evidence/', TransferParticipationCompletionEvidenceView.as_view()),
    path('orders/<int:order_id>/segment-report/', SegmentReportActionView.as_view()),
    path('orders/<int:order_id>/start-service/', StartOrderServiceView.as_view()),
    path('orders/<int:order_id>/complete/', CompleteOrderView.as_view()),
    path('orders/<int:order_id>/tip/', TipOrderView.as_view()),
    path('orders/<int:order_id>/cancel/', CancelOrderView.as_view()),
    path('orders/<int:order_id>/reject/', RejectOrderView.as_view()),
    path('orders/<int:order_id>/refund/', RefundOrderView.as_view()),
    path('orders/<int:order_id>/assign/', AssignOrderView.as_view()),
    # 预约（ORD-1/2）：与订单共用前缀但独立 ID 空间，路径上用 reservations 区分
    path('reservations/', ReservationListCreateView.as_view(), name='reservation-list'),
    path('reservations/account-fields/<int:service_id>/', ReservationAccountFieldsView.as_view()),
    path('reservations/<int:reservation_id>/', ReservationDetailView.as_view()),
    path('reservations/<int:reservation_id>/confirm/', ReservationConfirmView.as_view()),
    path('reservations/<int:reservation_id>/reject/', ReservationRejectView.as_view()),
    path('reservations/<int:reservation_id>/replace-provider/', ReservationReplaceProviderView.as_view()),
    path('reservations/<int:reservation_id>/replacement-candidates/', ReservationReplacementCandidatesView.as_view()),
    path('reservations/<int:reservation_id>/cancel/', ReservationCancelView.as_view()),
    path('reservations/<int:reservation_id>/convert/', ReservationConvertView.as_view()),
    path('reservations/<int:reservation_id>/quote/', ReservationQuoteView.as_view()),
    path('evaluate/', EvaluateOrderView.as_view()),
    path('evaluations/mine/', MyEvaluationListView.as_view()),
    path('evaluations/<int:evaluation_id>/reply/', ReplyEvaluationView.as_view()),
    path('stats/', OrderStatsView.as_view()),
]
