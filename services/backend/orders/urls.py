from django.urls import path

from .reservation_views import (
    ReservationCancelView,
    ReservationConfirmView,
    ReservationConvertView,
    ReservationDetailView,
    ReservationListCreateView,
    ReservationRejectView,
)
from .views import (
    AssignOrderView,
    CancelOrderView,
    CompleteOrderView,
    CreateOrderView,
    EvaluateOrderView,
    FavoriteListView,
    FavoriteToggleView,
    GameCategoryListView,
    GiftContextView,
    GrabOrderView,
    MyEvaluationListView,
    OrderListView,
    OrderDetailView,
    OrderQuoteView,
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

from .applications import ApplicationActionView, SelectApplicationView, TeammateListView

from .traffic_views import TrafficStatusView, NetworkReportView

from .aftersale import AfterSaleView

urlpatterns = [
    path('orders/<int:order_id>/aftersale/', AfterSaleView.as_view()),
    path('traffic-status/', TrafficStatusView.as_view()),
    path('network-report/', NetworkReportView.as_view()),
    path('orders/<int:order_id>/applications/<int:application_id>/action/', ApplicationActionView.as_view()),
    path('orders/<int:order_id>/applications/<int:application_id>/select/', SelectApplicationView.as_view()),
    path('orders/<int:order_id>/teammates/', TeammateListView.as_view()),
    path('gameplays/', GameplayListView.as_view()),
    path('game-categories/', GameCategoryListView.as_view()),
    path('services/', ServiceListView.as_view()),
    path('services/<int:service_id>/', ServiceDetailView.as_view()),
    path('services/<int:service_id>/evaluations/', ServiceEvaluationListView.as_view()),
    path('favorites/', FavoriteListView.as_view()),
    path('favorites/toggle/', FavoriteToggleView.as_view()),
    path('rankings/', RankingView.as_view()),
    path('create/', CreateOrderView.as_view()),
    path('quote/', OrderQuoteView.as_view()),
    path('gifts/context/', GiftContextView.as_view()),
    path('gifts/send/', TipOrderView.as_view()),
    path('orders/', OrderListView.as_view(), name='order-list'),
    path('orders/<int:order_id>/', OrderDetailView.as_view(), name='order-detail'),
    path('orders/<int:order_id>/grab/', GrabOrderView.as_view()),
    path('orders/<int:order_id>/start/', StartOrderServiceView.as_view()),
    path('orders/<int:order_id>/start-service/', StartOrderServiceView.as_view()),
    path('orders/<int:order_id>/complete/', CompleteOrderView.as_view()),
    path('orders/<int:order_id>/tip/', TipOrderView.as_view()),
    path('orders/<int:order_id>/cancel/', CancelOrderView.as_view()),
    path('orders/<int:order_id>/reject/', RejectOrderView.as_view()),
    path('orders/<int:order_id>/refund/', RefundOrderView.as_view()),
    path('orders/<int:order_id>/assign/', AssignOrderView.as_view()),
    # 预约（ORD-1/2）：与订单共用前缀但独立 ID 空间，路径上用 reservations 区分
    path('reservations/', ReservationListCreateView.as_view(), name='reservation-list'),
    path('reservations/<int:reservation_id>/', ReservationDetailView.as_view()),
    path('reservations/<int:reservation_id>/confirm/', ReservationConfirmView.as_view()),
    path('reservations/<int:reservation_id>/reject/', ReservationRejectView.as_view()),
    path('reservations/<int:reservation_id>/cancel/', ReservationCancelView.as_view()),
    path('reservations/<int:reservation_id>/convert/', ReservationConvertView.as_view()),
    path('evaluate/', EvaluateOrderView.as_view()),
    path('evaluations/mine/', MyEvaluationListView.as_view()),
    path('evaluations/<int:evaluation_id>/reply/', ReplyEvaluationView.as_view()),
    path('stats/', OrderStatsView.as_view()),
]
